from __future__ import annotations

import json
from pathlib import Path

import pytest

from experiments.style_control import ablation
from experiments.style_control.ablation import (
    anonymise_ablation,
    build_ablation_plan,
    compute_ablation_metrics,
    estimate_ablation_cost,
    run_ablation_samples,
)
from experiments.style_control.ablation_prompts import build_ablation_style_components
from experiments.style_control.dedupe import require_safe_demonstrations, validate_demonstrations
from experiments.style_control.models import (
    PreparedAblationStyle,
    StyleDemonstration,
    StyleDemonstrations,
    StyleSignature,
)
from experiments.style_control.review_aggregate import (
    QUALITY_SCORE_FIELDS,
    STYLE_SCORE_FIELDS,
    aggregate_reviews,
)


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "experiments" / "style_control" / "fixtures"
MANIFEST = FIXTURES / "style_contract_ablation_manifest.json"
REFINEMENT_MANIFEST = FIXTURES / "style_contract_ablation_d2_refinement_manifest.json"
PREPARED = FIXTURES / "style_contract_ablation_prepared.json"


def _prepared_s3() -> PreparedAblationStyle:
    payload = json.loads(PREPARED.read_text(encoding="utf-8"))
    return PreparedAblationStyle.model_validate(payload["styles"]["S3"])


def _rendered(arm: str) -> str:
    prepared = _prepared_s3()
    parts = build_ablation_style_components(
        arm=arm,
        signature=prepared.style_signature,
        demonstrations=prepared.style_demonstrations,
        scene_modulation="测试场景调制",
    )
    return "\n".join(parts.values())


def test_style_signature_schema_and_s3_revision():
    signature = _prepared_s3().style_signature
    assert 5 <= len(signature.active_dimensions) <= 8
    assert "dialogue_function" in signature.active_dimensions
    assert "对话承担主要情节信息和关系变化" in signature.dialogue_function
    assert "反问、打断、答非所问和省略" in signature.dialogue_turn_pattern
    assert any("S1" in item and "动作和沉默" in item for item in signature.discriminators)
    assert any("S2" in item and "感官和意象" in item for item in signature.discriminators)

    invalid = signature.model_dump()
    invalid["unexpected"] = "not allowed"
    with pytest.raises(Exception):
        StyleSignature.model_validate(invalid)


def test_ablation_arm_component_isolation_and_evidence_exclusion():
    d0, d1, d2, d3, f0 = (_rendered(arm) for arm in ("D0", "D1", "D2", "D3", "F0"))
    assert "StyleSignature" in d0
    assert "正向示例" not in d0 and "安全反例" not in d0 and "错误原因" not in d0
    assert "安全正向示例" in d1 and "安全反例" not in d1 and "错误原因" not in d1
    assert "安全正向示例" not in d2 and "安全反例" in d2 and "错误原因" in d2
    assert "安全正向示例" in d3 and "安全反例" in d3 and "错误原因" in d3
    assert "StyleSignature" not in f0 and "SceneModulation" not in f0
    assert "安全正向示例" in f0 and "安全反例" not in f0
    evidence_excerpt = _prepared_s3().evidence.items[0].excerpt
    assert all(evidence_excerpt not in text for text in (d0, d1, d2, d3, f0))


def test_d2r_keeps_error_patterns_without_complete_negative_prose():
    prepared = _prepared_s3()
    d2 = _rendered("D2")
    d2r = _rendered("D2R")
    negative_text = prepared.style_demonstrations.negative_demonstrations[0].text
    negative_reason = prepared.style_demonstrations.negative_reasons[0]

    assert "StyleSignature" in d2r and "SceneModulation" in d2r
    assert "应避免的错误模式" in d2r
    assert negative_reason in d2r
    assert negative_text in d2
    assert negative_text not in d2r
    assert "安全正向示例" not in d2r
    assert "安全反例" not in d2r


def test_plan_has_20_samples_full_prompts_telemetry_and_invariant_non_style(tmp_path: Path):
    plan = build_ablation_plan(MANIFEST, PREPARED, tmp_path)
    assert plan["sample_count"] == 20
    assert plan["experiment_enabled_by_default"] is False
    assert plan["production_behavior_changed"] is False
    assert len({sample["sample_id"] for sample in plan["samples"]}) == 20
    assert {sample["arm"] for sample in plan["samples"]} == {"D0", "D1", "D2", "D3", "F0"}
    for scene_id in ("SC1", "SC2"):
        hashes = {
            sample["non_style_prompt_hash"]
            for sample in plan["samples"]
            if sample["scene_id"] == scene_id
        }
        assert len(hashes) == 1
    prompt = json.loads(Path(plan["samples"][0]["prompt_path"]).read_text(encoding="utf-8"))
    assert prompt["messages"]
    assert prompt["evidence_included"] is False
    assert all(
        {"characters", "estimated_tokens"} <= set(value)
        for value in prompt["component_telemetry"].values()
    )


