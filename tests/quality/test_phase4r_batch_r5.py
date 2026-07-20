import json
from pathlib import Path


REPORT = Path("reports/phase4r-batch-r5-boundary-validator.json")
PREDICTOR = Path("tests/benchmarks/phase4r_r5_boundary_validator.py")


def load_report():
    return json.loads(REPORT.read_text(encoding="utf-8"))


def test_r5_is_offline_stopped_and_production_unchanged():
    report = load_report()
    assert report["mode"] == "offline_post_generation_validation"
    assert report["status"] == "completed_stopped"
    assert report["candidate_count"] == 12
    assert report["writer_generation_calls"] == 0
    assert report["llm_calls"] == 0
    assert report["production_behavior_changed"] is False
    assert report["production_messages_hash_unchanged"] is True
    assert report["private_generated_prose_emitted"] is False


def test_predictor_source_cannot_read_evaluation_answers():
    source = PREDICTOR.read_text(encoding="utf-8")
    forbidden = (
        "blind_review.completed.json",
        "evaluation.private.json",
        "defect_evidence",
        "preference",
        "hard_violations",
        "continuity_defects",
        "event_order_defects",
    )
    assert all(name not in source for name in forbidden)


def test_boundary_metrics_and_q7_q8_gates_are_reported_without_duplicate_labels():
    report = load_report()
    assert report["prediction_frozen_before_evaluation"] is True
    assert report["prediction_sha256"] == "fb6e21589d362b9e43f8da00ed8f99709c2d90804a2c72be63e691553baa42c0"
    boundary = report["metrics"]["boundary"]
    assert boundary["tp"] + boundary["fn"] == 3
    assert report["metrics"]["required_event_q7"]["tp"] + report["metrics"]["required_event_q7"]["fn"] == 1
    assert report["gates"]["q7_all_states_correct"] is True
    assert report["gates"]["q8_all_boundary_violations_detected"] is True
    assert report["evidence_traceability_rate"] == 1.0
    assert report["all_mechanical_gates_passed"] is True
    assert report["decision"] == "eligible_to_propose_separately_authorized_validator_shadow_integration"


def test_unsupported_fact_is_exploratory_and_not_a_release_gate():
    report = load_report()
    assert report["unsupported_fact_scope"] == "exploratory_not_a_release_gate"
    assert not any("unsupported" in name for name in report["gates"])


def test_public_report_contains_no_full_private_payload():
    raw = REPORT.read_text(encoding="utf-8")
    for forbidden in ('"messages"', '"candidate_text"', '"output_text"', '"prompt"'):
        assert forbidden not in raw
    for row in load_report()["per_candidate"]:
        for key in ("boundary_evidence", "unsupported_fact_evidence"):
            assert all(len(item["excerpt"]) <= 140 for item in row[key])
