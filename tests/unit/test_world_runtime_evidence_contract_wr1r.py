import json

import pytest

from experiments.world_runtime_writer_canary import evidence_contract_wr1r as evidence


def test_gold_contract_binds_all_outputs_and_compiles_exact_spans():
    gold, compiled = evidence.load_and_validate_gold()

    assert len(gold.items) == 8
    assert len(compiled) == 8
    assert {item["sample_id"] for item in compiled} == {
        f"WR1R-{index:02d}" for index in range(1, 9)
    }
    for item in compiled:
        text = (
            evidence.DEFAULT_RUNTIME
            / "private/outputs"
            / f"{item['sample_id']}.txt"
        ).read_text(encoding="utf-8")
        judgments = [
            item["required_event_completed"],
            item["task_evasion"],
            *item["hard_reality_violations"].values(),
        ]
        for judgment in judgments:
            for span in judgment["evidence"]:
                assert text[span["start"]:span["end"]] == span["excerpt"]


def test_gold_contract_rejects_output_hash_drift(tmp_path):
    runtime = tmp_path / "runtime"
    output_dir = runtime / "private/outputs"
    output_dir.mkdir(parents=True)
    for source in (evidence.DEFAULT_RUNTIME / "private/outputs").glob("*.txt"):
        (output_dir / source.name).write_text(
            source.read_text(encoding="utf-8"), encoding="utf-8"
        )
    path = output_dir / "WR1R-01.txt"
    path.write_text(path.read_text(encoding="utf-8") + "漂移", encoding="utf-8")

    with pytest.raises(ValueError, match="output hash mismatch: WR1R-01"):
        evidence.load_and_validate_gold(runtime)


def test_absence_basis_cannot_carry_fabricated_evidence():
    with pytest.raises(ValueError, match="absence judgment"):
        evidence.BinaryJudgment(
            value=False,
            reason_code="invalid",
            basis="full_text_absence",
            evidence=(evidence.EvidenceAnchor(claim="fake", excerpt="fake"),),
        )


def test_proxy_accuracy_exposes_false_positive_false_negative_and_coverage_gap():
    result = evidence.evaluate_proxy_accuracy()
    event = result["proxy_accuracy"]["required_event_completed"]
    violation = result["proxy_accuracy"]["hard_reality_violations"]

    assert event == {
        "tp": 4,
        "fp": 0,
        "fn": 3,
        "tn": 1,
        "precision": 1.0,
        "recall": 0.5714,
        "specificity": 1.0,
        "accuracy": 0.625,
    }
    assert violation == {
        "tp": 0,
        "fp": 1,
        "fn": 1,
        "tn": 10,
        "precision": 0.0,
        "recall": 0.0,
        "specificity": 0.9091,
        "accuracy": 0.8333,
    }
    assert result["proxy_accuracy"]["task_evasion"] == {
        "evaluator_coverage": False,
        "gold_positive_samples": 1,
    }
    assert result["proxy_accuracy"]["unsourced_setting"] == {
        "evaluator_coverage": False,
        "gold_positive_samples": 5,
    }
    assert result["gate"]["passed"] is False
    assert result["decision"] == "evaluator_rebuild_required_before_new_generation"
    assert {tuple((item["sample_id"], item["target"])) for item in result["mismatches"]} == {
        ("WR1R-01", "required_event_completed"),
        ("WR1R-02", "required_event_completed"),
        ("WR1R-02", "storefront_public_open_before_0600"),
        ("WR1R-03", "required_event_completed"),
        ("WR1R-03", "coworker_knows_without_transmission_path"),
    }


def test_fixture_is_valid_json_and_has_no_arm_labels():
    payload = json.loads(evidence.DEFAULT_GOLD.read_text(encoding="utf-8"))
    assert payload["status"] == "posthoc_diagnostic_not_promotion_evidence"
    assert all("arm" not in item for item in payload["items"])
