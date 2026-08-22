import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / "reports" / "character-arc-projection-v1.json"


def _report():
    return json.loads(REPORT.read_text(encoding="utf-8"))


def test_real_projection_is_traceable_but_not_falsely_authoritative():
    report = _report()
    fixed = report["fixed_real_case"]
    assert fixed["projection_candidate_count"] == 8
    assert fixed["authoritative_candidate_count"] == 0
    assert fixed["hard_candidate_count"] == 0
    assert fixed["source_traceability"] == 1.0
    assert fixed["duplicate_projection_ids"] == 0


def test_projection_does_not_promote_or_connect_events_implicitly():
    rules = _report()["promotion_rules"]
    assert rules["unconfirmed_outline_event_can_be_authoritative"] is False
    assert rules["ordinary_plot_event_auto_becomes_arc"] is False
    assert rules["decision_or_state_transition_auto_becomes_hard"] is False
    assert rules["implicit_event_graph_edges"] == 0


def test_production_remains_isolated_and_v1():
    isolation = _report()["production_isolation"]
    assert isolation["character_manager_imports_projection"] is False
    assert isolation["writer_imports_projection"] is False
    assert isolation["coordinator_imports_projection"] is False
    assert isolation["event_graph_imports_projection"] is False
    assert isolation["character_arc_contract_default"] == "v1"
    assert isolation["database_or_redis_writes"] == 0


def test_report_contains_no_private_story_or_prompt():
    raw = REPORT.read_text(encoding="utf-8")
    for forbidden in ["完整正文", "完整Prompt", "api_key", "sk-"]:
        assert forbidden.lower() not in raw.lower()