def test_safe_demonstrations_pass_and_unsafe_exact_sentence_is_blocked():
    prepared = _prepared_s3()
    reference = (FIXTURES / "reference_dialogue_noir.txt").read_text(encoding="utf-8")
    result = require_safe_demonstrations(
        prepared.style_demonstrations,
        reference=reference,
        protected_terms=prepared.evidence.protected_terms,
    )
    assert result["usable"] is True

    unsafe = StyleDemonstrations(
        positive_demonstrations=[
            StyleDemonstration(
                demonstration_id="unsafe-exact",
                mechanism="copy",
                text="招牌坏了一根灯管，亮一下，灭两下。这里继续填充到四十个汉字以满足长度门槛，并验证来自参考正文的完整句复制一定会被安全机制拦截。",
            )
        ]
    )
    with pytest.raises(ValueError, match="unsafe"):
        require_safe_demonstrations(
            unsafe,
            reference=reference,
            protected_terms=prepared.evidence.protected_terms,
        )


def test_unsafe_12gram_and_protected_term_are_blocked():
    reference = "甲乙丙丁戊己庚辛壬癸子丑寅卯。另一句完全不同。"
    demos = StyleDemonstrations(
        positive_demonstrations=[
            StyleDemonstration(
                demonstration_id="unsafe-12",
                mechanism="overlap",
                text="前缀甲乙丙丁戊己庚辛壬癸子丑后缀填充足够多的汉字以达到四十个汉字，并且额外写入独特物件红色怀表。",
            )
        ]
    )
    result = validate_demonstrations(
        demos,
        reference=reference,
        protected_terms=["红色怀表"],
    )
    failures = result["positive"][0]["failures"]
    assert "shared_12gram" in failures
    assert "protected_term" in failures
    assert result["usable"] is False


def test_mock_resume_failed_rerun_metrics_anonymisation_and_budget(tmp_path: Path):
    plan = build_ablation_plan(MANIFEST, PREPARED, tmp_path)
    first = run_ablation_samples(tmp_path, backend="mock")
    second = run_ablation_samples(tmp_path, backend="mock")
    assert all(item["status"] == "mock_completed" for item in first["samples"])
    assert all(item["status"] == "mock_completed" for item in second["samples"])

    result_path = Path(plan["samples"][0]["result_path"])
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["route_evidence"] is False
    assert "copy_safety_metrics" in result
    result["status"] = "failed"
    result_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    rerun = run_ablation_samples(
        tmp_path,
        backend="mock",
        rerun_id=plan["samples"][0]["sample_id"],
    )
    assert rerun["samples"][0]["status"] == "mock_completed"

    metrics = compute_ablation_metrics(tmp_path)
    assert len(metrics["rows"]) == 20
    assert metrics["route_decision_allowed"] is False
    public = anonymise_ablation(tmp_path)
    assert len(public["samples"]) == 20
    assert len(public["pairs"]) == 16
    public_text = json.dumps(public, ensure_ascii=False)
    for secret in ("D0", "D1", "D2", "D3", "F0", "few-shot", "sample_id"):
        assert secret not in public_text
    private = json.loads((tmp_path / "blind-review-key.private.json").read_text(encoding="utf-8"))
    assert {row["arm"] for row in private["samples"]} == {"D0", "D1", "D2", "D3", "F0"}

    budget = estimate_ablation_cost(tmp_path)
    assert budget["planned_real_calls"] == 20
    assert budget["includes_f0"] is True
    assert budget["estimated_input_tokens"] > 0
    assert budget["expected_output_tokens"] == 24000
    assert budget["resume_supported"] is True


def test_real_backend_requires_explicit_enable(tmp_path: Path):
    build_ablation_plan(MANIFEST, PREPARED, tmp_path)
    with pytest.raises(PermissionError, match="disabled"):
        run_ablation_samples(tmp_path, backend="llm")


