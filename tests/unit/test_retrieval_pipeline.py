from app.retrieval_pipeline import (
    ExplainableReranker,
    PlannedQuery,
    QueryPlan,
    QueryPlanner,
    ShadowRetriever,
    merge_candidates,
)


def test_query_planner_is_bounded_and_uses_supported_intents():
    plan = QueryPlanner(max_queries=3).plan(
        topic="周六面包店",
        section_title="流量的背面",
        subsection_title="陌生短信",
        key_points=["林晚收到威胁并想起此前承诺", "周野守在店门口"],
        description="夜里，林晚在房间里查看消息。",
        character_names=["林晚", "周野", "季晴"],
        current_section=14,
        current_subsection=2,
    )

    assert 1 <= len(plan.queries) <= 3
    assert {query.intent for query in plan.queries} <= {
        "character", "event", "foreshadowing", "scene"
    }
    assert all(query.query.strip() for query in plan.queries)
    assert all("季晴" not in query.characters for query in plan.queries)


def test_query_planner_returns_empty_plan_for_empty_input():
    plan = QueryPlanner().plan(current_section=1, current_subsection=1)
    assert plan.queries == ()


def test_merge_candidates_keeps_identity_and_all_intents():
    event = PlannedQuery("event", "父亲 白吐司")
    character = PlannedQuery("character", "周野 父亲", ("周野",))
    merged = merge_candidates([
        (event, [{"id": "same", "text": "A", "score": 0.7, "rank": 2}]),
        (character, [{"id": "same", "text": "A", "score": 0.9, "rank": 1}]),
    ])

    assert len(merged) == 1
    assert merged[0]["best_vector_score"] == 0.9
    assert merged[0]["best_coarse_rank"] == 1
    assert merged[0]["matched_intents"] == ["event", "character"]
    assert merged[0]["coarse_ranks"] == [2, 1]


def test_reranker_excludes_future_and_records_score_components():
    plan = QueryPlan(
        current_section=10,
        current_subsection=2,
        max_queries=4,
        queries=(PlannedQuery("event", "周野 父亲 白吐司", ("周野",)),),
    )
    candidates = [
        {
            "id": "past",
            "text": "周野给父亲留下一袋白吐司。",
            "section": 9,
            "subsection": 1,
            "title": "一袋吐司",
            "metadata": {"characters": '["周野"]'},
            "best_vector_score": 0.9,
            "rank": 1,
            "matched_intents": ["event"],
        },
        {
            "id": "future",
            "text": "未来发生的内容",
            "section": 11,
            "subsection": 1,
            "title": "未来",
            "metadata": {},
            "best_vector_score": 0.99,
            "matched_intents": ["event"],
        },
    ]

    result = ExplainableReranker(min_score=0.2).rerank(plan, candidates)

    assert [item["id"] for item in result["selected"]] == ["past"]
    future = next(item for item in result["candidates"] if item["id"] == "future")
    assert future["reason"] == "future_section"
    past = result["selected"][0]
    assert set(past["score_components"]) == {
        "vector", "keyword", "title", "character", "chapter_proximity"
    }
    assert past["rank_vector_score"] > 0
    assert past["reason"].startswith("selected:intents=event")


def test_reranker_can_return_zero_and_penalizes_same_section_duplicates():
    plan = QueryPlan(8, 1, (PlannedQuery("scene", "完全不同的场景"),), 4)
    weak = [{
        "id": "weak", "text": "无关", "section": 1, "subsection": 1,
        "title": "", "metadata": {}, "best_vector_score": 0.01,
        "matched_intents": ["scene"],
    }]
    assert ExplainableReranker(min_score=0.8).rerank(plan, weak)["selected"] == []

    duplicates = [
        {
            "id": f"d{index}", "text": "面包店 场景", "section": 7,
            "subsection": index, "title": "面包店", "metadata": {},
            "best_vector_score": 0.9 - index * 0.01, "matched_intents": ["scene"],
        }
        for index in range(1, 3)
    ]
    ranked = ExplainableReranker(min_score=0.1).rerank(plan, duplicates)
    second = next(item for item in ranked["candidates"] if item["id"] == "d2")
    assert second["duplicate_section_penalty"] == 0.08


class FakeVectorStore:
    def __init__(self):
        self.calls = []
        self.last_search_trace = {}

    def search_with_meta(self, query, *, k, task_id, candidate_k):
        self.calls.append((query, k, task_id, candidate_k))
        self.last_search_trace = {
            "query": query,
            "filter": {"task_id": task_id},
            "candidate_count": 1,
        }
        return [{
            "id": "doc-1",
            "text": "林晚在面包店删除了图文。",
            "section": 4,
            "subsection": 1,
            "title": "暗涌初现",
            "score": 0.9,
            "rank": 1,
            "metadata": {"task_id": task_id, "characters": '["林晚"]'},
        }]


def test_shadow_retriever_uses_task_filter_and_never_claims_writer_output():
    plan = QueryPlanner().plan_text(
        "林晚删帖后回到面包店",
        requested_intents=["event", "character"],
        character_names=["林晚"],
        current_section=7,
        current_subsection=1,
    )
    store = FakeVectorStore()
    result = ShadowRetriever(candidate_k=8, min_score=0.1).run(
        store, plan, task_id="task-1"
    )

    assert result["mode"] == "shadow"
    assert result["writer_uses"] == "legacy"
    assert result["selected_ids"] == ["doc-1"]
    assert all(call[2] == "task-1" for call in store.calls)
    assert all(call[1] == call[3] == 8 for call in store.calls)
    trace = result["rerank"]["candidates"][0]
    assert trace["id"] == "doc-1"
    assert trace["reason"].startswith("selected:")
    assert "text" not in trace
