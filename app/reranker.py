"""Optional local cross-encoder reranking for the production RAG top-k.

Default OFF.  When ``RAG_RERANKER_ENABLED`` is false nothing in this module is
imported by the retrieval path, and ``VectorStore.search_with_meta`` behaves
byte-identically to the legacy implementation — same coarse query size, same
returned items, same ``last_search_trace`` keys.

Design constraints, matching the project's batch discipline:

* **No new dependency.** ``sentence-transformers>=3.0.0`` is already required
  for BGE-M3; ``CrossEncoder`` ships with it.
* **No text leaves the machine.** The cross-encoder runs locally, so enabling
  it does not interact with the tenant policy that blocks sending private
  prose, prompts or RAG content to external APIs.
* **Fail-open.** Any import error, provider error or timeout degrades to the
  legacy vector order and marks the provider degraded for the rest of the
  process.  Nothing raises into the writing loop.
* **Traceable.** Every rerank emits a deterministic trace with per-candidate
  before/after ranks, so ``rag_recall_log`` carries enough evidence for an
  offline P@5 comparison without re-running retrieval.

This module deliberately does NOT reopen Phase 3.  It does not touch
QueryPlanner, the multi-intent coarse recall, or ``ExplainableReranker``.  It
only reorders the single legacy query's candidate list.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from typing import Sequence

from .config import settings


logger = logging.getLogger("writing_system.reranker")

# Documents longer than this are truncated before scoring.  BGE-reranker-v2-m3
# accepts 8192 tokens, but long tails add latency without changing the order.
MAX_DOC_CHARS = 1200


class RerankProvider(ABC):
    """Score (query, document) pairs.  Higher means more relevant."""

    @abstractmethod
    def score_pairs(self, query: str, documents: Sequence[str]) -> list[float]:
        ...

    @property
    @abstractmethod
    def model_name(self) -> str:
        ...


class CrossEncoderRerankProvider(RerankProvider):
    """Local BGE-reranker cross-encoder via sentence-transformers."""

    def __init__(self, model_name: str = "BAAI/bge-reranker-v2-m3"):
        from sentence_transformers import CrossEncoder  # lazy: only when enabled

        self._model_name = model_name
        started = time.perf_counter()
        self._model = CrossEncoder(model_name)
        logger.info(
            "reranker model loaded: model=%s load_ms=%.1f",
            model_name,
            (time.perf_counter() - started) * 1000,
        )

    def score_pairs(self, query: str, documents: Sequence[str]) -> list[float]:
        if not documents:
            return []
        pairs = [(query, doc[:MAX_DOC_CHARS]) for doc in documents]
        return [float(value) for value in self._model.predict(pairs)]

    @property
    def model_name(self) -> str:
        return self._model_name


class HttpRerankProvider(RerankProvider):
    """A local rerank HTTP endpoint (TEI / Xinference / Jina-compatible).

    Expects ``POST {base_url}/rerank`` with ``{model, query, documents}`` and a
    response containing ``results: [{index, relevance_score}, ...]``.
    """

    def __init__(
        self,
        base_url: str,
        model_name: str = "bge-reranker-v2-m3",
        timeout_s: float = 5.0,
    ):
        import requests  # already a dependency

        self._requests = requests
        self._base_url = base_url.rstrip("/")
        self._model_name = model_name
        self._timeout_s = timeout_s

    def score_pairs(self, query: str, documents: Sequence[str]) -> list[float]:
        if not documents:
            return []
        payload = {
            "model": self._model_name,
            "query": query,
            "documents": [doc[:MAX_DOC_CHARS] for doc in documents],
        }
        response = self._requests.post(
            f"{self._base_url}/rerank", json=payload, timeout=self._timeout_s
        )
        response.raise_for_status()
        body = response.json()
        results = body.get("results")
        if not isinstance(results, list):
            raise ValueError("RerankEndpointMissingResults")
        scores = [0.0] * len(documents)
        for entry in results:
            index = entry.get("index")
            if isinstance(index, int) and 0 <= index < len(scores):
                scores[index] = float(entry.get("relevance_score") or 0.0)
        return scores

    @property
    def model_name(self) -> str:
        return self._model_name


# ---------------------------------------------------------------------------
# process-wide provider state
# ---------------------------------------------------------------------------

_provider: RerankProvider | None = None
_provider_lock = threading.Lock()
_degraded_reason: str | None = None
_executor: ThreadPoolExecutor | None = None


def _mark_degraded(reason: str) -> None:
    """Disable reranking for the rest of the process, keeping legacy order."""
    global _degraded_reason
    if _degraded_reason is None:
        _degraded_reason = reason
        logger.warning(
            "reranker degraded to legacy vector order for this process: %s", reason
        )


def is_degraded() -> str | None:
    return _degraded_reason


def reset_for_tests() -> None:
    """Clear cached provider/degraded state.  Tests only."""
    global _provider, _degraded_reason, _executor
    with _provider_lock:
        _provider = None
        _degraded_reason = None
        if _executor is not None:
            _executor.shutdown(wait=False)
            _executor = None


def get_rerank_provider() -> RerankProvider | None:
    """Build the configured provider once.  Returns None when unavailable."""
    global _provider
    if _degraded_reason is not None:
        return None
    if _provider is not None:
        return _provider
    with _provider_lock:
        if _provider is not None:
            return _provider
        name = (settings.RAG_RERANKER_PROVIDER or "").strip().lower()
        try:
            if name == "cross_encoder":
                _provider = CrossEncoderRerankProvider(settings.RAG_RERANKER_MODEL)
            elif name == "http":
                if not settings.RAG_RERANKER_BASE_URL:
                    raise ValueError("RAG_RERANKER_BASE_URL required for http provider")
                _provider = HttpRerankProvider(
                    base_url=settings.RAG_RERANKER_BASE_URL,
                    model_name=settings.RAG_RERANKER_MODEL,
                    timeout_s=settings.RAG_RERANKER_TIMEOUT_MS / 1000.0,
                )
            else:
                raise ValueError(f"unsupported RAG_RERANKER_PROVIDER={name!r}")
        except Exception as error:
            _mark_degraded(f"{type(error).__name__}: {error}")
            return None
        return _provider


def _get_executor() -> ThreadPoolExecutor:
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="rag-reranker"
        )
    return _executor


def _sigmoid(value: float) -> float:
    if value >= 0:
        return 1.0 / (1.0 + math.exp(-value))
    exponent = math.exp(value)
    return exponent / (1.0 + exponent)


# ---------------------------------------------------------------------------
# public entry point
# ---------------------------------------------------------------------------


def rerank_items(
    query: str,
    items: list[dict],
    top_k: int,
    *,
    provider: RerankProvider | None = None,
    min_score: float | None = None,
    timeout_ms: int | None = None,
) -> tuple[list[dict], dict]:
    """Reorder ``items`` by cross-encoder relevance and return the top-k.

    Returns ``(items, trace)``.  On ANY failure the returned items are exactly
    ``items[:top_k]`` — the legacy vector order — and the trace records why.
    This function never raises.
    """
    trace: dict = {
        "applied": False,
        "provider": None,
        "model": None,
        "candidate_count": len(items),
        "top_k": top_k,
        "elapsed_ms": 0.0,
        "reason": None,
    }
    legacy = items[:top_k]
    if not items:
        trace["reason"] = "no_candidates"
        return legacy, trace

    active = provider if provider is not None else get_rerank_provider()
    if active is None:
        trace["reason"] = _degraded_reason or "provider_unavailable"
        return legacy, trace

    trace["provider"] = settings.RAG_RERANKER_PROVIDER if provider is None else "injected"
    trace["model"] = active.model_name
    budget_ms = timeout_ms if timeout_ms is not None else settings.RAG_RERANKER_TIMEOUT_MS
    floor = min_score if min_score is not None else settings.RAG_RERANKER_MIN_SCORE

    started = time.perf_counter()
    documents = [str(item.get("text", "")) for item in items]
    try:
        future = _get_executor().submit(active.score_pairs, query, documents)
        raw_scores = future.result(timeout=max(0.05, budget_ms / 1000.0))
    except FutureTimeout:
        _mark_degraded(f"score timeout > {budget_ms}ms")
        trace["reason"] = "timeout"
        trace["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
        return legacy, trace
    except Exception as error:
        _mark_degraded(f"{type(error).__name__}: {error}")
        trace["reason"] = type(error).__name__
        trace["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
        return legacy, trace

    if len(raw_scores) != len(items):
        _mark_degraded(
            f"score length mismatch: got {len(raw_scores)} for {len(items)} docs"
        )
        trace["reason"] = "score_length_mismatch"
        trace["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
        return legacy, trace

    # Cross-encoders emit unbounded logits; HTTP endpoints usually emit 0..1.
    # Normalize only when needed so an already-bounded score is left untouched.
    needs_sigmoid = any(score < 0.0 or score > 1.0 for score in raw_scores)
    scores = [_sigmoid(score) for score in raw_scores] if needs_sigmoid else list(raw_scores)
    trace["normalized"] = needs_sigmoid

    enriched: list[dict] = []
    for index, (item, raw, score) in enumerate(zip(items, raw_scores, scores)):
        enriched.append(
            {
                **item,
                "coarse_rank": int(item.get("rank") or index + 1),
                "rerank_raw_score": round(float(raw), 6),
                "rerank_score": round(float(score), 6),
            }
        )

    # Deterministic: score desc, then original coarse rank, then id.
    ordered = sorted(
        enriched,
        key=lambda item: (
            -float(item["rerank_score"]),
            int(item["coarse_rank"]),
            str(item.get("id", "")),
        ),
    )
    kept = [item for item in ordered if float(item["rerank_score"]) >= floor]
    dropped_by_floor = len(ordered) - len(kept)
    if not kept:
        # A floor that removes everything is a configuration error, not a
        # reason to hand Writer an empty context.
        _mark_degraded(f"min_score={floor} removed all {len(ordered)} candidates")
        trace["reason"] = "min_score_removed_all"
        trace["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
        return legacy, trace

    selected = kept[:top_k]
    for position, item in enumerate(selected, 1):
        item["rerank_rank"] = position

    legacy_ids = [str(item.get("id", "")) for item in legacy]
    selected_ids = [str(item.get("id", "")) for item in selected]
    trace.update(
        {
            "applied": True,
            "reason": "ok",
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
            "min_score": floor,
            "dropped_by_min_score": dropped_by_floor,
            "order_changed": legacy_ids != selected_ids,
            "legacy_top_k_ids": legacy_ids,
            "reranked_top_k_ids": selected_ids,
            "promoted_ids": [i for i in selected_ids if i not in legacy_ids],
            "demoted_ids": [i for i in legacy_ids if i not in selected_ids],
            "candidates": [
                {
                    "id": str(item.get("id", "")),
                    "coarse_rank": int(item["coarse_rank"]),
                    "rerank_score": item["rerank_score"],
                    "selected": str(item.get("id", "")) in selected_ids,
                }
                for item in ordered
            ],
        }
    )
    return selected, trace
