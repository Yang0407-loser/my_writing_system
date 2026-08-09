import hashlib
import json

from experiments.world_runtime_writer_canary import evaluator_v2_wr1e as v2


def test_calibration_and_holdout_are_disjoint_and_cover_all_dimensions():
    calibration = json.loads(v2.CALIBRATION.read_text(encoding="utf-8"))
    holdout = json.loads(v2.HOLDOUT.read_text(encoding="utf-8"))
    calibration_texts = {case["text"] for case in calibration["cases"]}
    holdout_texts = {case["text"] for case in holdout["cases"]}

    assert calibration["partition"] == "calibration"
    assert holdout["partition"] == "holdout"
    assert calibration_texts.isdisjoint(holdout_texts)
    for fixture in (calibration, holdout):
        assert len(fixture["cases"]) == 12
        for dimension in (
            "required_event_completed",
            "task_evasion",
            "unsourced_setting",
        ):
            values = {case["expected"][dimension] for case in fixture["cases"]}
            assert values == {False, True}
        violation_values = {
            value
            for case in fixture["cases"]
            for value in case["expected"]["hard_reality_violations"].values()
        }
        assert violation_values == {False, True}


def test_v2_passes_calibration_and_disjoint_holdout_gate():
    calibration = v2.run_benchmark(v2.CALIBRATION)
    holdout = v2.run_benchmark(v2.HOLDOUT)

    assert calibration["gate"]["passed"] is True
    assert holdout["gate"]["passed"] is True
    assert calibration["generation_authorized"] is False
    assert holdout["generation_authorized"] is False
    for result in (calibration, holdout):
        for metric in result["metrics"].values():
            assert metric["precision"] >= 0.9
            assert metric["recall"] >= 0.9


def test_freeze_manifest_binds_evaluator_fixtures_and_reports():
    manifest_path = (
        v2.ROOT
        / "experiments/world_runtime_writer_canary/fixtures/wr1e_evaluator_v2_freeze_manifest.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    paths = {
        "evaluator_source_sha256": v2.ROOT / "experiments/world_runtime_writer_canary/evaluator_v2_wr1e.py",
        "calibration_fixture_sha256": v2.CALIBRATION,
        "holdout_fixture_sha256": v2.HOLDOUT,
        "calibration_report_sha256": v2.ROOT / "reports/world-runtime-wr1e-evaluator-v2-calibration-2026-08-04.json",
        "holdout_report_sha256": v2.ROOT / "reports/world-runtime-wr1e-evaluator-v2-holdout-2026-08-04.json",
    }
    for field, path in paths.items():
        assert hashlib.sha256(path.read_bytes()).hexdigest() == manifest[field]
    assert manifest["holdout_blind_to_implementer"] is False
    assert manifest["generation_authorized"] is False
    assert manifest["production_authorized"] is False


def test_every_positive_v2_result_has_exact_source_evidence():
    fixture = json.loads(v2.HOLDOUT.read_text(encoding="utf-8"))
    for case in fixture["cases"]:
        result = v2.evaluate_text_v2(case["scene_id"], case["text"])
        judgments = [
            result.required_event_completed,
            result.task_evasion,
            result.unsourced_setting,
            *result.hard_reality_violations.values(),
        ]
        for judgment in judgments:
            if judgment.value:
                assert judgment.basis == "evidence"
                assert judgment.evidence
            for span in judgment.evidence:
                assert case["text"][span.start:span.end] == span.excerpt


def test_v2_matches_wr1r_manual_gold_without_changing_frozen_evaluator():
    gold = json.loads(
        (
            v2.ROOT
            / "experiments/world_runtime_writer_canary/fixtures/wr1r_evidence_gold_v1.json"
        ).read_text(encoding="utf-8")
    )
    for item in gold["items"]:
        text = (
            v2.ROOT
            / ".world_runtime_wr1r_canary_runtime/private/outputs"
            / f"{item['sample_id']}.txt"
        ).read_text(encoding="utf-8")
        result = v2.evaluate_text_v2(item["scene_id"], text)
        assert result.required_event_completed.value == item["required_event_completed"]["value"]
        assert {
            key: value.value for key, value in result.hard_reality_violations.items()
        } == {
            key: value["value"] for key, value in item["hard_reality_violations"].items()
        }
        assert result.task_evasion.value == item["task_evasion"]["value"]
        assert result.unsourced_setting.value == bool(item["unsourced_setting_candidates"])
