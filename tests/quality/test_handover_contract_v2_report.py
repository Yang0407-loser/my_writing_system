import json
from pathlib import Path

from tests.benchmarks.verify_handover_contract_v2_regression import (
    build_regression_summary,
)


ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / "reports" / "handover-contract-v2-minimal-fix.json"


def _report():
    return json.loads(REPORT.read_text(encoding="utf-8"))


def test_fixed_negative_regression_blocks_known_failure_categories():
    result = build_regression_summary()
    assert result["unsupported_psychology_known"] == 3
    assert result["unsupported_psychology_blocked_by_v2_policy"] == 3
    assert result["stale_new_fact_known"] == 1
    assert result["stale_new_fact_blocked_by_v2_policy"] == 1
    assert result["unsourced_arc_pending_known"] == 15
    assert result["unsourced_arc_pending_blocked_by_v2_policy"] == 15
    assert result["outline_boundaries_built"] == 4
    assert result["known_boundary_conflicts_recorded"] == 2
    assert result["writer_or_external_llm_calls"] == 0
    assert result["claim_precision_claimed"] is False


def test_report_keeps_v1_default_and_limits_next_step():
    report = _report()
    assert report["status"] == "engineering_gate_pending_remaining_verification"
    assert report["configuration"]["default"] == "v1"
    assert report["compatibility"]["v1_prompt_changed"] is False
    assert report["compatibility"]["v1_side_effects_changed"] is False
    assert report["model_calls"]["new_calls"] == 0
    assert report["decision"]["production_promoted"] is False
    assert report["decision"]["next_step"] in {
        "run_one_real_v2_demo",
        "stop_for_minimal_fix",
    }


def test_app_does_not_import_tests_or_private_runtime():
    for path in (ROOT / "app").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "tests." not in source
        assert ".handover_content_audit_runtime" not in source


def test_public_artifacts_do_not_embed_private_payloads():
    report = _report()
    privacy = report["privacy"]
    assert privacy["full_draft_in_report"] is False
    assert privacy["full_handover_in_report"] is False
    assert privacy["prompt_or_messages_in_report"] is False
    assert privacy["runtime_committed"] is False
    assert privacy["max_public_excerpt_characters"] <= 140
