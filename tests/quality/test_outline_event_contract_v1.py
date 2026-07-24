import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / "reports" / "outline-event-contract-v1.json"


def _report():
    return json.loads(REPORT.read_text(encoding="utf-8"))


def test_contract_report_meets_stop_rule():
    report = _report()
    acceptance = report["acceptance"]
    assert acceptance["source_traceability"] == 1.0
    assert acceptance["duplicate_event_ids"] == 0
    assert acceptance["contract_hash_determinism"] == 1.0
    assert acceptance["current_next_boundary_expressible"] is True
    assert acceptance["legacy_auto_hard_count"] == 0
    assert acceptance["source_change_invalidates_confirmed"] is True
    assert acceptance["advisor_has_second_event_interpreter"] is False
    assert acceptance["writer_production_behavior_changed"] is False


def test_real_fixture_is_structurally_accepted_without_private_text():
    report = _report()
    fixed = report["fixed_real_case"]
    assert fixed["subsection_count"] == 4
    assert fixed["all_contracts_generated"] is True
    assert fixed["s1_1_event_count"] > 1
    assert fixed["s1_2_time_jump_count"] >= 2
    assert fixed["s1_2_shooting_temporal_scope"] == "current"
    assert fixed["s1_3_events_deferred_from_s1_2"] is True
    assert fixed["s1_3_complexity_not_above_s1_2"] is True
    assert fixed["s1_4_requires_structure_review"] is True
    raw = REPORT.read_text(encoding="utf-8")
    assert "完整正文" not in raw
    assert "api_key" not in raw.lower()


def test_writer_and_downstream_modules_do_not_consume_contract():
    forbidden = [
        "app/agents/writer.py",
        "app/writing/scene_spec_provider.py",
        "app/writing/boundary_validator.py",
        "app/writing/story_state_view.py",
    ]
    for relative in forbidden:
        path = ROOT / relative
        if path.exists():
            assert "outline_event_contract" not in path.read_text(encoding="utf-8")


def test_app_does_not_import_tests():
    for path in (ROOT / "app").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "from tests" not in source
        assert "import tests" not in source