def test_d2_refinement_plan_mock_pairs_and_budget(tmp_path: Path):
    plan = build_ablation_plan(REFINEMENT_MANIFEST, PREPARED, tmp_path)
    assert plan["sample_count"] == 6
    assert plan["experiment_enabled_by_default"] is False
    assert {sample["arm"] for sample in plan["samples"]} == {"D2", "D2R"}
    assert {sample["scene_id"] for sample in plan["samples"]} == {"SC2"}
    assert len({sample["non_style_prompt_hash"] for sample in plan["samples"]}) == 1

    run_ablation_samples(tmp_path, backend="mock")
    public = anonymise_ablation(tmp_path)
    assert len(public["samples"]) == 6
    assert len(public["pairs"]) == 3
    public_text = json.dumps(public, ensure_ascii=False)
    assert "D2" not in public_text and "D2R" not in public_text
    template = json.loads((tmp_path / "blind-review-template.json").read_text(encoding="utf-8"))
    assert [row["blind_id"] for row in template["samples"]] == [
        row["blind_id"] for row in public["samples"]
    ]
    assert [row["pair_id"] for row in template["pairs"]] == [
        row["pair_id"] for row in public["pairs"]
    ]

    private = json.loads((tmp_path / "blind-review-key.private.json").read_text(encoding="utf-8"))
    assert {row["arm"] for row in private["samples"]} == {"D2", "D2R"}
    assert all(
        {row["option_1_arm"], row["option_2_arm"]} == {"D2", "D2R"}
        for row in private["pairs"]
    )

    budget = estimate_ablation_cost(tmp_path)
    assert budget["planned_real_calls"] == 6
    assert budget["includes_f0"] is False
    assert budget["expected_output_tokens"] == 6000


def test_d2_refinement_review_aggregate_accepts_dynamic_arms(tmp_path: Path):
    build_ablation_plan(REFINEMENT_MANIFEST, PREPARED, tmp_path)
    run_ablation_samples(tmp_path, backend="mock")
    public = anonymise_ablation(tmp_path)
    review = {
        "reviewer_id": "test-reviewer",
        "review_scope": {
            "independent_blind_review": True,
            "private_key_accessed": False,
            "other_reviews_accessed": False,
        },
        "samples": [
            {
                "blind_id": row["blind_id"],
                "style_choice": "S3",
                "s3_closeness": 4,
                "style_scores": {field: 4 for field in STYLE_SCORE_FIELDS},
                "quality_scores": {field: 4 for field in QUALITY_SCORE_FIELDS},
                "hard_flags": {
                    "plot_or_character_error": False,
                    "core_task_miss": False,
                    "severe_prompt_conflict": False,
                },
                "hard_error_evidence": "",
                "comment": "",
            }
            for row in public["samples"]
        ],
        "pairs": [
            {
                "pair_id": row["pair_id"],
                "closer_to_s3": "tie",
                "better_quality": "tie",
                "confidence": 3,
                "comment": "",
            }
            for row in public["pairs"]
        ],
    }
    review_dir = tmp_path / "reviews"
    review_dir.mkdir()
    (review_dir / "reviewer-01.json").write_text(
        json.dumps(review, ensure_ascii=False),
        encoding="utf-8",
    )

    aggregate = aggregate_reviews(tmp_path)
    assert set(aggregate["by_arm"]) == {"D2", "D2R"}
    assert aggregate["pair_baseline_arm"] == "D2"
    assert set(aggregate["paired_vs_baseline"]) == {"D2R"}
    assert aggregate["costs"]["D2R"]["actual_input_token_delta_vs_baseline"] < 0


def test_length_finish_reason_is_preserved(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    plan = build_ablation_plan(MANIFEST, PREPARED, tmp_path)
    plan["samples"] = plan["samples"][:1]
    (tmp_path / "contract_ablation_run_manifest.json").write_text(
        json.dumps(plan, ensure_ascii=False),
        encoding="utf-8",
    )

    class FakeLLM:
        def chat_completion(self, messages, **kwargs):
            kwargs["completion_metadata_sink"](
                {
                    "finish_reason": "length",
                    "input_tokens": 100,
                    "output_tokens": 3000,
                    "latency_seconds": 0.1,
                }
            )
            return "被截断的正文"

    monkeypatch.setattr(ablation, "get_llm_client", lambda: FakeLLM())
    completed = run_ablation_samples(
        tmp_path,
        backend="llm",
        allow_real_calls=True,
    )
    result = json.loads(Path(completed["samples"][0]["result_path"]).read_text(encoding="utf-8"))
    assert result["metadata"]["finish_reason"] == "length"
    assert result["hard_gate_flags"]["truncated"] is True
    assert result["route_evidence"] is True
