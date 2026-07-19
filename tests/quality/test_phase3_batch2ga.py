import inspect

from app.event_chunker import EventChunker
from tests.quality.baseline import ROOT, load_json


REPORT = ROOT / "reports" / "phase3-batch2ga-event-chunking.json"
REVIEW = ROOT / "tests" / "quality" / "phase3_batch2ga_event_evidence_review.json"
JUDGMENTS = ROOT / "tests" / "quality" / "phase3_batch2ga_event_evidence_judgments.json"


def test_batch2ga_chunker_contract_has_no_evaluation_or_storage_inputs():
    assert set(inspect.signature(EventChunker.chunk_parent).parameters) == {"self", "parent"}


def test_batch2ga_is_offline_and_leaves_production_unchanged():
    report = load_json(REPORT)
    assert report["mode"] == "offline_parent_event_feasibility_no_embedding_no_chroma"
    assert report["production_changed"] is False
    assert report["writer_changed"] is False
    assert report["embedding_called"] is False
    assert report["chroma_read"] is False
    assert report["chroma_write"] is False
    assert report["database_created"] is False
    assert report["runtime_uses_gold_or_must_recall_facts"] is False


def test_batch2ga_parent_event_contracts_are_reconstructable_and_traceable():
    report = load_json(REPORT)
    metrics = report["structure_metrics"]
    assert metrics["parent_occurrences"] == 38
    assert metrics["unique_parents"] == 23
    assert metrics["reconstructable_unique_parents"] == 23
    assert metrics["event_count"] == 45
    assert metrics["exact_offset_traceability"] == 1.0
    assert metrics["parent_text_coverage"] == 1.0
    assert metrics["orphan_events"] == 0
    assert metrics["empty_events"] == 0
    assert metrics["duplicate_event_ids"] == 0
    assert metrics["hash_mismatches"] == 0
    assert metrics["known_chain_breaks"] == {
        "dialogue": 0, "invitation_response": 0, "money_people": 0, "action_result": 0
    }
    parents = {item["source_id"]: item for item in report["contracts"]["parents"]}
    for event in report["contracts"]["events"]:
        parent = parents[event["parent_source_id"]]
        assert parent["text"][event["start"]:event["end"]] == event["text"]


def test_batch2ga_passes_offline_feasibility_gates_without_relabeling_ceiling_items():
    report, review, judgments = load_json(REPORT), load_json(REVIEW), load_json(JUDGMENTS)
    assert report["assembly_metrics"]["mean_tokens"] == 369.5
    assert report["assembly_metrics"]["weighted_token_reduction"] == 0.2143
    assert report["assembly_metrics"]["full_parent_fallbacks"] == 0
    assert report["fact_evidence_review"] == {
        "item_count": 11, "independently_verifiable_items": 9, "reviewed": 11,
        "independently_verifiable_preserved": 9, "status": "complete"
    }
    assert report["all_gates_passed"] is True
    assert all(report["gates"].values())
    assert report["decision"] == "recommend_batch_2gb_authorization"
    ceiling = [item for item in review["items"] if item["baseline_annotation_ceiling"]]
    assert {item["review_item_id"] for item in ceiling} == {"q06-679a7aa0", "q07-679a7aa0"}
    assert all(item["codex_assisted_evidence_preserved"] is False for item in ceiling)
    assert judgments["independent_human_confirmation"] is False
    assert all(item["review_provenance"] == "codex_assisted_review" for item in review["items"])
