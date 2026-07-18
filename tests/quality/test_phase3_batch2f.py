import inspect

from app.structured_context_compactor import StructuredContextCompactor
from tests.quality.baseline import ROOT, load_json


REPORT = ROOT / "reports" / "phase3-batch2f-structured-compaction.json"
REVIEW = ROOT / "tests" / "quality" / "phase3_batch2f_evidence_review.json"
JUDGMENTS = ROOT / "tests" / "quality" / "phase3_batch2f_evidence_judgments.json"


def test_batch2f_runtime_contract_has_no_evaluation_inputs():
    assert set(inspect.signature(StructuredContextCompactor.compact).parameters) == {
        "self", "query", "sources", "character_names"
    }


def test_batch2f_frozen_v1_contract_and_source_traceability():
    report = load_json(REPORT)
    assert report["mode"] == "frozen_v1_selected_sources_structured_compaction_shadow"
    assert report["production_changed"] is False
    assert report["writer_changed"] is False
    assert report["query_planner_changed"] is False
    assert report["reranker_changed"] is False
    assert report["candidate_set_changed"] is False
    assert report["runtime_uses_gold_or_must_recall_facts"] is False
    for profile, metrics in report["metrics"].items():
        assert metrics["selected_sources"] == 38, profile
        assert metrics["selected_source_retention"] == 1.0, profile
        assert metrics["known_relevant_source_retention"] == 1.0, profile
        assert metrics["late_source_retention"] == 1.0, profile
        assert metrics["all_fragments_traceable"] is True, profile


def test_batch2f_profiles_show_compression_evidence_tradeoff_and_no_winner():
    report = load_json(REPORT)
    metrics, review = report["metrics"], report["fact_evidence_review"]
    assert metrics["paragraph_window"]["mean_compacted_tokens"] == 288.3
    assert metrics["paragraph_window"]["weighted_token_reduction"] == 0.387
    assert review["paragraph_window"]["preserved"] == 4
    assert metrics["dialogue_narrative_block"]["mean_compacted_tokens"] == 346.7
    assert metrics["dialogue_narrative_block"]["weighted_token_reduction"] == 0.2628
    assert review["dialogue_narrative_block"]["preserved"] == 7
    assert metrics["character_span_150"]["weighted_token_reduction"] == 0.0776
    assert review["character_span_150"]["preserved"] == 9
    assert review["character_span_250"]["preserved"] == 9
    assert review["character_span_350"]["preserved"] == 9
    assert report["eligible_profiles"] == []
    assert report["selected_profile"] is None
    assert report["decision"] == "remain_shadow_no_structured_profile_passed"


def test_batch2f_review_is_complete_codex_assisted_and_records_annotation_ceiling():
    report, review, judgments = load_json(REPORT), load_json(REVIEW), load_json(JUDGMENTS)
    assert report["baseline_annotation_ceiling"] == {
        "independently_verifiable_items": 9,
        "total_items": 11,
        "affected_review_item_ids": ["q06-679a7aa0", "q07-679a7aa0"],
        "reason": "The original sources do not independently contain every part of their assigned supports_which_fact claims.",
    }
    assert len(review["items"]) == 11
    assert len(judgments["baseline_annotation_findings"]) == 2
    for summary in review["summary"].values():
        assert summary["reviewed"] == 11
        assert summary["status"] == "complete"
    for item in review["items"]:
        for row in item["strategies"].values():
            assert row["review_provenance"] == "codex_assisted_review"
            assert row["independent_human_confirmation"] is False
            assert row["codex_assisted_evidence_preserved"] in (True, False)
            assert row["codex_review_note"].strip()
