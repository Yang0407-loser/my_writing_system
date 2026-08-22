import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / "reports" / "phase4-entry-context-census.json"


def load_report():
    return json.loads(REPORT.read_text(encoding="utf-8"))


def test_phase3_is_closed_without_production_promotion_or_shadow_cleanup():
    report = load_report()
    closure = report["phase3_closure"]
    assert closure["status"] == "closed_experiments_not_promoted_production_legacy_frozen"
    assert closure["production_contract"] == "shared_collection + original task_id filter + current RAG_TOP_K"
    assert closure["shadow_event_count_retained"] == 45
    assert closure["cleanup_executed"] is False
    assert report["production_behavior_changed"] is False
    assert report["writer_generation_calls"] == 0
    assert report["offline_llm_calls"] == 0


def test_census_has_ten_traceable_prompt_ledgers():
    report = load_report()
    assert report["summary"]["query_count"] == 10
    assert len(report["samples"]) == 10
    assert len(report["retrieval_runs"]) == 10
    assert all(run["filter"] == {"task_id": "07d1391e-06ff-4af3-8bd7-6a404d2f4fd6"} for run in report["retrieval_runs"])
    assert all(run["returned"] == 5 and len(run["source_ids"]) == 5 for run in report["retrieval_runs"])
    for sample in report["samples"]:
        ledger = sample["ledger"]
        assert sum(value["estimated_tokens"] for value in ledger["categories"].values()) == ledger["total_estimated_tokens"]
        assert 1 <= sample["recent_original_count"] <= 3
        assert sample["rag_item_count"] == 5
        assert sample["prompt_rendered_without_llm"] is True
        assert sample["prompt_hash"]
        for block in sample["blocks"]:
            assert block["block_id"] and block["category"] and block["source_id"]
            assert block["injection_position"]
            assert "text" not in block
            assert len(block["text_hash"]) == 64
            assert block["requirement"] in {
                "hard_required", "continuity_required", "evidence_required", "optional_context"
            }


def test_required_manifest_and_evidence_presence_are_explicit():
    report = load_report()
    evidence = []
    for sample in report["samples"]:
        ids = [item["item_id"] for item in sample["required_manifest"]]
        assert len(ids) == len(set(ids))
        assert any(item["requirement"] == "hard_required" for item in sample["required_manifest"])
        assert any(item["requirement"] == "continuity_required" for item in sample["required_manifest"])
        evidence.extend(item for item in sample["required_manifest"] if item["requirement"] == "evidence_required")
    assert len(evidence) == 11
    assert sum(item["present_in_current_prompt"] for item in evidence) == 4
    assert all(item["review_provenance"] == "human_review" and item["facts"] for item in evidence)


def test_summary_freezes_source_shares_and_non_required_ceiling():
    summary = load_report()["summary"]
    assert summary["mean_total_estimated_tokens"] == 12406.4
    assert summary["min_total_estimated_tokens"] == 10511
    assert summary["max_total_estimated_tokens"] == 14480
    assert summary["top_three_sources"] == [
        {"category": "recent_original", "mean_estimated_tokens": 5127.1},
        {"category": "rag", "mean_estimated_tokens": 3068},
        {"category": "fixed_prompt", "mean_estimated_tokens": 1104.2},
    ]
    assert summary["mean_recent_original_share"] == 0.4133
    assert summary["mean_rag_share"] == 0.2473
    assert summary["mean_provable_duplicate_tokens"] == 0
    assert summary["mean_theoretical_non_required_ceiling_tokens"] == 7754.7
    assert summary["theoretical_non_required_ceiling_share"] == 0.6251
    assert summary["theoretical_non_required_reduction_is_not_a_recommendation"] is True


def test_source_contract_and_phase4_entry_are_explicit():
    report = load_report()
    required_fields = {"producer", "storage", "injection", "requirement", "trimmable"}
    assert all(required_fields <= set(contract) for contract in report["source_contracts"].values())
    assert report["duplicate_status"].startswith("per sample")
    assert report["non_injected_computed_inputs"]["style_structured"]
    assert report["phase4_entry"]["eligible_for_shadow_context_broker_implementation"] is True
    assert report["context_manager_contract"].startswith("most recent 3 subsection originals")
