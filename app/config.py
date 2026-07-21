import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    # --- LLM ---
    LLM_API_KEY: str = os.getenv("LLM_API_KEY", "")
    LLM_BASE_URL: str = os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1")
    LLM_MODEL: str = os.getenv("LLM_MODEL", "deepseek-v4-pro")

    # --- Embedding ---
    EMBEDDING_PROVIDER: str = os.getenv("EMBEDDING_PROVIDER", "sentence_transformers")
    EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3")
    OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    _VALID_PROVIDERS = {"sentence_transformers", "openai", "ollama"}
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    OPENAI_BASE_URL: str = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")

    # --- Redis ---
    REDIS_BROKER_URL: str = os.getenv("REDIS_BROKER_URL", "redis://localhost:6379/0")
    REDIS_BACKEND_URL: str = os.getenv("REDIS_BACKEND_URL", "redis://localhost:6379/1")

    # --- World State (v0.6.0) ---
    ENABLE_WORLD_STATE: bool = os.getenv("ENABLE_WORLD_STATE", "true").lower() == "true"
    WORLD_STATE_VERIFY_MODE: str = os.getenv("WORLD_STATE_VERIFY_MODE", "both")

    # --- Storage paths ---
    CHROMA_DATA_PATH: str = os.getenv("CHROMA_DATA_PATH", "./chroma_data")
    CHARACTER_DB_PATH: str = os.getenv("CHARACTER_DB_PATH", "./characters.db")
    TASK_DB_PATH: str = os.getenv("TASK_DB_PATH", "./tasks.db")

    # --- Long-form writing ---
    DEFAULT_TARGET_WORDS_PER_SECTION: int = int(os.getenv("DEFAULT_TARGET_WORDS", "10000"))
    SUBSECTION_TARGET_WORDS: int = int(os.getenv("SUBSECTION_TARGET_WORDS", "2000"))
    CHUNK_SIZE: int = int(os.getenv("CHUNK_SIZE", "500"))
    CHUNK_OVERLAP: int = int(os.getenv("CHUNK_OVERLAP", "100"))
    ENABLE_RAG: bool = os.getenv("ENABLE_RAG", "true").lower() in ("true", "1", "yes")
    ENABLE_STYLE_BEHAVIOR: bool = os.getenv("ENABLE_STYLE_BEHAVIOR", "true").lower() in ("true", "1", "yes")
    RAG_TOP_K: int = int(os.getenv("RAG_TOP_K", "5"))
    # 0 keeps legacy top-k behavior. Larger values only expand the logged
    # coarse candidate set; Writer still receives exactly RAG_TOP_K items.
    RAG_TRACE_CANDIDATE_K: int = int(os.getenv("RAG_TRACE_CANDIDATE_K", "0"))
    # Phase 3 batch 1 is trace-only. Even when enabled, Writer continues to
    # consume the legacy RAG_TOP_K result.
    RAG_PHASE3_SHADOW: bool = os.getenv("RAG_PHASE3_SHADOW", "false").lower() in ("true", "1", "yes")
    RAG_PHASE3_MAX_QUERIES: int = int(os.getenv("RAG_PHASE3_MAX_QUERIES", "4"))
    RAG_PHASE3_CANDIDATE_K: int = int(os.getenv("RAG_PHASE3_CANDIDATE_K", "12"))
    RAG_PHASE3_MIN_SCORE: float = float(os.getenv("RAG_PHASE3_MIN_SCORE", "0.35"))

    # --- Writer agent tuning ---
    WRITER_REVIEW_TRIGGER_SUBS: int = int(os.getenv("WRITER_REVIEW_TRIGGER_SUBS", "3"))
    WRITER_REVIEW_TRIGGER_CHARS: int = int(os.getenv("WRITER_REVIEW_TRIGGER_CHARS", "8000"))
    WRITER_INCREMENTAL_SECTION_REVIEW_RAW: str = os.getenv(
        "WRITER_INCREMENTAL_SECTION_REVIEW", "false"
    ).strip().lower()
    WRITER_INCREMENTAL_SECTION_REVIEW: bool = (
        WRITER_INCREMENTAL_SECTION_REVIEW_RAW in {"true", "1", "yes"}
    )
    WRITER_EXPAND_THRESHOLD: float = float(os.getenv("WRITER_EXPAND_THRESHOLD", "0.7"))
    WRITER_ACCEPT_THRESHOLD: float = float(os.getenv("WRITER_ACCEPT_THRESHOLD", "0.6"))
    WRITER_MAX_EXPAND_ATTEMPTS: int = int(os.getenv("WRITER_MAX_EXPAND_ATTEMPTS", "2"))
    WRITER_MAX_TOKENS_FLOOR: int = int(os.getenv("WRITER_MAX_TOKENS_FLOOR", "2048"))
    WRITER_MAX_TOKENS_CEIL: int = int(os.getenv("WRITER_MAX_TOKENS_CEIL", "16384"))
    WRITER_AWAIT_QUEUE_TIMEOUT: int = int(os.getenv("WRITER_AWAIT_QUEUE_TIMEOUT", "600"))
    WRITER_BOUNDARY_VALIDATOR_SHADOW: bool = os.getenv(
        "WRITER_BOUNDARY_VALIDATOR_SHADOW", "false"
    ).lower() in ("true", "1", "yes")
    WRITER_SCENE_SPEC_MODE: str = os.getenv("WRITER_SCENE_SPEC_MODE", "off").strip().lower()
    WRITER_SCENE_SPEC_CANARY_TASK_IDS: str = os.getenv(
        "WRITER_SCENE_SPEC_CANARY_TASK_IDS", ""
    )
    WRITER_MANDATORY_EVENT_MODE: str = os.getenv(
        "WRITER_MANDATORY_EVENT_MODE", "warn"
    ).strip().lower()
    WRITER_MANDATORY_EVENT_RETRY_TASK_IDS: str = os.getenv(
        "WRITER_MANDATORY_EVENT_RETRY_TASK_IDS", ""
    )
    CHARACTER_ARC_CONTRACT_VERSION: str = os.getenv(
        "CHARACTER_ARC_CONTRACT_VERSION", "v1"
    ).strip().lower()

    # --- Coordinator tuning ---
    WORLD_STATE_EXTRACT_CHARS: int = int(os.getenv("WORLD_STATE_EXTRACT_CHARS", "3000"))

    # --- LLM client tuning ---
    TOKEN_BUCKET_RATE: float = float(os.getenv("TOKEN_BUCKET_RATE", "55"))
    TOKEN_BUCKET_BURST: int = int(os.getenv("TOKEN_BUCKET_BURST", "10"))


    def validate(self) -> list[str]:
        """运行时校验关键配置，返回警告列表（不阻塞启动）。"""
        warnings = []
        if not self.LLM_API_KEY:
            warnings.append("LLM_API_KEY 未设置，LLM 调用将失败")
        if self.EMBEDDING_PROVIDER not in self._VALID_PROVIDERS:
            warnings.append(
                f"EMBEDDING_PROVIDER={self.EMBEDDING_PROVIDER} 不在有效值中: "
                f"{self._VALID_PROVIDERS}"
            )
        if self.WORLD_STATE_VERIFY_MODE not in ("rule", "llm", "both"):
            warnings.append(
                f"WORLD_STATE_VERIFY_MODE={self.WORLD_STATE_VERIFY_MODE} 无效，"
                f"应为 rule/llm/both"
            )
        if self.DEFAULT_TARGET_WORDS_PER_SECTION < 500:
            warnings.append("DEFAULT_TARGET_WORDS 过低 (<500)")
        if self.CHUNK_SIZE < 100:
            warnings.append("CHUNK_SIZE 过低 (<100)")
        if self.RAG_TOP_K < 1:
            warnings.append("RAG_TOP_K 必须 >= 1")
        if self.RAG_TRACE_CANDIDATE_K < 0:
            warnings.append("RAG_TRACE_CANDIDATE_K 必须 >= 0")
        if not 1 <= self.RAG_PHASE3_MAX_QUERIES <= 4:
            warnings.append("RAG_PHASE3_MAX_QUERIES 必须在 1..4")
        if self.RAG_PHASE3_CANDIDATE_K < self.RAG_TOP_K:
            warnings.append("RAG_PHASE3_CANDIDATE_K 不应小于 RAG_TOP_K")
        if not 0 <= self.RAG_PHASE3_MIN_SCORE <= 1:
            warnings.append("RAG_PHASE3_MIN_SCORE 必须在 0..1")
        if self.WRITER_INCREMENTAL_SECTION_REVIEW_RAW not in {
            "true", "1", "yes", "false", "0", "no", ""
        }:
            warnings.append(
                "WRITER_INCREMENTAL_SECTION_REVIEW="
                f"{self.WRITER_INCREMENTAL_SECTION_REVIEW_RAW} 无效，按 false 处理"
            )
        if self.WRITER_SCENE_SPEC_MODE not in {"off", "shadow", "canary"}:
            warnings.append(
                f"WRITER_SCENE_SPEC_MODE={self.WRITER_SCENE_SPEC_MODE} 无效，按 off 处理；"
                "应为 off/shadow/canary"
            )
        if self.WRITER_MANDATORY_EVENT_MODE not in {"off", "warn", "retry"}:
            warnings.append(
                f"WRITER_MANDATORY_EVENT_MODE={self.WRITER_MANDATORY_EVENT_MODE} "
                "无效，按 warn 处理；应为 off/warn/retry"
            )
        if self.CHARACTER_ARC_CONTRACT_VERSION not in {"v1", "v2"}:
            warnings.append(
                f"CHARACTER_ARC_CONTRACT_VERSION={self.CHARACTER_ARC_CONTRACT_VERSION} "
                "无效，按 v1 处理；应为 v1/v2"
            )
        return warnings


settings = Settings()

# ── 日志配置 ──────────────────────────────────────────────────────
import logging
import sys
from contextvars import ContextVar

# 当前任务 ID，由 coordinator 在 writing_task 入口设置
_task_id_ctx: ContextVar[str] = ContextVar("task_id", default="-")

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_FORMAT = "%(asctime)s [%(name)s] [%(task_id)s] %(levelname)s: %(message)s"
LOG_DATE_FORMAT = "%H:%M:%S"


class _TaskIdFilter(logging.Filter):
    """将 ContextVar 中的 task_id 注入每一条日志记录。"""
    def filter(self, record):
        record.task_id = _task_id_ctx.get("-")[:8] if _task_id_ctx.get("-") != "-" else "-"
        return True


handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT))
handler.addFilter(_TaskIdFilter())

for name in ("writing_system", "writing_system.coordinator", "writing_system.writer",
             "writing_system.llm", "writing_system.agents"):
    lg = logging.getLogger(name)
    lg.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
    lg.handlers = [handler]
    lg.propagate = False


def set_task_id(task_id: str) -> None:
    """设置当前协程/线程的 task_id，自动注入到所有日志中。"""
    _task_id_ctx.set(task_id)
