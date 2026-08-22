import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = ROOT / "reports" / "character-arc-contract-impact-audit.json"


def _report():
    return json.loads(REPORT_PATH.read_text(encoding="utf-8"))


def test_fixed_tasks_and_redelivery_exclusion_are_explicit():
    report = _report()
    assert set(report["task_results"]) == {
        "e7cb9ac2-c76c-44e8-a9de-4d470c238872",
        "b5ddb41c-da52-47a1-a03e-9278a0b2ab12",
    }
    assert report["scope"]["excluded_cost_sample"] == "6d8187a1-8a53-47b3-9d90-1f3e4bdc3961"


def test_report_separates_link_operations_from_causal_evidence():
    totals = _report()["totals"]
    assert totals["legacy_link_operations"] >= totals["legacy_same_section_pairwise_links"]
    assert totals["legacy_same_section_pairwise_links"] > 0
    assert totals["proven_causal_edges"] == 0
    assert totals["v2_legal_edges_from_existing_metadata"] == 0


def test_legacy_items_are_not_promoted_to_hard_or_reclassified_from_reviews():
    report = _report()
    assert report["contract_v2"]["legacy_unclassified_runtime_view"] == "soft_arc_progress"
    assert report["contract_v2"]["legacy_storage_rewritten"] is False
    assert report["totals"]["structurally_proven_hard"] == 0
    assert report["totals"]["unresolved_without_new_inference"] == report["totals"]["legacy_milestones"]


def test_two_detector_chains_are_not_confused():
    report = _report()
    assert report["conclusions"]["arc_post_check_causes_retry"] is False
    post_check = next(item for item in report["production_impact_chain"] if item["stage"] == "post_check")
    pre_check = next(item for item in report["production_impact_chain"] if item["stage"] == "pre_check")
    assert post_check["changes_generation_count"] is False
    assert pre_check["changes_writer_messages"] is True


def test_report_contains_no_private_prose_or_evaluation_answers():
    text = REPORT_PATH.read_text(encoding="utf-8")
    assert "draft_preview" not in text
    assert "defect_evidence" not in text
    assert "human_relevant" not in text
    report = _report()
    assert report["scope"]["private_prose_committed"] is False
    assert report["scope"]["data_source"] == "read-only tasks.db events_json; no review or candidate files"


def test_production_app_does_not_import_tests():
    for path in (ROOT / "app").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "from tests" not in source
        assert "import tests" not in source


def test_default_is_v1_and_demo_is_not_run():
    report = _report()
    assert report["contract_v2"]["default_version"] == "v1"
    assert report["scope"]["writer_llm_calls"] == 0
    assert report["scope"]["new_generation_runs"] == 0
