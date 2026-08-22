import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / "reports" / "phase4-batch1-context-broker-shadow.json"


def load_report():
    return json.loads(REPORT.read_text(encoding="utf-8"))


def test_phase4_batch1_report_has_ten_real_filtered_retrievals():
    report = load_report()
    assert len(report["samples"]) == 10
    assert len(report["retrieval_runs"]) == 10
    assert all(run["returned"] == 5 for run in report["retrieval_runs"])
    assert all(set(run["filter"]) == {"task_id"} for run in report["retrieval_runs"])
    assert report["offline_llm_calls"] == 0
    assert report["writer_generation_calls"] == 0


def test_budgeted_broker_retains_every_required_gate_and_reduces_tokens():
    report = load_report()
    summary = report["summary"]["budgeted_broker"]
    assert summary["hard_required_retention"] == 1.0
    assert summary["immediate_previous_retention"] == 1.0
    assert summary["handover_retention"] == 1.0
    assert summary["legacy_present_human_evidence"] == {"total": 4, "kept": 4, "retention": 1.0}
    assert summary["baseline_retrieval_ceiling"]["total"] == 7
    assert summary["late_query_required_retention"] == 1.0
    assert summary["reduction_vs_legacy"] >= 0.20
    assert summary["acceptance"]["all_batch1_gates"] is True


def test_every_trace_is_explainable_and_does_not_copy_text():
    report = load_report()
    allowed = {"hard_required", "continuity_required", "evidence_required", "optional_context"}
    for sample in report["samples"]:
        assert sample["writer_legacy_message_hash_unchanged"] is True
        for run in sample["profiles"].values():
            for item in run["items"]:
                assert item["requirement"] in allowed
                assert item["source_id"] and item["text_hash"] and item["injection_position"]
                assert item["keep_reason"] if item["keep"] else item["drop_reason"]
                if item["source_type"] == "rag":
                    assert item["section"] is not None and item["subsection"] is not None
                assert "text" not in item


def test_runtime_trace_contains_no_evaluation_answers_and_remains_shadow():
    report = load_report()
    forbidden = {"must_recall_facts", "gold_sections", "human_relevant", "supports_which_fact"}
    for sample in report["samples"]:
        for run in sample["profiles"].values():
            assert not (forbidden & run.keys())
            for item in run["items"]:
                assert not (forbidden & item.keys())
    assert report["evaluation_loaded_after_all_runtime_selections"] is True
    assert report["production_behavior_changed"] is False
    assert report["decision"]["production_promotion"] is False
    assert report["decision"]["batch2_started"] is False
