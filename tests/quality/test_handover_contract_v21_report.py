import json
from pathlib import Path

from tests.benchmarks.measure_handover_v21_capacity import build_capacity_report


ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / "reports" / "handover-contract-v21-compact-output.json"


def _report():
    return json.loads(REPORT.read_text(encoding="utf-8"))


def test_report_matches_deterministic_capacity_accounting():
    report = _report()
    measured = build_capacity_report()

    assert report["v2_output_cost"]["typical_representative"] == measured["v2_output"]["typical"]
    assert report["v2_output_cost"]["worst_representative"] == measured["v2_output"]["worst_representative"]
    assert report["capacity"]["typical"]["estimated_tokens"] == measured["v21_output"]["typical"]["estimated_tokens"]
    assert report["capacity"]["worst_legal"]["estimated_tokens"] == measured["v21_output"]["worst_legal"]["estimated_tokens"]
    assert report["capacity"]["worst_case_safety_margin_tokens"] >= 100
    assert report["compact_schema"]["output_cap_tokens"] == 600


def test_report_keeps_v1_default_and_does_not_claim_generation_quality():
    report = _report()

    assert report["status"] == "engineering_gate_passed_one_v21_demo_authorized"
    assert report["compatibility"]["default_version"] == "v1"
    assert report["compatibility"]["v1_behavior_changed"] is False
    assert report["compatibility"]["v2_validator_semantics_changed"] is False
    assert report["sealed_regression"]["generation_quality_claimed"] is False
    assert report["scope"]["writer_or_external_llm_calls"] == 0
    assert report["scope"]["real_demo_run"] is False
    assert report["decision"]["production_promoted"] is False


def test_report_preserves_privacy_and_fail_open_boundaries():
    report = _report()

    assert all(value is False for value in report["privacy"].values())
    assert report["fail_open"] == {
        "finish_length_parsed": False,
        "truncated_json_repaired": False,
        "second_model_call": False,
        "writer_retry": False,
        "partial_handover_committed": False,
    }
