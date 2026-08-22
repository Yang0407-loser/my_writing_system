from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from experiments.writer_boundary_v12_r341.builder import (
    PINNED_REVIEW_HASHES,
    build,
    validate_pinned_reviews,
)
from experiments.writer_boundary_v12_r341.models import R341IndependentReview


def valid_review() -> dict:
    return {
        "schema_version": "1.2-r3.4.1-independent-review",
        "reviewer_id": "R341-INDEPENDENT-REVIEWER-01",
        "scope": ["R3.4 generation package targeted reaudit"],
        "independence": {
            "independent_fresh_conversation": True,
            "inherited_project_history": False,
            "other_r3_4_reviews_accessed": False,
            "implementation_modified": False,
            "external_or_story_model_called": False,
            "network_requests_sent": False,
        },
        "tests_run": [
            {
                "test_id": "T-01",
                "description": "targeted static audit",
                "result": "pass",
                "evidence": "all targeted checks passed",
            }
        ],
        "findings": [],
        "out_of_scope_observations": [],
        "severity_counts": {"P0": 0, "P1": 0, "P2": 0, "P3": 0},
        "verdict": "pass",
        "recommendation": {
            "capability_probe_layer_build_recommended": True,
            "reason": "No blocking finding was observed.",
        },
        "authorization": {
            "authorized_scope": "independent_r3_4_generation_package_audit_only",
            "capability_probe_layer_build_authorized": False,
            "capability_probe_call_authorized": False,
            "real_generation_authorized": False,
            "model_call_authorized": False,
        },
    }


def test_recommendation_true_does_not_expand_authorization():
    review = R341IndependentReview.model_validate(valid_review())
    assert review.recommendation.capability_probe_layer_build_recommended is True
    assert review.authorization.capability_probe_layer_build_authorized is False


@pytest.mark.parametrize(
    "field",
    [
        "capability_probe_layer_build_authorized",
        "capability_probe_call_authorized",
        "real_generation_authorized",
        "model_call_authorized",
    ],
)
def test_every_authorization_boolean_is_literal_false(field: str):
    raw = valid_review()
    raw["authorization"][field] = True
    with pytest.raises(ValidationError):
        R341IndependentReview.model_validate(raw)


def test_severity_counts_are_mechanical():
    raw = valid_review()
    raw["severity_counts"]["P3"] = 1
    with pytest.raises(ValidationError):
        R341IndependentReview.model_validate(raw)


def test_fail_cannot_recommend_layer_build():
    raw = valid_review()
    raw["findings"] = [
        {
            "finding_id": "F-01",
            "severity": "P1",
            "title": "blocking issue",
            "evidence": ["evidence"],
            "reproduction": ["step"],
            "impact": "unsafe",
            "minimal_fix": ["fix"],
        }
    ]
    raw["severity_counts"]["P1"] = 1
    raw["verdict"] = "fail"
    with pytest.raises(ValidationError):
        R341IndependentReview.model_validate(raw)


def test_historical_reviews_are_hash_pinned():
    assert validate_pinned_reviews() == PINNED_REVIEW_HASHES


def test_builder_emits_machine_schema_and_keeps_calls_disabled(tmp_path: Path):
    output = tmp_path / "output"
    manifest = build(output, tmp_path / "report.md")
    schema = json.loads((output / "review-schema.json").read_text(encoding="utf-8"))
    contract = json.loads((output / "review-contract.json").read_text(encoding="utf-8"))
    assert schema["additionalProperties"] is False
    assert contract["protocol_failure"]["classification"] == "invalid_review_protocol"
    assert manifest["model_calls"] == manifest["network_requests"] == 0
    assert manifest["capability_probe_layer_build_authorized"] is False
    assert manifest["authorized_next_stage"] == "independent_r3_4_1_targeted_reaudit"
