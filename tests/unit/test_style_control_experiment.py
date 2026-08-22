from __future__ import annotations

import json
from pathlib import Path

from experiments.style_control.metrics import compute_metrics, overlap_metrics
from experiments.style_control.models import HISTORICAL_STYLE_FIELDS, StyleContract
from experiments.style_control.prompts import build_style_input
from experiments.style_control.runner import (
    anonymise,
    build_control_plan,
    build_plan,
    compute_control_metrics,
    compute_run_metrics,
    run_samples,
)


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "experiments" / "style_control" / "fixtures"


def _prepared_style() -> dict:
    prepared = json.loads((FIXTURES / "prepared_style_inputs.mock.json").read_text(encoding="utf-8"))
    return prepared["styles"]["S1"]


def test_historical_50d_label_recovers_49_actual_non_metadata_fields():
    assert len(HISTORICAL_STYLE_FIELDS) == 49
    assert len(set(HISTORICAL_STYLE_FIELDS)) == 49
    assert "dialogue_ratio" in HISTORICAL_STYLE_FIELDS
    assert "tension_curve" in HISTORICAL_STYLE_FIELDS


def test_style_contract_fixture_is_valid_and_evidence_backed():
    contract = StyleContract.model_validate(_prepared_style()["style_contract"])
    assert 3 <= len(contract.positive_principles) <= 5
    assert 3 <= len(contract.prohibitions) <= 5
    assert {"dialogue", "action", "introspection"} <= set(contract.scene_adaptation)
    assert contract.evidence


def test_arm_inputs_are_isolated():
    prepared = _prepared_style()
    arm_a = build_style_input("A", prepared, "场景调制")
    arm_b = build_style_input("B", prepared, "场景调制")
    arm_c = build_style_input("C", prepared, "场景调制")
    arm_d = build_style_input("D", prepared, "场景调制")

    assert "style_brief" not in arm_a
    assert "情感" not in arm_a
    assert "控制量" in arm_b
    assert "正例" not in arm_b
    assert prepared["historical_brief"] in arm_c
    assert "historical_profile" not in arm_c
    assert "稳定风格契约" in arm_d
    assert "本场景调制" in arm_d


def test_plan_has_48_unique_samples_and_no_handover(tmp_path: Path):
    run_dir = tmp_path / "run"
    plan = build_plan(
        FIXTURES / "experiment_manifest.json",
        FIXTURES / "prepared_style_inputs.mock.json",
        run_dir,
    )
    assert plan["sample_count"] == 48
    assert len({item["sample_id"] for item in plan["samples"]}) == 48
    assert plan["handover_enabled"] is False
    prompt = json.loads(Path(plan["samples"][0]["prompt_path"]).read_text(encoding="utf-8"))
    rendered = "\n".join(item["content"] for item in prompt["messages"])
    assert "交接笔记" not in rendered
    assert "handover" not in rendered.lower()

    control_plan = build_control_plan(
        FIXTURES / "experiment_manifest.json",
        run_dir,
    )
    assert control_plan["sample_count"] == 64
    assert len(control_plan["dimensions"]) == 8
    assert {
        item["level"] for item in control_plan["samples"]
    } == {"low", "high"}


def test_mock_run_resume_metrics_and_anonymisation(tmp_path: Path):
    run_dir = tmp_path / "run"
    build_plan(
        FIXTURES / "experiment_manifest.json",
        FIXTURES / "prepared_style_inputs.mock.json",
        run_dir,
    )
    first = run_samples(run_dir, "mock")
    second = run_samples(run_dir, "mock")
    assert all(item["status"] == "mock_completed" for item in first["samples"])
    assert all(item["status"] == "mock_completed" for item in second["samples"])

    results = compute_run_metrics(run_dir)
    assert len(results["rows"]) == 48
    assert results["route_decision_allowed"] is False

    public = anonymise(run_dir, seed=20260727)
    assert len(public["samples"]) == 48
    assert len(public["pairs"]) == 36
    assert public["mock_warning"] is True
    assert public["style_identification_answer_is_private"] is True
    assert all(row["blind_id"].startswith("样本-") for row in public["samples"])
    assert all("target_style_code" not in row for row in public["samples"])
    public_text = json.dumps(public, ensure_ascii=False)
    assert "__A__" not in public_text
    assert "实验组A" not in public_text
    private = json.loads((run_dir / "blind-review-key.private.json").read_text(encoding="utf-8"))
    assert {item["arm"] for item in private["samples"]} == {"A", "B", "C", "D"}

    build_control_plan(FIXTURES / "experiment_manifest.json", run_dir)
    run_samples(run_dir, "mock", plan_file="control_run_manifest.json")
    control_results = compute_control_metrics(run_dir)
    assert len(control_results["rows"]) == 64
    assert len(control_results["comparisons"]) == 32
    assert control_results["route_decision_allowed"] is False
    assert control_results["human_required_dimensions"] == [
        "dialogue_tag_style",
        "sentence_opening_style",
    ]


def test_metrics_include_proxies_and_copy_guard():
    reference = "雨一直落在旧书店玻璃上。她把仓库钥匙放在桌面中央。"
    generated = "雨一直落在旧书店玻璃上。她把仓库钥匙放在桌面中央。然后他没有拿。"
    overlap = overlap_metrics(generated, reference)
    assert overlap["exact_copied_sentence_count"] == 2
    metrics = compute_metrics(generated, reference, ["她", "他"])
    for key in (
        "dialogue_ratio",
        "sentence_length_median",
        "mechanical_start_ratio",
        "sensory_terms_per_1k",
        "psychological_exposition_per_1k",
        "adjective_density",
        "adverb_density",
        "metaphor_density",
        "longest_common_contiguous_chars",
    ):
        assert key in metrics
