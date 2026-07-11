import time
import random
import logging
import threading
from contextvars import ContextVar
from openai import OpenAI, RateLimitError
from ..config import settings

import re

logger = logging.getLogger("writing_system.llm")

# Per-task API key, set by coordinator at task start.
# Falls back to settings.LLM_API_KEY if not set.
_api_key_ctx: ContextVar[str] = ContextVar('llm_api_key', default='')

# Per-task token counter (cumulative)
_token_count_ctx: ContextVar[int] = ContextVar('llm_token_count', default=0)

# CJK character range for rough token estimation
_CJK_RE = re.compile(r'[一-鿿㐀-䶿豈-﫿]')


def estimate_tokens(text: str) -> int:
    """Rough token count: CJK chars ~1.5 tokens, others ~0.75 tokens.

    Not a replacement for a real tokenizer, but close enough for budget
    monitoring and cost awareness.  Error margin approx +-15% vs tiktoken.
    """
    if not text:
        return 0
    cjk = len(_CJK_RE.findall(text))
    other = len(text) - cjk
    return int(cjk * 1.5 + other * 0.75)


def estimate_messages_tokens(messages: list[dict]) -> int:
    """Estimate total tokens for a messages array (system + user + assistant)."""
    total = 0
    for m in messages:
        content = m.get("content", "")
        if isinstance(content, str):
            total += estimate_tokens(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and "text" in part:
                    total += estimate_tokens(part["text"])
    return total + 4  # message framing overhead


def get_cumulative_tokens() -> int:
    """Return total estimated tokens consumed in this task context."""
    return _token_count_ctx.get()


def reset_token_counter() -> None:
    """Reset the per-task token counter (called at task start)."""
    _token_count_ctx.set(0)


def set_api_key(key: str) -> None:
    """Set the API key for the current task context."""
    _api_key_ctx.set(key)


def get_api_key() -> str:
    """Get the API key for the current task context (no server fallback)."""
    return _api_key_ctx.get()


# ============================================================
# 本地令牌桶 —— 客户端侧预防性限流，避免触发服务端 429
# ============================================================
class TokenBucket:
    """线程安全的令牌桶，用于限制 LLM API 调用频率。"""

    def __init__(self, rate: int = 55, burst: int = 10):
        self.rate = rate          # 每分钟令牌数（留 5 次余量给突发）
        self.burst = burst        # 突发容量
        self.tokens = float(burst)
        self.last_refill = time.monotonic()
        self.lock = threading.Lock()

    def acquire(self, timeout: float = 30.0) -> bool:
        """获取一个令牌，超时返回 False。"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self.lock:
                now = time.monotonic()
                elapsed = now - self.last_refill
                self.tokens = min(self.burst, self.tokens + elapsed * self.rate / 60.0)
                self.last_refill = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return True
            time.sleep(0.1)
        return False


# 全局令牌桶（所有 LLMClient 实例共享）
_token_bucket = TokenBucket(rate=settings.TOKEN_BUCKET_RATE, burst=settings.TOKEN_BUCKET_BURST)


class LLMClient:
    """OpenAI 兼容 LLM 客户端封装。默认连接 DeepSeek V4 Pro。

    支持 per-task API key：通过 contextvars 在 celery 任务入口 set 后，
    所有下游调用自动使用该 key，无需修改 agent 代码。
    """

    def __init__(self):
        self._base_url = settings.LLM_BASE_URL
        self._model = settings.LLM_MODEL
        # Cache: api_key -> OpenAI client (reuse same key's client)
        self._clients: dict[str, OpenAI] = {}
        logger.info(f"LLM 客户端初始化: model={self._model}, base_url={self._base_url}")

    def _get_client(self) -> OpenAI:
        """Get or create OpenAI client for the current task's API key."""
        key = get_api_key()
        if not key:
            raise ValueError(
                "未配置 API Key。请在设置面板中输入 DeepSeek API Key。"
            )
        if key not in self._clients:
            self._clients[key] = OpenAI(api_key=key, base_url=self._base_url,
                                        timeout=300.0, max_retries=0)
        return self._clients[key]

    def chat_completion(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 2000,
        max_retries: int = 3,
        json_mode: bool = False,
        top_p: float | None = None,
        prompt_name: str = "",
    ) -> str:
        """调用 LLM 完成对话，失败时自动重试。对 429 做 Retry-After 退避。

        Args:
            prompt_name: optional prompt registry key for version/cost tracking
        """
        last_error = None
        extra = {}
        if json_mode:
            extra["response_format"] = {"type": "json_object"}

        # --- token estimation ---
        est_input = estimate_messages_tokens(messages)
        est_total = est_input + max_tokens
        from .prompt_templates import get_prompt_version
        pv = get_prompt_version(prompt_name) if prompt_name else "-"
        logger.info(
            f"LLM [{prompt_name or '-'} v{pv}]: "
            f"est_input={est_input}tk, est_total<={est_total}tk, "
            f"cumulative={get_cumulative_tokens()}tk, "
            f"temp={temperature}, json={json_mode}"
        )

        client = self._get_client()

        for attempt in range(max_retries + 1):
            # 令牌桶限流 —— 首次尝试也需获取令牌
            if not _token_bucket.acquire(timeout=60.0):
                logger.warning("令牌桶获取超时(60s)，仍然尝试调用 API")

            try:
                t0 = time.time()
                create_kwargs = dict(
                    model=self._model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    extra_body={"thinking": {"type": "disabled"}},
                    **extra,
                )
                if top_p is not None:
                    create_kwargs["top_p"] = top_p
                resp = client.chat.completions.create(**create_kwargs)
                t_api = time.time() - t0
                choice = resp.choices[0]
                msg = choice.message
                content = msg.content

                actual_in = getattr(resp, 'usage', None) and resp.usage.prompt_tokens
                actual_out = getattr(resp, 'usage', None) and resp.usage.completion_tokens
                if actual_in and actual_out:
                    cumulative = _token_count_ctx.get() + actual_in + actual_out
                    _token_count_ctx.set(cumulative)
                logger.info(f"LLM response: {t_api:.1f}s, finish={choice.finish_reason}, "
                           f"tokens_in={actual_in or '?'}, tokens_out={actual_out or '?'}, "
                           f"cumulative={_token_count_ctx.get()}")

                if not content:
                    raw = resp.model_dump_json() if hasattr(resp, "model_dump_json") else str(resp)
                    raise RuntimeError(
                        f"LLM 返回空内容。finish_reason: {choice.finish_reason}。"
                        f"可能原因: 推理模型的思考过程消耗了所有 token。"
                        f"原始响应: {raw[:800]}"
                    )

                return content.strip()

            except RateLimitError as e:
                last_error = e
                retry_after = _parse_retry_after(e)
                if attempt < max_retries:
                    jitter = random.uniform(0, 2)
                    wait = retry_after + jitter
                    logger.warning(f"API 限流 (429)。Retry-After={retry_after}s，"
                                 f"等待 {wait:.1f}s 后重试 ({attempt + 1}/{max_retries + 1})...")
                    time.sleep(wait)
                else:
                    logger.error(f"API 限流重试耗尽 ({max_retries + 1} 次): {e}", exc_info=True)

            except Exception as e:
                last_error = e
                if attempt < max_retries:
                    wait = (2 ** attempt) + random.uniform(0, 1)
                    logger.warning(f"LLM 调用失败 (尝试 {attempt + 1}/{max_retries + 1}): {e}。"
                                 f"{wait:.1f}s 后重试...")
                    time.sleep(wait)

        logger.error(f"LLM 调用最终失败（已重试 {max_retries} 次）: {last_error}")
        raise RuntimeError(
            f"LLM 调用失败（已重试 {max_retries} 次）。"
            f"模型: {self._model}, 错误: {last_error}"
        )

    def chat_completion_stream(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 2000,
        top_p: float | None = None,
    ):
        """流式调用 LLM，逐 token 返回生成器。"""
        t0 = time.time()
        token_count = 0
        client = self._get_client()

        # 令牌桶限流
        if not _token_bucket.acquire(timeout=60.0):
            logger.warning("流式调用令牌桶获取超时，仍然尝试调用")

        try:
            create_kwargs = dict(
                model=self._model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                extra_body={"thinking": {"type": "disabled"}},
                stream=True,
            )
            if top_p is not None:
                create_kwargs["top_p"] = top_p
            stream = client.chat.completions.create(**create_kwargs)
            for chunk in stream:
                if chunk.choices and chunk.choices[0].delta:
                    token = chunk.choices[0].delta.content
                    if token:
                        token_count += 1
                        yield token
            logger.info(f"LLM 流式完成: {time.time() - t0:.1f}s, {token_count} chunks")
        except RateLimitError as e:
            retry_after = _parse_retry_after(e)
            logger.error(f"LLM 流式限流 (429): Retry-After={retry_after}s")
            raise RuntimeError(
                f"LLM 流式调用被限流。请在 {retry_after:.0f}s 后重试。模型: {self._model}"
            )
        except Exception as e:
            logger.error(f"LLM 流式失败 ({time.time() - t0:.1f}s, {token_count} chunks): {e}", exc_info=True)
            raise RuntimeError(
                f"LLM 流式调用失败。模型: {self._model}, 错误: {e}"
            )

    @property
    def model_name(self) -> str:
        return self._model


def _parse_retry_after(e: RateLimitError) -> float:
    """从 429 响应中解析 Retry-After 头，默认 5 秒。"""
    try:
        header = e.response.headers.get("Retry-After", "5")
        return float(header)
    except (ValueError, AttributeError):
        return 5.0


# 全局单例
_llm_client: LLMClient | None = None


def get_llm_client() -> LLMClient:
    global _llm_client
    if _llm_client is None:
        _llm_client = LLMClient()
    return _llm_client
