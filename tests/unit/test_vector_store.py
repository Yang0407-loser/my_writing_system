from app.vector_store import VectorStore


class FakeCollection:
    def __init__(self):
        self.add_calls = []
        self.query_calls = []
        self.get_result = {"ids": [], "metadatas": []}
        self.query_result = {
            "ids": [["doc-1", "doc-2", "doc-3"]],
            "documents": [["第一条", "第二条", "第三条"]],
            "metadatas": [[
                {"task_id": "task-1", "section": 1, "subsection": 1, "title": "开端"},
                {"task_id": "task-1", "section": 2, "subsection": 1, "title": "发展"},
                {"task_id": "task-1", "section": 3, "subsection": 1, "title": "转折"},
            ]],
            "distances": [[0.1, 0.25, 0.5]],
        }
        self.deleted = []

    def count(self):
        return 3

    def add(self, **kwargs):
        self.add_calls.append(kwargs)

    def get(self, **kwargs):
        return self.get_result

    def query(self, **kwargs):
        self.query_calls.append(kwargs)
        return self.query_result

    def delete(self, ids):
        self.deleted.extend(ids)


def make_store(collection=None):
    store = VectorStore.__new__(VectorStore)
    store._collection = collection or FakeCollection()
    store._last_search_trace = {}
    return store


def test_add_text_skips_empty_chunks(caplog):
    store = make_store()
    assert store.add_text("   ", {"task_id": "task-1", "section": 2}) is None
    assert store._collection.add_calls == []
    assert "fallback=skip" in caplog.text


def test_add_text_skips_exact_duplicate_within_task():
    collection = FakeCollection()
    collection.get_result = {"ids": ["existing-id"], "metadatas": [{}]}
    store = make_store(collection)

    result = store.add_text("同一个文本块", {"task_id": "task-1", "section": 1})

    assert result == "existing-id"
    assert collection.add_calls == []


def test_add_text_normalizes_metadata_and_adds_provenance():
    store = make_store()
    doc_id = store.add_text(
        "  正文块  ",
        {"task_id": "task-1", "section": 1, "characters": ["林晚", "周野"]},
    )

    assert doc_id
    call = store._collection.add_calls[0]
    assert call["documents"] == ["正文块"]
    metadata = call["metadatas"][0]
    assert metadata["characters"] == '["林晚","周野"]'
    assert len(metadata["content_hash"]) == 64
    assert metadata["source_version"] == 1
    assert metadata["created_at"]


def test_search_with_meta_traces_coarse_candidates_but_returns_legacy_k():
    store = make_store()

    items = store.search_with_meta("父亲 白吐司", k=2, task_id="task-1", candidate_k=3)

    assert [item["id"] for item in items] == ["doc-1", "doc-2"]
    assert items[0]["metadata"]["task_id"] == "task-1"
    assert items[0]["distance"] == 0.1
    assert items[0]["score"] > items[1]["score"]
    assert store._collection.query_calls[0]["n_results"] == 3
    assert store._collection.query_calls[0]["where"] == {"task_id": "task-1"}
    assert store.last_search_trace["candidate_count"] == 3
    assert store.last_search_trace["returned_count"] == 2
    assert len(store.last_search_trace["candidates"]) == 3


def test_search_empty_collection_records_empty_trace():
    collection = FakeCollection()
    collection.count = lambda: 0
    store = make_store(collection)

    assert store.search_with_meta("query", task_id="task-1") == []
    assert store.last_search_trace["filter"] == {"task_id": "task-1"}
    assert store.last_search_trace["candidate_count"] == 0


def test_enforce_task_limit_deletes_oldest_created_at_first(monkeypatch):
    collection = FakeCollection()
    collection.get_result = {
        "ids": ["new", "old", "middle"],
        "metadatas": [
            {"created_at": "2026-07-03"},
            {"created_at": "2026-07-01"},
            {"created_at": "2026-07-02"},
        ],
    }
    store = make_store(collection)
    monkeypatch.setattr(store, "MAX_CHUNKS_PER_TASK", 2)

    assert store.enforce_task_limit("task-1") == 1
    assert collection.deleted == ["old"]
