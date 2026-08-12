import logging
import os
import sys
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Mapping
from dotenv import load_dotenv

if os.getenv("WRITER_TESTING") != "1":
    load_dotenv()


@dataclass(frozen=True)
class CanonicalSettings:
    """Validated settings for the canonical database and rollout boundary."""

    database_url: str
    commit_mode: str
    canary_task_ids: frozenset[str]
    canary_subsection_ids: frozenset[str]

    @staticmethod
    def _parse_allowlist(raw: str) -> frozenset[str]:
        return frozenset(item.strip() for item in raw.split(",") if item.strip())

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> "CanonicalSettings":
        testing = environ.get("WRITER_TESTING", "0").strip() == "1"
        database_url = environ.get("CANONICAL_DATABASE_URL", "").strip()
        if not database_url:
            if testing:
                database_url = "sqlite+pysqlite:///:memory:"
            else:
                raise ValueError(
                    "CANONICAL_DATABASE_URL is required outside WRITER_TESTING"
                )
        if not database_url.startswith(
            ("sqlite://", "sqlite+pysqlite://", "postgresql://", "postgresql+psycopg://")
        ):
            raise ValueError(
                "CANONICAL_DATABASE_URL must be a SQLAlchemy SQLite or PostgreSQL URL"
            )

        commit_mode = environ.get("CANONICAL_COMMIT_MODE", "legacy").strip().lower()
        valid_modes = {"legacy", "canary", "internal_required"}
        if commit_mode not in valid_modes:
            raise ValueError(
                f"CANONICAL_COMMIT_MODE={commit_mode!r} is invalid; "
                f"expected one of {sorted(valid_modes)}"
            )
        if (
            commit_mode == "internal_required"
            and not testing
            and not database_url.startswith(
                ("postgresql://", "postgresql+psycopg://")
            )
        ):
            raise ValueError(
                "CANONICAL_COMMIT_MODE=internal_required requires PostgreSQL "
                "outside WRITER_TESTING"
            )

        return cls(
            database_url=database_url,
            commit_mode=commit_mode,
            canary_task_ids=cls._parse_allowlist(
                environ.get("CANONICAL_CANARY_TASK_IDS", "")
            ),
            canary_subsection_ids=cls._parse_allowlist(
                environ.get("CANONICAL_CANARY_SUBSECTION_IDS", "")
            ),
        )

    def resolve_path(
        self,
        task_id: str,
        subsection_id: str,
        *,
        pre_foundation_resume: bool = False,
    ) -> str:
        """Select one write path; canonical and legacy never dual-write."""

        if pre_foundation_resume or self.commit_mode == "legacy":
            return "legacy"
        if self.commit_mode == "internal_required":
            return "canonical"
        task_selected = bool(self.canary_task_ids) and task_id in self.canary_task_ids
        subsection_selected = (
            bool(self.canary_subsection_ids)
            and subsection_id in self.canary_subsection_ids
        )
        return "canonical" if task_selected and subsection_selected else "legacy"


_canonical_settings = CanonicalSettings.from_env(os.environ)


