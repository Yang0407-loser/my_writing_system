import inspect

from app.context_compactor import ContextCompactor
from tests.quality.baseline import ROOT, load_json


REPORT_PATH = ROOT / "reports" / "phase3-batch2e-context-compaction.json"
REVIEW_PATH = ROOT / "tests" / "quality" / "phase3_batch2e_fact_evidence_review.json"


def test_batch2e_compactor_runtime_contract_has_no_gold_or_fact_inputs():
    parameters = set(inspect.signature(ContextCompactor.compact).parameters)

    assert parameters == {"self", "query", "sources", "character_names"}


def test_batch2e_report_preserves_sources_but_fails_evidence_gate():
    report = load_json(REPORT_PATH)
    metrics = report["metrics"]

    assert report["mode"] == "real_v1_retrieval_shadow_context_compaction"
    assert report["production_changed"] is False
    assert report["writer_changed"] is False
    assert report["query_planner_changed"] is False
    assert report["reranker_changed"] is False
    assert metrics["selected_sources"] == 38
    assert metrics["represented_sources"] == 38
    assert metrics["known_relevant_source_retention"] == 1.0
    assert metrics["supported_fact_source_retention"] == 1.0
    assert metrics["mean_raw_tokens"] == 470.3
    assert metrics["mean_deduplicated_tokens"] == 470.3
    assert metrics["mean_compacted_tokens"] == 80.7
    assert metrics["weighted_token_reduction"] == 0.8284
    assert metrics["near_duplicate_groups"] == 0
    assert metrics["budget_overflow_queries"] == 0
    assert report["gates"]["codex_assisted_evidence_preserved"] is False
    assert report["all_compaction_gates_passed"] is False
    assert report["decision"] == "remain_shadow_compaction_failed"


def test_batch2e_all_fragments_are_traceable_and_each_source_is_represented():
    report = load_json(REPORT_PATH)

    assert report["gates"]["all_fragments_traceable"] is True
    for query_index, result in report["compactions"].items():
        assert float(result["source_retention"]) == 1.0, query_index
        assert set(result["selected_source_ids"]) == set(result["represented_source_ids"])
        for fragment in result["fragments"]:
            assert fragment["source_id"] in result["selected_source_ids"]
            assert 0 <= fragment["start"] < fragment["end"]
            assert fragment["text"]


def test_batch2e_codex_assisted_review_records_ten_evidence_losses():
    review = load_json(REVIEW_PATH)

    assert review["summary"] == {
        "item_count": 11,
        "codex_assisted_reviewed": 11,
        "evidence_preserved": 1,
        "status": "complete",
    }
    assert sum(item["codex_assisted_evidence_preserved"] is False for item in review["items"]) == 10
    assert sum(item["codex_assisted_evidence_preserved"] is True for item in review["items"]) == 1
    assert all(item["review_provenance"] == "codex_assisted_review" for item in review["items"])
    assert all(item["independent_human_confirmation"] is False for item in review["items"])
    assert all(item["codex_review_note"].strip() for item in review["items"])
