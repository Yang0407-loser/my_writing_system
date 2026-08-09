"""Tests for the default-off local cross-encoder reranker.

The most important assertion in this file is the first one: with the flag off,
retrieval must be byte-identical to the legacy implementation — same coarse
query size, same returned items, and a trace with no extra keys.
"""

import pytest

from app import reranker as reranker_module
from app.config import settings
from app.reranker import RerankProvider, rerank_items, reset_for_tests
from app.vector_store import VectorStore


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


class StaticProvider(RerankProvider):
    """Deterministic provider driven by a fixed score list."""

    def __init__(self, scores, *, raises=None, delay=0.0, model="static-test"):
        self._scores = scores
        self._raises = raises
        self._delay = delay
        self._model = model
        self.calls = []

    def score_pairs(self, query, documents):
        self.calls.append((query, list(documents)))
        if self._delay:
            import time

            time.sleep(self._delay)
        if self._raises is not None:
            raise self._raises
        return list(self._scores)

    @property
    def model_name(self):
        return self._model


def make_items(count=5):
    return [
        {
            "id": f"doc-{index}",
            "text": f"正文块 {index}",
            "section": index,
            "subsection": 1,
            "title": f"标题{index}",
            "score": round(1.0 - index * 0.1, 6),
            "rank": index,
        }
        for index in range(1, count + 1)
    ]


class FakeCollection:
    """Returns `total` documents so candidate widening is observable."""

    def __init__(self, total=25):
        self.total = total
        self.query_calls = []

    def count(self):
        return self.total

    def query(self, **kwargs):
        n = kwargs["n_results"]
        self.query_calls.append(kwargs)
        return {
            "ids": [[f"doc-{i}" for i in range(1, n + 1)]],
            "documents": [[f"正文块 {i}" for i in range(1, n + 1)]],
            "metadatas": [
                [
                    {"task_id": "t1", "section": i, "subsection": 1, "title": f"标题{i}"}
                    for i in range(1, n + 1)
                ]
            ],
            "distances": [[0.1 * i for i in range(1, n + 1)]],
        }


def make_store(collection=None):
    store = VectorStore.__new__(VectorStore)
    store._collection = collection or FakeCollection()
    store._last_search_trace = {}
    return store


@pytest.fixture(autouse=True)
def _clean_reranker_state():
    reset_for_tests()
    yield
    reset_for_tests()


# ---------------------------------------------------------------------------
# 1. the flag-off guarantee
# ---------------------------------------------------------------------------


def test_disabled_keeps_legacy_query_size_items_and_trace(monkeypatch):
    monkeypatch.setattr(settings, "RAG_RERANKER_ENABLED", False)
    collection = FakeCollection()
    store = make_store(collection)

    items = store.search_with_meta("查询", k=5, task_id="t1")

    assert len(items) == 5
    assert [item["id"] for item in items] == [f"doc-{i}" for i in range(1, 6)]
    # coarse query was NOT widened
    assert collection.query_calls[0]["n_results"] == 5
    # trace carries no reranker key at all
    assert set(store.last_search_trace) == {
        "query",
        "filter",
        "elapsed_ms",
        "candidate_count",
        "returned_count",
        "candidates",
    }
    # no rerank_* keys leaked onto the items
    assert not any(key.startswith("rerank") for item in items for key in item)


def test_disabled_still_honours_explicit_candidate_k(monkeypatch):
    monkeypatch.setattr(settings, "RAG_RERANKER_ENABLED", False)
    collection = FakeCollection()
    store = make_store(collection)

    store.search_with_meta("查询", k=5, task_id="t1", candidate_k=12)

    assert collection.query_calls[0]["n_results"] == 12


# ---------------------------------------------------------------------------
# 2. enabled path
# ---------------------------------------------------------------------------


