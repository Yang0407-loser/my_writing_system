import json
from pathlib import Path


REPORT = Path("reports/outline-budget-advisor-v1.json")


def test_fixed_task_report_meets_advisory_acceptance_contract():
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    acceptance = report["acceptance"]
    assert report["provisional_advisory"] is True
    assert report["actual_output_used_as_ideal_label"] is False
    assert report["writer_execution_contract_default"] == "off"
    positive_checks = {
        key: value
        for key, value in acceptance.items()
        if key not in {"writer_messages_changed", "writer_or_llm_calls"}
    }
    assert all(positive_checks.values())
    assert acceptance["writer_messages_changed"] is False
    assert acceptance["writer_or_llm_calls"] == 0
    assert report["fixed_task_result"]["allocated_total"] == 4000
    assert len(report["fixed_task_result"]["subsections"]) == 4


def test_fixed_task_relations_match_required_ordering():
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    subsections = report["fixed_task_result"]["subsections"]
    assert subsections[0]["event_unit_count"] > 1
    assert subsections[1]["time_jump_count"] == 2
    assert subsections[2]["event_unit_count"] <= subsections[1]["event_unit_count"]
    assert subsections[3]["confidence"] != "high"
    assert subsections[3]["recommended_action"] == "review_structure"


def test_report_event_units_are_traceable_without_private_text():
    raw = REPORT.read_text(encoding="utf-8")
    report = json.loads(raw)
    units = [
        unit
        for subsection in report["fixed_task_result"]["subsections"]
        for unit in subsection["event_units"]
    ]
    assert len(units) == 15
    assert all(len(unit) == 3 and len(unit[0]) == 20 and len(unit[2]) == 64 for unit in units)
    assert '"description":' not in raw
    assert '"key_points": [' not in raw
    assert "api_key" not in raw.lower()
