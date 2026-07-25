import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / "reports" / "handover-contract-v2-real-demo.json"


def _report():
    return json.loads(REPORT.read_text(encoding="utf-8"))


def test_real_demo_accounts_for_all_four_truncated_calls():
    report = _report()
    calls = report["handover_calls"]
    totals = report["handover_totals"]

    assert len(calls) == 4
    assert all(call["finish_reason"] == "length" for call in calls)
    assert all(call["output_tokens"] == 600 for call in calls)
    assert all(call["execution_status"] == "error" for call in calls)
    assert sum(call["input_tokens"] for call in calls) == totals["input_tokens"]
    assert sum(call["output_tokens"] for call in calls) == totals["output_tokens"]
    assert totals["known_tokens"] == totals["input_tokens"] + totals["output_tokens"]
    assert totals["typed_contract_count"] == 0


def test_real_demo_does_not_confuse_fail_open_with_v2_acceptance():
    report = _report()

    assert report["task"]["status"] == "completed"
    assert report["attribution"]["fail_open_preserved_task_completion"] is True
    assert report["attribution"]["typed_validator_reached"] is False
    assert report["attribution"]["validator_quality_assessable"] is False
    assert report["gates"]["production_promotion"] is False
    assert report["decision"]["default_version"] == "v1"
    assert report["decision"]["v2_promoted"] is False
    assert report["decision"]["rerun_same_configuration"] is False


def test_real_demo_report_preserves_privacy_boundary():
    privacy = _report()["privacy"]

    assert privacy["full_draft_in_report"] is False
    assert privacy["full_handover_in_report"] is False
    assert privacy["prompt_or_messages_in_report"] is False
    assert privacy["database_or_chroma_in_report"] is False
    assert privacy["attachment_log_committed"] is False
    assert privacy["max_public_excerpt_characters"] <= 140