def test_enabled_widens_candidates_and_reorders(monkeypatch):
    monkeypatch.setattr(settings, "RAG_RERANKER_ENABLED", True)
    monkeypatch.setattr(settings, "RAG_RERANKER_CANDIDATE_K", 20)
    # doc-20 .. doc-16 are the most relevant; all are below the legacy top-5
    scores = [0.0] * 20
    for offset, doc_index in enumerate([20, 19, 18, 17, 16]):
        scores[doc_index - 1] = 9.0 - offset
    monkeypatch.setattr(
        reranker_module, "get_rerank_provider", lambda: StaticProvider(scores)
    )
    collection = FakeCollection()
    store = make_store(collection)

    items = store.search_with_meta("查询", k=5, task_id="t1")

    assert collection.query_calls[0]["n_results"] == 20
    assert [item["id"] for item in items] == [
        "doc-20",
        "doc-19",
        "doc-18",
        "doc-17",
        "doc-16",
    ]
    assert [item["rerank_rank"] for item in items] == [1, 2, 3, 4, 5]
    trace = store.last_search_trace["rerank"]
    assert trace["applied"] is True
    assert trace["order_changed"] is True
    assert trace["candidate_count"] == 20
    assert set(trace["promoted_ids"]) == {f"doc-{i}" for i in (16, 17, 18, 19, 20)}
    assert set(trace["demoted_ids"]) == {f"doc-{i}" for i in (1, 2, 3, 4, 5)}
    assert store.last_search_trace["returned_count"] == 5


def test_enabled_but_order_unchanged_is_reported(monkeypatch):
    monkeypatch.setattr(settings, "RAG_RERANKER_ENABLED", True)
    monkeypatch.setattr(settings, "RAG_RERANKER_CANDIDATE_K", 20)
    descending = [1.0 - 0.01 * i for i in range(20)]
    monkeypatch.setattr(
        reranker_module, "get_rerank_provider", lambda: StaticProvider(descending)
    )
    store = make_store()

    items = store.search_with_meta("查询", k=5, task_id="t1")

    assert [item["id"] for item in items] == [f"doc-{i}" for i in range(1, 6)]
    assert store.last_search_trace["rerank"]["order_changed"] is False


# ---------------------------------------------------------------------------
# 3. fail-open behaviour
# ---------------------------------------------------------------------------


def test_provider_error_falls_back_to_legacy_order():
    items = make_items()
    provider = StaticProvider([], raises=RuntimeError("model exploded"))

    selected, trace = rerank_items("查询", items, 3, provider=provider)

    assert [item["id"] for item in selected] == ["doc-1", "doc-2", "doc-3"]
    assert trace["applied"] is False
    assert trace["reason"] == "RuntimeError"
    assert reranker_module.is_degraded() is not None


def test_timeout_falls_back_and_degrades_process():
    items = make_items()
    provider = StaticProvider([1.0] * 5, delay=0.5)

    selected, trace = rerank_items("查询", items, 3, provider=provider, timeout_ms=100)

    assert [item["id"] for item in selected] == ["doc-1", "doc-2", "doc-3"]
    assert trace["reason"] == "timeout"
    assert "timeout" in reranker_module.is_degraded()


def test_score_length_mismatch_falls_back():
    items = make_items()
    provider = StaticProvider([0.9, 0.8])  # 2 scores for 5 documents

    selected, trace = rerank_items("查询", items, 3, provider=provider)

    assert [item["id"] for item in selected] == ["doc-1", "doc-2", "doc-3"]
    assert trace["reason"] == "score_length_mismatch"


def test_min_score_removing_everything_falls_back_not_empty():
    items = make_items()
    provider = StaticProvider([0.01] * 5)

    selected, trace = rerank_items(
        "查询", items, 3, provider=provider, min_score=0.9
    )

    assert len(selected) == 3
    assert trace["reason"] == "min_score_removed_all"


def test_degraded_provider_is_not_retried(monkeypatch):
    monkeypatch.setattr(settings, "RAG_RERANKER_ENABLED", True)
    monkeypatch.setattr(settings, "RAG_RERANKER_CANDIDATE_K", 20)
    failing = StaticProvider([], raises=RuntimeError("boom"))
    rerank_items("查询", make_items(), 3, provider=failing)
    assert reranker_module.is_degraded() is not None

    # the real factory must now refuse to build anything
    assert reranker_module.get_rerank_provider() is None

    store = make_store()
    items = store.search_with_meta("查询", k=5, task_id="t1")
    assert [item["id"] for item in items] == [f"doc-{i}" for i in range(1, 6)]
    assert store.last_search_trace["rerank"]["applied"] is False