class Settings:
    # --- Canonical Foundation ---
    CANONICAL_DATABASE_URL: str = _canonical_settings.database_url
    CANONICAL_COMMIT_MODE: str = _canonical_settings.commit_mode
    CANONICAL_CANARY_TASK_IDS: frozenset[str] = _canonical_settings.canary_task_ids
    CANONICAL_CANARY_SUBSECTION_IDS: frozenset[str] = (
        _canonical_settings.canary_subsection_ids
    )

    # --- LLM ---
    LLM_API_KEY: str = os.getenv("LLM_API_KEY", "")
    LLM_BASE_URL: str = os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1")
    LLM_MODEL: str = os.getenv("LLM_MODEL", "deepseek-v4-pro")
    WRITER_LLM_MODEL: str = os.getenv("WRITER_LLM_MODEL", "deepseek-v4-flash")

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
    PROJECTION_MARKDOWN_ROOT: str = os.getenv(
        "PROJECTION_MARKDOWN_ROOT", "./projection_markdown"
    )

    # --- Long-form writing ---
    DEFAULT_TARGET_WORDS_PER_SECTION: int = int(os.getenv("DEFAULT_TARGET_WORDS", "10000"))
    SUBSECTION_TARGET_WORDS: int = int(os.getenv("SUBSECTION_TARGET_WORDS", "2000"))
    CHUNK_SIZE: int = int(os.getenv("CHUNK_SIZE", "500"))
    CHUNK_OVERLAP: int = int(os.getenv("CHUNK_OVERLAP", "100"))
    ENABLE_RAG: bool = os.getenv("ENABLE_RAG", "true").lower() in ("true", "1", "yes")
    ENABLE_STYLE_BEHAVIOR: bool = os.getenv("ENABLE_STYLE_BEHAVIOR", "true").lower() in ("true", "1", "yes")
    WRITER_STYLE_CONTROL_MODE_RAW: str = os.getenv(
        "WRITER_STYLE_CONTROL_MODE", "shadow"
    ).strip().lower()
    WRITER_STYLE_CONTROL_MODE: str = (
        WRITER_STYLE_CONTROL_MODE_RAW
        if WRITER_STYLE_CONTROL_MODE_RAW in {"legacy", "shadow", "policy"}
        else "shadow"
    )
    WRITER_STYLE_EVALUATION: bool = os.getenv(
        "WRITER_STYLE_EVALUATION", "true"
    ).lower() in ("true", "1", "yes")
    WRITER_ANTI_AI_EXPRESSION_MODE_RAW: str = os.getenv(
        "WRITER_ANTI_AI_EXPRESSION_MODE", "off"
    ).strip().lower()
    WRITER_ANTI_AI_EXPRESSION_MODE: str = (
        WRITER_ANTI_AI_EXPRESSION_MODE_RAW
        if WRITER_ANTI_AI_EXPRESSION_MODE_RAW in {"off", "shadow", "canary"}
        else "off"
    )
    WRITER_COMMERCIAL_HARNESS_MODE_RAW: str = os.getenv(
        "WRITER_COMMERCIAL_HARNESS_MODE", "shadow"
    ).strip().lower()
    WRITER_COMMERCIAL_HARNESS_MODE: str = (
        WRITER_COMMERCIAL_HARNESS_MODE_RAW
        if WRITER_COMMERCIAL_HARNESS_MODE_RAW in {"off", "shadow", "canary"}
        else "shadow"
    )
    WRITER_NARRATIVE_INTEGRITY_MODE_RAW: str = os.getenv(
        "WRITER_NARRATIVE_INTEGRITY_MODE", "shadow"
    ).strip().lower()
    WRITER_NARRATIVE_INTEGRITY_MODE: str = (
        WRITER_NARRATIVE_INTEGRITY_MODE_RAW
        if WRITER_NARRATIVE_INTEGRITY_MODE_RAW in {"off", "shadow", "canary"}
        else "shadow"
    )
    WRITER_WORLD_PRESSURE_MODE_RAW: str = os.getenv(
        "WRITER_WORLD_PRESSURE_MODE", "shadow"
    ).strip().lower()
    WRITER_WORLD_PRESSURE_MODE: str = (
        WRITER_WORLD_PRESSURE_MODE_RAW
        if WRITER_WORLD_PRESSURE_MODE_RAW in {"off", "shadow", "canary"}
        else "shadow"
    )
    WRITER_WORLD_PRESSURE_PRESET_RAW: str = os.getenv(
        "WRITER_WORLD_PRESSURE_PRESET", "none"
    ).strip().lower()
    WRITER_WORLD_PRESSURE_PRESET: str = (
        WRITER_WORLD_PRESSURE_PRESET_RAW
        if WRITER_WORLD_PRESSURE_PRESET_RAW in {"none", "modern_urban_realism"}
        else "none"
    )
    WRITER_WORLD_RUNTIME_MODE_RAW: str = os.getenv(
        "WRITER_WORLD_RUNTIME_MODE", "off"
    ).strip().lower()
    WRITER_WORLD_RUNTIME_MODE: str = (
        WRITER_WORLD_RUNTIME_MODE_RAW
        if WRITER_WORLD_RUNTIME_MODE_RAW in {"off", "shadow", "canary"}
        else "off"
    )
    WRITER_WORLD_RUNTIME_CANARY_TASK_IDS: str = os.getenv(
        "WRITER_WORLD_RUNTIME_CANARY_TASK_IDS", ""
    )
    WRITER_NARRATIVE_REALITY_CHECKS: bool = os.getenv(
        "WRITER_NARRATIVE_REALITY_CHECKS", "true"
    ).lower() in ("true", "1", "yes")
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
    # --- Local cross-encoder reranking (default OFF) ---
    # When false, search_with_meta is byte-identical to the legacy path: same
    # coarse query size, same items, same trace keys. This is NOT Phase 3; it
    # only reorders the single legacy query's candidate list, locally.
    RAG_RERANKER_ENABLED: bool = os.getenv(
        "RAG_RERANKER_ENABLED", "false"
    ).strip().lower() in ("true", "1", "yes")
    RAG_RERANKER_PROVIDER: str = os.getenv(
        "RAG_RERANKER_PROVIDER", "cross_encoder"
    ).strip().lower()
    RAG_RERANKER_MODEL: str = os.getenv("RAG_RERANKER_MODEL", "BAAI/bge-reranker-v2-m3")
    RAG_RERANKER_BASE_URL: str = os.getenv("RAG_RERANKER_BASE_URL", "")
    # Coarse candidates fed to the cross-encoder. Must be >= RAG_TOP_K.
    RAG_RERANKER_CANDIDATE_K: int = int(os.getenv("RAG_RERANKER_CANDIDATE_K", "20"))
    RAG_RERANKER_TIMEOUT_MS: int = int(os.getenv("RAG_RERANKER_TIMEOUT_MS", "3000"))
    # Normalized 0..1 relevance floor. 0.0 keeps every reranked candidate.
    RAG_RERANKER_MIN_SCORE: float = float(os.getenv("RAG_RERANKER_MIN_SCORE", "0.0"))
    _VALID_RERANKER_PROVIDERS = {"cross_encoder", "http"}

    # --- Writer agent tuning ---
    WRITER_REVIEW_TRIGGER_SUBS: int = int(os.getenv("WRITER_REVIEW_TRIGGER_SUBS", "3"))
    WRITER_REVIEW_TRIGGER_CHARS: int = int(os.getenv("WRITER_REVIEW_TRIGGER_CHARS", "8000"))
    WRITER_INCREMENTAL_SECTION_REVIEW_RAW: str = os.getenv(
        "WRITER_INCREMENTAL_SECTION_REVIEW", "false"
    ).strip().lower()
    WRITER_INCREMENTAL_SECTION_REVIEW: bool = (
        WRITER_INCREMENTAL_SECTION_REVIEW_RAW in {"true", "1", "yes"}
    )
    WRITER_CONDENSE_MODE_RAW: str = os.getenv(
        "WRITER_CONDENSE_MODE", "warn"
    ).strip().lower()
    WRITER_CONDENSE_MODE: str = (
        WRITER_CONDENSE_MODE_RAW
        if WRITER_CONDENSE_MODE_RAW in {"legacy", "warn"}
        else "legacy"
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
    WRITER_POST_WRITE_EXTRACTION_MODE_RAW: str = os.getenv(
        "WRITER_POST_WRITE_EXTRACTION_MODE", "off"
    ).strip().lower()
    WRITER_POST_WRITE_EXTRACTION_MODE: str = (
        WRITER_POST_WRITE_EXTRACTION_MODE_RAW
        if WRITER_POST_WRITE_EXTRACTION_MODE_RAW in {"off", "shadow"}
        else "off"
    )
    WRITER_SCENE_SPEC_MODE: str = os.getenv("WRITER_SCENE_SPEC_MODE", "off").strip().lower()
    WRITER_SCENE_SPEC_CANARY_TASK_IDS: str = os.getenv(
        "WRITER_SCENE_SPEC_CANARY_TASK_IDS", ""
    )
    WRITER_EXECUTION_CONTRACT_MODE_RAW: str = os.getenv(
        "WRITER_EXECUTION_CONTRACT_MODE", "off"
    ).strip().lower()
    WRITER_EXECUTION_CONTRACT_MODE: str = (
        WRITER_EXECUTION_CONTRACT_MODE_RAW
        if WRITER_EXECUTION_CONTRACT_MODE_RAW in {"off", "shadow", "canary"}
        else "off"
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
    WRITER_HANDOVER_CONTRACT_VERSION_RAW: str = os.getenv(
        "WRITER_HANDOVER_CONTRACT_VERSION", "v1"
    ).strip().lower()
    WRITER_HANDOVER_CONTRACT_VERSION: str = (
        WRITER_HANDOVER_CONTRACT_VERSION_RAW
        if WRITER_HANDOVER_CONTRACT_VERSION_RAW in {"v1", "v2", "v2.1", "v2.2", "v2.3"}
        else "v1"
    )

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
        if self.RAG_RERANKER_ENABLED:
            if self.RAG_RERANKER_PROVIDER not in self._VALID_RERANKER_PROVIDERS:
                warnings.append(
                    f"RAG_RERANKER_PROVIDER={self.RAG_RERANKER_PROVIDER} 无效，"
                    f"可选: {sorted(self._VALID_RERANKER_PROVIDERS)}；reranker 将降级为旧顺序"
                )
            if self.RAG_RERANKER_PROVIDER == "http" and not self.RAG_RERANKER_BASE_URL:
                warnings.append(
                    "RAG_RERANKER_PROVIDER=http 但 RAG_RERANKER_BASE_URL 未设置；"
                    "reranker 将降级为旧顺序"
                )
            if self.RAG_RERANKER_CANDIDATE_K < self.RAG_TOP_K:
                warnings.append(
                    f"RAG_RERANKER_CANDIDATE_K={self.RAG_RERANKER_CANDIDATE_K} "
                    f"小于 RAG_TOP_K={self.RAG_TOP_K}，重排没有可选空间"
                )
            if not 0 <= self.RAG_RERANKER_MIN_SCORE <= 1:
                warnings.append("RAG_RERANKER_MIN_SCORE 必须在 0..1")
            if self.RAG_RERANKER_TIMEOUT_MS < 100:
                warnings.append("RAG_RERANKER_TIMEOUT_MS 过低 (<100)，首次加载模型会超时")
        if self.WRITER_INCREMENTAL_SECTION_REVIEW_RAW not in {
            "true", "1", "yes", "false", "0", "no", ""
        }:
            warnings.append(
                "WRITER_INCREMENTAL_SECTION_REVIEW="
                f"{self.WRITER_INCREMENTAL_SECTION_REVIEW_RAW} 无效，按 false 处理"
            )
        if self.WRITER_STYLE_CONTROL_MODE_RAW not in {"legacy", "shadow", "policy"}:
            warnings.append(
                "WRITER_STYLE_CONTROL_MODE="
                f"{self.WRITER_STYLE_CONTROL_MODE_RAW} invalid; "
                "using shadow (expected legacy/shadow/policy)"
            )
        if self.WRITER_ANTI_AI_EXPRESSION_MODE_RAW not in {
            "off", "shadow", "canary"
        }:
            warnings.append(
                "WRITER_ANTI_AI_EXPRESSION_MODE="
                f"{self.WRITER_ANTI_AI_EXPRESSION_MODE_RAW} invalid; "
                "using off (expected off/shadow/canary)"
            )
        if self.WRITER_COMMERCIAL_HARNESS_MODE_RAW not in {
            "off", "shadow", "canary"
        }:
            warnings.append(
                "WRITER_COMMERCIAL_HARNESS_MODE="
                f"{self.WRITER_COMMERCIAL_HARNESS_MODE_RAW} invalid; "
                "using shadow (expected off/shadow/canary)"
            )
        if self.WRITER_NARRATIVE_INTEGRITY_MODE_RAW not in {
            "off", "shadow", "canary"
        }:
            warnings.append(
                "WRITER_NARRATIVE_INTEGRITY_MODE="
                f"{self.WRITER_NARRATIVE_INTEGRITY_MODE_RAW} invalid; "
                "using shadow (expected off/shadow/canary)"
            )
        if self.WRITER_WORLD_PRESSURE_MODE_RAW not in {
            "off", "shadow", "canary"
        }:
            warnings.append(
                "WRITER_WORLD_PRESSURE_MODE="
                f"{self.WRITER_WORLD_PRESSURE_MODE_RAW} invalid; "
                "using shadow (expected off/shadow/canary)"
            )
        if self.WRITER_WORLD_PRESSURE_PRESET_RAW not in {
            "none", "modern_urban_realism"
        }:
            warnings.append(
                "WRITER_WORLD_PRESSURE_PRESET="
                f"{self.WRITER_WORLD_PRESSURE_PRESET_RAW} invalid; "
                "using none (expected none/modern_urban_realism)"
            )
        if self.WRITER_WORLD_RUNTIME_MODE_RAW not in {
            "off", "shadow", "canary"
        }:
            warnings.append(
                "WRITER_WORLD_RUNTIME_MODE="
                f"{self.WRITER_WORLD_RUNTIME_MODE_RAW} invalid; "
                "using off (expected off/shadow/canary)"
            )
        if self.WRITER_CONDENSE_MODE_RAW not in {"legacy", "warn"}:
            warnings.append(
                f"WRITER_CONDENSE_MODE={self.WRITER_CONDENSE_MODE_RAW} invalid; "
                "using legacy (expected legacy/warn)"
            )
        if self.WRITER_POST_WRITE_EXTRACTION_MODE_RAW not in {"off", "shadow"}:
            warnings.append(
                "WRITER_POST_WRITE_EXTRACTION_MODE="
                f"{self.WRITER_POST_WRITE_EXTRACTION_MODE_RAW} invalid; "
                "using off (expected off/shadow)"
            )
        if self.WRITER_SCENE_SPEC_MODE not in {"off", "shadow", "canary"}:
            warnings.append(
                f"WRITER_SCENE_SPEC_MODE={self.WRITER_SCENE_SPEC_MODE} 无效，按 off 处理；"
                "应为 off/shadow/canary"
            )
        if self.WRITER_EXECUTION_CONTRACT_MODE_RAW not in {
            "off", "shadow", "canary"
        }:
            warnings.append(
                "WRITER_EXECUTION_CONTRACT_MODE="
                f"{self.WRITER_EXECUTION_CONTRACT_MODE_RAW} invalid; "
                "using off (expected off/shadow/canary)"
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
        if self.WRITER_HANDOVER_CONTRACT_VERSION_RAW not in {"v1", "v2", "v2.1", "v2.2", "v2.3"}:
            warnings.append(
                "WRITER_HANDOVER_CONTRACT_VERSION="
                f"{self.WRITER_HANDOVER_CONTRACT_VERSION_RAW} invalid; "
                "using v1 (expected v1/v2/v2.1/v2.2/v2.3)"
            )
        return warnings


settings = Settings()

# ── 日志配置 ──────────────────────────────────────────────────────
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
