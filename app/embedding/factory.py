import logging

from .base import EmbeddingProvider
from ..config import settings


logger = logging.getLogger("writing_system.embedding")


def preflight_embedding_backend() -> tuple[bool, str]:
    """Cheap reachability probe for the configured embedding backend.

    The pipeline only touches embeddings inside ``_phase_writing``, which runs
    *after* ``character_arcs`` and ``world_state`` have already spent real LLM
    tokens.  When the backend is down, the resulting ``RuntimeError`` matches
    ``writing_task``'s ``autoretry_for`` and the whole task replays — planning
    calls included.  A real incident on 2026-07-26 burned 26,058 tokens over
    five such replays before the sixth attempt succeeded: ~40% of that task's
    total spend, none of it useful.

    Probing here costs nothing — one HTTP GET for Ollama, no model load — and
    leaves retry semantics intact.  If the backend recovers inside the retry
    backoff window the task still succeeds; it just stops paying for the
    planning phases on every failed attempt.

    Returns ``(ok, reason)``.  Never raises.
    """
    provider = (settings.EMBEDDING_PROVIDER or "").strip().lower()

    if provider == "sentence_transformers":
        return True, ""  # in-process, nothing to probe
    if provider == "openai":
        return True, ""  # remote API; probing would cost a billable call

    if provider == "ollama":
        import requests

        base_url = (settings.OLLAMA_BASE_URL or "").rstrip("/")
        try:
            response = requests.get(f"{base_url}/api/tags", timeout=5)
            response.raise_for_status()
        except Exception as error:
            return False, (
                f"Ollama 不可达 ({base_url}): {type(error).__name__}. "
                "确认服务在跑，且 OLLAMA_BASE_URL 用的是 127.0.0.1 而非 localhost。"
            )
        wanted = (settings.EMBEDDING_MODEL or "bge-m3:latest").split(":")[0]
        try:
            names = [
                str(item.get("name", ""))
                for item in (response.json().get("models") or [])
            ]
        except Exception:
            return True, ""  # reachable; tag parsing is not worth failing on
        if names and not any(name.split(":")[0] == wanted for name in names):
            return False, (
                f"Ollama 可达但未拉取模型 '{settings.EMBEDDING_MODEL}'"
                f"（现有: {', '.join(names[:5])}）"
            )
        return True, ""

    return False, f"未知 EMBEDDING_PROVIDER={provider!r}"


def get_embedding_provider() -> EmbeddingProvider:
    """根据环境变量 EMBEDDING_PROVIDER 创建对应的 embedding 提供商。

    支持的值:
        - 'sentence_transformers' (默认): 使用 BGE-M3 本地模型
        - 'openai': 使用 OpenAI text-embedding-3-small

    扩展方式:
        在此函数中添加新的 elif 分支即可，例如未来可支持:
        - 'flag_embedding': 使用 FlagEmbedding 加载 BGE-M3
        - 'ollama': 使用 Ollama 本地 embedding 服务
    """
    provider_name = settings.EMBEDDING_PROVIDER.lower()

    if provider_name == "sentence_transformers":
        from .sentence_transformer_provider import SentenceTransformerProvider
        return SentenceTransformerProvider(model_name=settings.EMBEDDING_MODEL)

    elif provider_name == "openai":
        from .openai_provider import OpenAIEmbeddingProvider
        return OpenAIEmbeddingProvider(
            model_name=settings.EMBEDDING_MODEL or "text-embedding-3-small",
            api_key=settings.OPENAI_API_KEY,
            base_url=settings.OPENAI_BASE_URL,
        )

    elif provider_name == "ollama":
        from .ollama_provider import OllamaEmbeddingProvider
        return OllamaEmbeddingProvider(
            model_name=settings.EMBEDDING_MODEL or "bge-m3:latest",
            base_url=settings.OLLAMA_BASE_URL,
        )

    # 预留扩展点
    # elif provider_name == "flag_embedding":
    #     from .flag_embedding_provider import FlagEmbeddingProvider
    #     return FlagEmbeddingProvider(model_name=settings.EMBEDDING_MODEL)

    else:
        raise ValueError(
            f"不支持的 embedding 提供商: '{provider_name}'。"
            f"可选值: 'sentence_transformers', 'openai', 'ollama'"
        )
