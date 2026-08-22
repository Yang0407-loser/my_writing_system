import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = ROOT / "reports" / "foreshadowing-resolve-chapter-normalization.json"


def _report():
    return json.loads(REPORT_PATH.read_text(encoding="utf-8"))


def test_latest_incident_is_recorded_as_completed_with_review_only_failure():
    report = _report()

    assert report["latest_incident"]["task_id"] == (
        "3530d835-6b1e-4b46-94cc-94fc856c5cb6"
    )
    assert report["latest_incident"]["task_completed"] is True
    assert report["latest_incident"]["draft_and_checkpoint_saved"] is True
    assert report["latest_incident"]["writer_retry_triggered"] is False
    assert report["latest_incident"]["review_health_summary_failed"] is True


def test_report_records_complete_comparison_boundary_and_no_mutation():
    report = _report()

    assert report["status"] == "production_normalization_complete"
    assert report["read_behavior"]["resolve_values_normalized"] is True
    assert report["read_behavior"]["comparison_chapter_normalized"] is True
    assert report["read_behavior"]["raw_sql_resolve_comparisons"] == 0
    assert report["database_actions"] == {
        "schema_changed": False,
        "migration_run": False,
        "historical_rows_rewritten": False,
        "records_deleted": False,
    }
    assert report["runtime_calls"] == {"writer": 0, "llm": 0}
