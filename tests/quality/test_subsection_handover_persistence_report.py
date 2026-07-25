import json
from pathlib import Path


REPORT_JSON = Path("reports/subsection-handover-artifact-v1.json")
REPORT_MD = Path(
    "reports/subsection-handover-artifact-v1-2026-07-25.md"
)
DEMO_DOC = Path("docs/subsection-handover-real-demo-acceptance.md")


def test_public_report_contract_and_privacy():
    report = json.loads(REPORT_JSON.read_text(encoding="utf-8"))
    assert report["status"] == "real_demo_accepted"
    assert report["synthetic_acceptance"]["records"] == 4
    assert report["synthetic_acceptance"]["records_before_commit"] == 0
    assert report["synthetic_acceptance"]["duplicate_record_ids"] == 0
    assert report["synthetic_acceptance"]["source_hash_trace_rate"] == 1.0
    assert report["compatibility"]["handover_llm_calls_added"] == 0
    assert report["compatibility"]["legacy_handover_chain_changed"] is False
    assert report["real_demo"]["executed"] is True
    assert report["real_demo"]["task_status"] == "completed"
    assert report["real_demo"]["records"] == 4
    assert report["real_demo"]["pending"] == 0
    assert report["real_demo"]["errors"] == 0
    assert report["real_demo"]["duplicate_record_ids"] == 0
    assert report["real_demo"]["source_hash_trace_rate"] == 1.0
    assert report["real_demo"]["blackboard_checkpoint_task_store_equal"] is True
    assert report["real_demo"]["task_store_read_only_recovery"] is True
    assert report["real_demo"]["worker_restarted_after_completion"] is True
    assert report["real_demo"]["restart_hashes_stable"] is True
    assert report["real_demo"]["writer_or_llm_calls_added"] == 0

    public = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (REPORT_JSON, REPORT_MD, DEMO_DOC)
    ).lower()
    for forbidden in (
        "api_key=",
        "authorization:",
        "完整正文：",
        "完整 prompt：",
        "private-value",
    ):
        assert forbidden not in public


def test_scope_and_runtime_import_boundaries():
    app_sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in Path("app").rglob("*.py")
    )
    assert "from tests" not in app_sources
    assert "import tests" not in app_sources
    report = json.loads(REPORT_JSON.read_text(encoding="utf-8"))
    assert report["compatibility"]["new_production_consumers"] == 0
    assert "StateFrame injection" in report["out_of_scope"]


def test_plan_records_real_demo_acceptance():
    plan = Path(
        "plans/2026-07-17-context-consistency-refactor.md"
    ).read_text(encoding="utf-8")
    assert "Subsection Handover Artifact V1" in plan
    assert "real_demo_accepted" in plan
