import hashlib
import json

from experiments.world_runtime_writer_canary import extractor_adversarial_wr2a as adversarial


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_adversarial_fixture_and_runtime_sources_remain_hash_locked():
    preflight = adversarial.verify_lock()

    assert preflight["ready"] is True
    assert preflight["hashes_matched"] is True
    assert preflight["run_policy"]["extractor_execution_count"] == 1
    assert preflight["run_policy"]["same_partition_tuning"] is False
    assert preflight["run_policy"]["state_commit"] is False


def test_frozen_partition_contains_controls_negatives_and_unseen_transitions():
    fixture = _read(adversarial.FIXTURE)
    classes = {item["class"] for item in fixture["cases"]}

    assert fixture["status"] == "frozen_before_first_run_no_same_partition_tuning"
    assert len(fixture["cases"]) == 27
    assert sum(len(item["changes"]) for item in fixture["cases"]) == 20
    assert {"control", "negation", "future_plan", "paraphrase", "unseen_transition"}.issubset(classes)


def test_one_shot_ledger_is_consumed_and_binds_the_result():
    ledger = _read(adversarial.LEDGER)

    assert ledger["status"] == "completed"
    assert ledger["attempt_count_total"] == 1
    assert ledger["transport_retries"] == 0
    assert ledger["model_calls"] == 0
    assert ledger["result_sha256"] == hashlib.sha256(adversarial.RESULT.read_bytes()).hexdigest()


def test_failed_diagnostic_is_reported_without_promotion_or_commit():
    result = _read(adversarial.RESULT)
    evaluation = result["evaluation"]

    assert result["status"] == "adversarial_extractor_diagnostic_failed"
    assert result["decision"] == "hold_scope_generalization_failed"
    assert evaluation["control_matched_changes"] == evaluation["control_expected_changes"] == 3
    assert evaluation["semantic_precision"] == 1.0
    assert evaluation["semantic_recall"] == 0.15
    assert evaluation["invalid_transition_recall"] == 1 / 6
    assert evaluation["empty_delta_correct"] == evaluation["empty_delta_cases"] == 8
    assert evaluation["diagnostic_gate_passed"] is False
    assert result["production_promotion_eligible"] is False
    assert result["state_mutations"] == 0
    assert result["commits"] == 0
    assert result["execution"]["model_calls"] == 0


def test_failure_separates_ontology_gaps_from_paraphrase_gaps():
    result = _read(adversarial.RESULT)
    evaluation = result["evaluation"]

    assert evaluation["unsupported_expected_change_types"] == [
        "clock_state",
        "location_state",
        "publication_state",
        "resignation_delivery",
        "resignation_personal_record",
        "storefront_public_handoff",
    ]
    assert evaluation["class_metrics"]["control"]["recall"] == 1.0
    assert evaluation["class_metrics"]["paraphrase"]["recall"] == 0.0
    assert evaluation["class_metrics"]["unseen_transition"]["recall"] == 0.0

