from app.retrieval_pipeline import QueryPlanner, ShadowRetriever
from app.vector_store import VectorStore


class FilterAwareCollection:
    def __init__(self):
        self.query_calls = []

    def count(self):
        return 4

    def query(self, **kwargs):
        self.query_calls.append(kwargs)
        return {
            "ids": [["relevant", "future"]],
            "documents": [["周野给父亲留白吐司。", "未来章节内容"]],
            "metadatas": [[
                {"task_id": "task-1", "section": 9, "subsection": 1, "title": "一袋吐司"},
                {"task_id": "task-1", "section": 11, "subsection": 1, "title": "未来"},
            ]],
            "distances": [[0.1, 0.01]],
        }


def test_shadow_pipeline_preserves_task_filter_and_excludes_future_section():
    collection = FilterAwareCollection()
    store = VectorStore.__new__(VectorStore)
    store._collection = collection
    store._last_search_trace = {}
    plan = QueryPlanner().plan_text(
        "周野与父亲之间的一袋白吐司",
        requested_intents=["character", "event"],
        character_names=["周野"],
        current_section=10,
        current_subsection=1,
    )

    result = ShadowRetriever(candidate_k=4, min_score=0.1).run(
        store, plan, task_id="task-1"
    )

    assert result["writer_uses"] == "legacy"
    assert result["selected_ids"] == ["relevant"]
    assert all(call["where"] == {"task_id": "task-1"} for call in collection.query_calls)
    future = next(
        item for item in result["rerank"]["candidates"] if item["id"] == "future"
    )
    assert future["reason"] == "future_section"
