import ast
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "app" / "writing" / "subsection_outcome_bundle.py"
SCRIPT = ROOT / "tests" / "benchmarks" / "audit_subsection_outcome_bundle.py"
REPORT = ROOT / "reports" / "subsection-outcome-bundle-v1-coverage.json"


def _report():
    return json.loads(REPORT.read_text(encoding="utf-8"))


def test_production_module_is_read_only_and_does_not_import_tests_or_llm():
    source = MODULE.read_text(encoding="utf-8")
    imports = "\n".join(
        ast.unparse(node)
        for node in ast.walk(ast.parse(source))
        if isinstance(node, (ast.Import, ast.ImportFrom))
    ).lower()
    assert "tests" not in imports
    assert "llm" not in imports
    assert "sqlite" not in imports
    assert "redis" not in imports
    for forbidden in (
        "chat_completion",
        "save_checkpoint",
        "blackboard.set",
        "taskstore",
        "insert into",
        "update ",
        "delete from",
    ):
        assert forbidden not in source.lower()


def test_audit_does_not_read_evaluation_answers_or_write_production_stores():
    source = SCRIPT.read_text(encoding="utf-8").lower()
    for forbidden in (
        "candidate_",
        "arm_mapping",
        "user_review",
        "blind_review",
        "must_recall_facts",
        "gold_sections",
        "human_relevant",
        "chat_completion",
        "blackboard.set",
        "save_checkpoint",
        "insert into",
        "update ",
        "delete from",
    ):
        assert forbidden not in source


def test_real_task_generates_four_deterministic_traceable_bundles():
    report = _report()
    assert report["mode"] == "real_task_read_only_asset_audit"
    assert report["subsection_count"] == 4
    assert report["totals"]["bundles"] == 4
    assert report["totals"]["duplicate_bundle_ids"] == 0
    assert report["totals"]["source_hash_traceability_rate"] == 1.0
    assert report["totals"]["worker_restart_recoverable_source_rate"] == 1.0
    assert report["all_mechanical_gates_passed"] is True
    assert report["mechanical_gates"]["deterministic_hashes"] is True
    assert report["mechanical_gates"]["future_state_backfill_count_zero"] is True
    assert report["mechanical_gates"][
        "current_snapshot_not_promoted_to_delta"
    ] is True


def test_partial_is_not_counted_as_complete_or_subsection_exact():
    report = _report()
    assert report["totals"]["component_instances"] == 20
    assert report["totals"]["available"] == 0
    assert report["totals"]["partial"] == 4
    assert report["totals"]["unavailable"] == 16
    assert report["totals"]["conflicted"] == 0
    assert report["totals"]["subsection_exact_components"] == 0
    assert report["totals"]["subsection_exact_coverage_rate"] == 0.0
    for metrics in report["coverage_by_component"].values():
        assert metrics["complete_coverage_rate"] == 0.0
        assert metrics["subsection_exact_coverage_rate"] == 0.0


def test_coarse_assets_are_exposed_only_as_partial():
    report = _report()
    last = report["bundles"][-1]
    components = {
        item["component_type"]: item for item in last["components"]
    }
    assert components["handover_delta"]["granularity"] == "section_aggregate"
    assert components["character_state_delta"]["granularity"] == (
        "task_final_snapshot"
    )
    assert components["foreshadow_delta"]["granularity"] == (
        "current_store_snapshot"
    )
    assert components["experience_delta"]["granularity"] == "section_aggregate"
    assert all(
        components[name]["availability"] == "partial"
        for name in (
            "handover_delta",
            "character_state_delta",
            "foreshadow_delta",
            "experience_delta",
        )
    )
    assert components["relationship_delta"]["availability"] == "unavailable"
    for bundle in report["bundles"][:-1]:
        assert all(
            component["availability"] == "unavailable"
            for component in bundle["components"]
        )


def test_quality_scope_and_shadow_decision_remain_conservative():
    report = _report()
    eligibility = report["state_frame_eligibility"]
    assert eligibility["reliable_after_sources"] == []
    assert eligibility["handover_continuity"] == "unassessable"
    assert eligibility["character_state_transition"] == "partial"
    assert eligibility["foreshadow_health"] == "unassessable"
    assert eligibility["unavailable_is_writer_failure"] is False
    assert eligibility["quality_truth_claimed"] is False
    assert report["decision"]["shadow_hook_recommended"] is False
    assert report["decision"]["next_step_automatic"] is False


def test_public_report_has_no_private_payload_or_production_effect():
    report = _report()
    integrity = report["production_integrity"]
    assert integrity["production_files_unchanged"] is True
    assert integrity["database_hashes_unchanged"] is True
    assert integrity["blackboard_writes"] == 0
    assert integrity["checkpoint_writes"] == 0
    assert integrity["task_store_writes"] == 0
    assert integrity["database_writes"] == 0
    assert integrity["writer_llm_calls"] == 0
    assert integrity["contains_story_text"] is False
    assert integrity["contains_prompt_or_messages"] is False
    assert integrity["contains_human_evaluation_answers"] is False
    assert report["totals"]["private_content_leak_count"] == 0
    rendered = REPORT.read_text(encoding="utf-8").lower()
    for forbidden in (
        '"description":',
        '"character_a":',
        '"character_b":',
        '"open_threads":',
        '"evidence_excerpt":',
        '"prompt":',
        '"messages":',
        "api_key",
        "bearer ",
    ):
        assert forbidden not in rendered
    verification = report["verification"]
    assert verification["targeted_tests_passed"] == 19
    assert verification["targeted_tests_failed"] == 0
    assert verification["compileall"] == "passed"
    assert verification["historical_phase_3_4_matrix_run"] is False