def test_empty_candidate_list_is_safe():
    selected, trace = rerank_items("查询", [], 5, provider=StaticProvider([]))
    assert selected == []
    assert trace["reason"] == "no_candidates"


# ---------------------------------------------------------------------------
# 4. scoring semantics
# ---------------------------------------------------------------------------


def test_logits_are_sigmoid_normalized():
    items = make_items(3)
    provider = StaticProvider([5.0, -5.0, 0.0])

    selected, trace = rerank_items("查询", items, 3, provider=provider)

    assert trace["normalized"] is True
    assert [item["id"] for item in selected] == ["doc-1", "doc-3", "doc-2"]
    assert selected[0]["rerank_score"] == pytest.approx(0.993307, abs=1e-5)
    assert selected[1]["rerank_score"] == pytest.approx(0.5, abs=1e-9)
    assert selected[0]["rerank_raw_score"] == 5.0


def test_bounded_scores_are_left_untouched():
    items = make_items(3)
    provider = StaticProvider([0.9, 0.1, 0.5])

    selected, trace = rerank_items("查询", items, 3, provider=provider)

    assert trace["normalized"] is False
    assert selected[0]["rerank_score"] == 0.9


def test_ties_break_deterministically_by_coarse_rank():
    items = make_items(4)
    provider = StaticProvider([0.5, 0.5, 0.5, 0.5])

    first, _ = rerank_items("查询", items, 4, provider=provider)
    second, _ = rerank_items("查询", make_items(4), 4, provider=StaticProvider([0.5] * 4))

    assert [item["id"] for item in first] == ["doc-1", "doc-2", "doc-3", "doc-4"]
    assert [item["id"] for item in first] == [item["id"] for item in second]


def test_min_score_drops_only_weak_candidates():
    items = make_items(5)
    provider = StaticProvider([0.95, 0.05, 0.90, 0.02, 0.85])

    selected, trace = rerank_items("查询", items, 5, provider=provider, min_score=0.5)

    assert [item["id"] for item in selected] == ["doc-1", "doc-3", "doc-5"]
    assert trace["dropped_by_min_score"] == 2


def test_original_item_fields_are_preserved():
    items = make_items(3)
    provider = StaticProvider([0.9, 0.8, 0.7])

    selected, _ = rerank_items("查询", items, 3, provider=provider)

    assert selected[0]["title"] == "标题1"
    assert selected[0]["section"] == 1
    assert selected[0]["text"] == "正文块 1"
    assert selected[0]["coarse_rank"] == 1


# ---------------------------------------------------------------------------
# 5. config validation
# ---------------------------------------------------------------------------


def test_validate_is_silent_when_reranker_disabled(monkeypatch):
    monkeypatch.setattr(settings, "RAG_RERANKER_ENABLED", False)
    monkeypatch.setattr(settings, "RAG_RERANKER_PROVIDER", "nonsense")
    assert not [w for w in settings.validate() if "RERANKER" in w]


def test_validate_flags_bad_reranker_config(monkeypatch):
    monkeypatch.setattr(settings, "RAG_RERANKER_ENABLED", True)
    monkeypatch.setattr(settings, "RAG_RERANKER_PROVIDER", "nonsense")
    monkeypatch.setattr(settings, "RAG_RERANKER_CANDIDATE_K", 2)
    monkeypatch.setattr(settings, "RAG_TOP_K", 5)

    warnings = settings.validate()

    assert any("RAG_RERANKER_PROVIDER" in w for w in warnings)
    assert any("RAG_RERANKER_CANDIDATE_K" in w for w in warnings)


def test_validate_requires_base_url_for_http_provider(monkeypatch):
    monkeypatch.setattr(settings, "RAG_RERANKER_ENABLED", True)
    monkeypatch.setattr(settings, "RAG_RERANKER_PROVIDER", "http")
    monkeypatch.setattr(settings, "RAG_RERANKER_BASE_URL", "")

    assert any("RAG_RERANKER_BASE_URL" in w for w in settings.validate())
