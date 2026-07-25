import ast
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / "reports" / "subsection-handover-content-validity.json"
MARKDOWN = (
    ROOT / "reports" / "subsection-handover-content-validity-2026-07-25.md"
)
SCRIPT = (
    ROOT / "tests" / "benchmarks" / "audit_subsection_handover_content.py"
)
def _report():
    return json.loads(REPORT.read_text(encoding="utf-8"))


def test_fixed_real_scope_is_four_records_and_three_transitions():
    report = _report()
    assert report["scope"]["handover_records"] == 4
    assert report["scope"]["transitions"] == 3
    assert report["scope"]["second_task_used"] is False
    assert report["scope"]["draft_regenerated"] is False
    assert report["scope"]["writer_or_external_model_calls"] == 0


def test_three_stage_freezes_prevent_future_result_backfill():
    report = _report()
    isolation = report["stage_isolation"]
    assert isolation["stage_a_read_handover"] is False
    assert isolation["stage_a_read_target_drafts"] is False
    assert isolation["stage_b_read_target_drafts"] is False
    assert isolation["stage_c_modified_prior_labels"] is False
    assert len(isolation["stage_a_seal"]) == 64
    assert len(isolation["stage_b_seal"]) == 64
    assert len(isolation["stage_c_seal"]) == 64
    assert len(set(
        (
            isolation["stage_a_seal"],
            isolation["stage_b_seal"],
            isolation["stage_c_seal"],
        )
    )) == 3


def test_claim_statuses_are_disjoint_and_total_74():
    report = _report()
    counts = report["faithfulness"]["support_status_counts"]
    assert counts == {
        "supported": 34,
        "partially_supported": 7,
        "unsupported": 3,
        "ambiguous": 15,
        "unverifiable": 15,
    }
    assert sum(counts.values()) == 74
    assert report["artifact_metrics"]["atomic_claims"] == 74
    assert report["faithfulness"]["assessable_claims"] == 44
    assert report["faithfulness"]["strict_claim_precision"] == 34 / 44


def test_claim_and_source_spans_are_fully_traceable():
    metrics = _report()["artifact_metrics"]
    assert metrics["source_hash_traceability_rate"] == 1.0
    assert metrics["evidence_span_traceability_rate"] == 1.0
    assert metrics["assessable_source_evidence_traceability_rate"] == 1.0


def test_carryover_denominators_do_not_count_other_context_as_handover():
    coverage = _report()["carryover_coverage"]
    assert coverage["critical"]["total"] == 9
    assert coverage["critical"]["covered"] == 4
    assert coverage["critical"]["strict_recall"] == 4 / 9
    assert coverage["critical"]["not_fully_covered_but_available_elsewhere"] == 5
    assert coverage["supporting"]["total"] == 7
    assert coverage["supporting"]["covered"] == 3
    assert coverage["supporting"]["strict_recall"] == 3 / 7
    assert coverage["supporting"]["not_fully_covered_but_available_elsewhere"] == 4


def test_downstream_success_is_not_back_propagated_to_handover():
    continuity = _report()["downstream_continuity"]
    assert continuity["correct_transitions"] == 1
    assert continuity["downstream_correct_without_handover_count"] == 1
    assert continuity["continuity_error_count"] == 2
    assert continuity["unattributable_to_handover_count"] == 2
    assert continuity["handover_conflict_count"] == 0


def test_sidecar_is_not_claimed_as_a_same_section_prompt_consumer():
    chain = _report()["consumer_chain"]
    assert chain["same_section_prev_handover_updated"] is False
    assert chain["sidecar_read_by_prompt_builder"] is False
    assert chain["sidecar_has_production_consumer"] is False
    assert len(chain["transition_injection"]) == 3
    assert all(not item["injected"] for item in chain["transition_injection"])
    assert all(not item["source_ids"] for item in chain["transition_injection"])


def test_failure_decision_and_single_next_step_are_conservative():
    report = _report()
    assert report["status"] == "persistence_accepted_content_not_validated"
    assert report["all_acceptance_gates_passed"] is False
    assert report["faithfulness"]["unsupported_invention_count"] == 3
    assert report["faithfulness"]["stale_state_count"] == 1
    assert report["artifact_metrics"]["boundary_leakage_count"] == 0
    assert report["targeted_human_review"]["required"] is False
    assert report["decision"]["downstream_use_promoted"] is False
    assert report["decision"]["next_step"] == (
        "one_minimal_handover_extractor_contract_fix_only"
    )
    assert report["decision"]["next_step_automatic"] is False


def test_audit_is_read_only_and_has_no_production_import_direction():
    source = SCRIPT.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = "\n".join(
        ast.unparse(node)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
    ).lower()
    assert "agents.writer" not in imports
    assert "taskstore" not in imports
    lowered = source.lower()
    for forbidden in (
        "blackboard.set",
        "save_checkpoint",
        "insert into",
        "update task_history",
        "delete from",
        "writer.run",
        "reviewer",
    ):
        assert forbidden not in lowered
    assert "mode=ro" in source


def test_public_artifacts_do_not_leak_private_payloads_or_secrets():
    report = _report()
    privacy = report["privacy"]
    assert privacy["max_public_excerpt_characters"] <= 140
    assert privacy["full_draft_in_report"] is False
    assert privacy["full_handover_in_report"] is False
    assert privacy["prompt_or_messages_in_report"] is False
    assert privacy["database_or_redis_dump_in_report"] is False
    assert privacy["secret_in_report"] is False
    public = (
        REPORT.read_text(encoding="utf-8")
        + MARKDOWN.read_text(encoding="utf-8")
    ).lower()
    assert re.search(
        r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
        r"[0-9a-f]{4}-[0-9a-f]{12}\b",
        public,
    ) is None
    for forbidden in (
        "api_key=",
        "authorization:",
        "bearer ",
        "begin private key",
    ):
        assert forbidden not in public


def test_report_metric_arithmetic_reconciles_without_private_runtime():
    report = _report()
    counts = report["faithfulness"]["support_status_counts"]
    assessable = (
        counts["supported"]
        + counts["partially_supported"]
        + counts["unsupported"]
    )
    assert report["faithfulness"]["assessable_claims"] == assessable
    assert report["faithfulness"]["strict_claim_precision"] == (
        counts["supported"] / assessable
    )
    assert sum(counts.values()) == report["artifact_metrics"]["atomic_claims"]
    assert sum(report["utility"].values()) == (
        report["artifact_metrics"]["atomic_claims"]
    )
