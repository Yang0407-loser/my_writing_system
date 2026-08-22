from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.utils.llm_client import estimate_tokens
from experiments.style_control.ablation import (
    anonymise_ablation,
    build_ablation_plan,
    estimate_ablation_cost,
    run_ablation_samples,
)
from experiments.style_control.ablation_prompts import (
    build_ablation_style_components,
)
from experiments.style_control.action_style_bridge import (
    compile_action_style_bridge,
    render_action_style_bridge,
)
from experiments.style_control.models import PreparedAblationStyle
from experiments.style_control.review_aggregate import (
    QUALITY_SCORE_FIELDS,
    STYLE_SCORE_FIELDS,
    aggregate_reviews,
)


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "experiments" / "style_control" / "fixtures"
MANIFEST = FIXTURES / "style_contract_ablation_action_bridge_manifest.json"
PREPARED = FIXTURES / "style_contract_ablation_prepared.json"


def _prepared_s3() -> PreparedAblationStyle:
    payload = json.loads(PREPARED.read_text(encoding="utf-8"))
    return PreparedAblationStyle.model_validate(payload["styles"]["S3"])


def _scene() -> dict:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    return payload["scenes"][0]


def _review(public: dict) -> dict:
    return {
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


def test_bridge_is_deterministic_read_only_and_fully_sourced():
    signature = _prepared_s3().style_signature
    first = compile_action_style_bridge(signature=signature, scene=_scene())
    second = compile_action_style_bridge(signature=signature, scene=_scene())

    assert first == second
    assert first.output_hash == second.output_hash
    assert first.content_facts_added == []
    assert first.relationship_mutations_allowed is False
    assert first.deterministic is True
    assert all(item.content_mutation_allowed is False for item in first.constraints)
    assert all(item.source_refs for item in first.constraints)
    assert all(set(item.source_refs) <= set(first.source_refs) for item in first.constraints)
    assert not any(ref.startswith("delta:") and "relationship" in ref for ref in first.source_refs)


def test_relationship_signal_is_expression_only_and_direction_is_not_copied():
    signature = _prepared_s3().style_signature
    scene = _scene()
    scene["approved_state_deltas"].append(
        {
            "delta_id": "approved-rel-1",
            "domain": "relationship_signal",
            "description": "上游批准的关系方向：缓和",
            "source": "scene_contract",
            "read_only": True,
        }
    )
    output = compile_action_style_bridge(signature=signature, scene=scene)
    relationship = next(
        item for item in output.constraints
        if item.constraint_id == "relationship-realization-only"
    )

    assert "delta:approved-rel-1" in relationship.source_refs
    assert "不改方向、主体或内容" in relationship.instruction
    assert "缓和" not in relationship.instruction
    assert "上游批准的关系方向" not in render_action_style_bridge(output)


def test_bridge_rejects_non_action_scene_and_mutable_delta():
    signature = _prepared_s3().style_signature
    non_action = _scene()
    non_action["type"] = "dialogue"
    with pytest.raises(ValueError, match="action scenes"):
        compile_action_style_bridge(signature=signature, scene=non_action)

    mutable = _scene()
    mutable["approved_state_deltas"][0]["read_only"] = False
    with pytest.raises(Exception):
        compile_action_style_bridge(signature=signature, scene=mutable)


def test_d2a_adds_exactly_one_component_and_stays_under_token_cap():
    prepared = _prepared_s3()
    common = {
        "signature": prepared.style_signature,
        "demonstrations": prepared.style_demonstrations,
        "scene_modulation": _scene()["scene_modulation"],
        "scene": _scene(),
    }
    d2 = build_ablation_style_components(arm="D2", **common)
    d2a = build_ablation_style_components(arm="D2A", **common)

    assert set(d2) == {
        "style_signature",
        "scene_modulation",
        "negative_demonstrations",
    }
    assert set(d2a) == set(d2) | {"action_style_bridge"}
    assert all(d2a[key] == value for key, value in d2.items())
    bridge_text = d2a["action_style_bridge"]
    assert estimate_tokens(bridge_text) <= 180
    for forbidden in ("许栀", "沈闻", "三箱", "四箱", "成本大概十五块"):
        assert forbidden not in bridge_text


def test_action_bridge_plan_mock_blinding_budget_and_dynamic_aggregate(tmp_path: Path):
    plan = build_ablation_plan(MANIFEST, PREPARED, tmp_path)
    assert plan["sample_count"] == 6
    assert plan["experiment_enabled_by_default"] is False
    assert {item["arm"] for item in plan["samples"]} == {"D2", "D2A"}
    assert {item["scene_id"] for item in plan["samples"]} == {"SC2"}
    assert len({item["non_style_prompt_hash"] for item in plan["samples"]}) == 1

    by_arm = {
        arm: json.loads(
            Path(next(item for item in plan["samples"] if item["arm"] == arm)["prompt_path"])
            .read_text(encoding="utf-8")
        )
        for arm in ("D2", "D2A")
    }
    d2_keys = set(by_arm["D2"]["component_telemetry"])
    d2a_keys = set(by_arm["D2A"]["component_telemetry"])
    assert d2a_keys == d2_keys | {"action_style_bridge"}

    run_ablation_samples(tmp_path, backend="mock")
    public = anonymise_ablation(tmp_path, seed=20260730)
    assert len(public["samples"]) == 6
    assert len(public["pairs"]) == 3
    public_text = json.dumps(public, ensure_ascii=False)
    for secret in ("D2", "D2A", "sample_id", "action_bridge"):
        assert secret not in public_text

    template = json.loads((tmp_path / "blind-review-template.json").read_text(encoding="utf-8"))
    assert [row["blind_id"] for row in template["samples"]] == [
        row["blind_id"] for row in public["samples"]
    ]
    assert [row["pair_id"] for row in template["pairs"]] == [
        row["pair_id"] for row in public["pairs"]
    ]

    review_dir = tmp_path / "reviews"
    review_dir.mkdir()
    (review_dir / "reviewer-01.json").write_text(
        json.dumps(_review(public), ensure_ascii=False),
        encoding="utf-8",
    )
    aggregate = aggregate_reviews(tmp_path)
    assert aggregate["pair_baseline_arm"] == "D2"
    assert set(aggregate["by_arm"]) == {"D2", "D2A"}
    assert set(aggregate["paired_vs_baseline"]) == {"D2A"}

    budget = estimate_ablation_cost(tmp_path)
    assert budget["planned_real_calls"] == 6
    assert budget["expected_output_tokens"] == 6000


def test_real_calls_remain_disabled_by_default(tmp_path: Path):
    build_ablation_plan(MANIFEST, PREPARED, tmp_path)
    with pytest.raises(PermissionError, match="disabled"):
        run_ablation_samples(tmp_path, backend="llm")
