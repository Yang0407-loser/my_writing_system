import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / "reports" / "state-frame-subsection-persistence.json"


def test_state_frame_persistence_report_contract():
    payload = json.loads(REPORT.read_text(encoding="utf-8"))
    assert payload["scope"]["writer_llm_calls"] == 0
    assert payload["scope"]["state_frame_injected_into_writer"] is False
    assert payload["scope"]["database_schema_changed"] is False
    assert payload["fixture"]["before_frames"] == 4
    assert payload["fixture"]["after_frames"] == 4
    assert payload["fixture"]["deltas"] == 4
    assert payload["fixture"]["duplicate_record_ids"] == 0
    assert payload["fixture"]["task_store_recovery_rate"] == 1.0
    assert payload["fixture"]["redis_loss_recovery_rate"] == 1.0
    assert (
        payload["fixture"]["persisted_fact_source_hash_traceability_rate"] == 1.0
    )
    assert payload["compatibility"]["section_end_state_backfilled_to_early_subsections"] is False
    assert payload["quality_limits"]["quality_rules_changed"] is False


def test_production_app_does_not_import_tests():
    for path in (ROOT / "app").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "from tests" not in source
        assert "import tests" not in source
