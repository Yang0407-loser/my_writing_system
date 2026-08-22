from __future__ import annotations

import json
from pathlib import Path

import pytest

from experiments.style_root_cause_probe.builder import (
    CONFIG,
    build,
    build_requests,
    load_json,
    validate_config,
)
from experiments.style_root_cause_probe.review import (
    RootCauseBlindReview,
    validate_review_against_public,
)
from experiments.style_root_cause_probe.runner import basic_text_checks


def test_config_freezes_minimal_probe() -> None:
    config = load_json(CONFIG)
    validate_config(config)
    assert set(config["arms"]) == {"G", "L", "W"}
    assert len(config["scenes"]) == 2
    assert config["repeats_per_scene"] == 2


def test_request_queue_has_four_balanced_blocks() -> None:
    queue = build_requests()
    assert len(queue) == 12
    # Repeats are independent samples of the same frozen request, not fake seeds.
    request_hashes = [item["request_sha256"] for item in queue]
    assert len(set(request_hashes)) == 6
    assert all(request_hashes.count(value) == 2 for value in set(request_hashes))
    blocks = {item["block_id"] for item in queue}
    assert len(blocks) == 4
    for block_id in blocks:
        members = [item for item in queue if item["block_id"] == block_id]
        assert {item["arm"] for item in members} == {"G", "L", "W"}
        contracts = [
            json.loads(item["messages"][1]["content"])["fixed_scene_contract"]
            for item in members
        ]
        assert contracts[0] == contracts[1] == contracts[2]


def test_builder_refuses_to_reset_ledger(tmp_path: Path) -> None:
    output = tmp_path / "output"
    report = tmp_path / "report.md"
    manifest = build(output, report)
    assert manifest["generation_requests"] == 12
    with pytest.raises(FileExistsError):
        build(output, report)


def test_basic_checks_keep_length_diagnostic_soft() -> None:
    result = basic_text_checks("正文。", "stop")
    assert result["nonempty"] is True
    assert result["within_target_band_1000_1500"] is False
    assert result["truncation_detected"] is False


def test_review_schema_and_public_coverage() -> None:
    public = {
        "blocks": [
            {
                "public_block_id": f"QB-{block:02d}",
                "candidates": [{"public_text_id": f"Q{(block - 1) * 3 + offset:02d}"} for offset in (1, 2, 3)],
            }
            for block in range(1, 5)
        ],
        "pairs": [],
    }
    for block in range(1, 5):
        ids = [f"Q{(block - 1) * 3 + offset:02d}" for offset in (1, 2, 3)]
        public["pairs"].extend(
            [
                {"public_pair_id": f"QB-{block:02d}-L", "pair_type": "literary", "candidate_ids": ids[:2]},
                {"public_pair_id": f"QB-{block:02d}-W", "pair_type": "web_fiction", "candidate_ids": ids[1:]},
            ]
        )
    assessment = lambda public_id: {
        "public_text_id": public_id,
        "mode_classification": "generic_or_unclear",
        "hard_task_complete": True,
        "unauthorized_event_detected": False,
        "literary_intentionality": 3,
        "commercial_momentum": 3,
        "narrative_intentionality": 3,
        "redundant_explanation": 2,
        "formulaic_expression": 2,
        "prompt_structure_leak": 1,
        "character_motivation_credibility": 3,
        "overall_ai_taste": 2,
        "evidence": [{"paragraph_id": "P01", "explanation": "evidence"}],
    }
    payload = {
        "schema_version": "style-root-cause-blind-review-v0",
        "reviewer_id": "RC-BLIND-REVIEWER-01",
        "scope": {
            "independent_fresh_conversation": True,
            "blind_key_accessed": False,
            "other_reviews_accessed": False,
            "private_material_accessed": False,
            "prompts_or_arm_identity_accessed": False,
            "public_material_only": True,
            "external_or_story_model_called": False,
        },
        "blocks": [
            {
                "public_block_id": block["public_block_id"],
                "assessments": [assessment(item["public_text_id"]) for item in block["candidates"]],
                "block_note": "",
            }
            for block in public["blocks"]
        ],
        "pairs": [
            {
                "public_pair_id": pair["public_pair_id"],
                "target_mode": pair["pair_type"],
                "candidate_ids": pair["candidate_ids"],
                "target_mode_winners": [pair["candidate_ids"][0]],
                "lower_ai_taste_winners": [pair["candidate_ids"][0]],
                "evidence": [{"paragraph_id": "P01", "explanation": "evidence"}],
            }
            for pair in public["pairs"]
        ],
        "cross_block_observations": [],
    }
    review = RootCauseBlindReview.model_validate(payload)
    validate_review_against_public(review, public)
