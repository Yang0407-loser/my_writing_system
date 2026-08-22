from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from experiments.writer_boundary_v12_preflight.contract import (
    allowed_values,
    contract_hash,
    load_contract,
    validate_observed_solution,
)
from experiments.writer_boundary_v12_preflight.models import (
    PreflightReviewRecord,
    SharedDecisionContract,
    SolutionCandidate,
)
from experiments.writer_boundary_v12_preflight.prompts import (
    boundary_maker_prompt_snapshot,
    w0_prompt_snapshot,
    w2_realizer_prompt_snapshot,
)
from experiments.writer_boundary_v12_preflight.runner import (
    AUDIT_ONLY_FIELDS,
    CONTRACT_PATH,
    SCENE_PATH,
    build_preflight,
)


def scene():
    return json.loads(SCENE_PATH.read_text(encoding="utf-8"))


def contract():
    return load_contract(CONTRACT_PATH)


def valid_review():
    ev_true = {"status": True, "evidence_paragraphs": ["P01"], "description": "正文证据"}
    ev_false = {"status": False, "evidence_paragraphs": ["P02"], "description": "未出现该项"}
    return {
        "schema_version": "1.2-preflight",
        "text_id": "T01",
        "hard_checks": {
            "mandatory_events_complete": {**ev_true, "failed_event_ids": []},
            "new_character": ev_false,
            "new_solution": {**ev_false, "candidates": []},
            "relationship_change": ev_false,
            "temporary_ending": ev_true,
            "boundary_fidelity": ev_true,
        },
        "execution_audit": {
            "primary_obligation": ev_true,
            "observed_temporary_solution": {
                "value": "raised_mesh_rack",
                "evidence_paragraphs": ["P03"],
                "description": "校样移到网架",
            },
            "additional_solution_candidates": [],
            "resource_constraint_preserved": ev_true,
            "long_term_problem_unresolved": ev_true,
        },
    }


def test_preflight_reuses_sc4_and_defines_no_new_scene():
    assert contract().scene_id == "SC4"
    assert scene()["scene_id"] == "SC4"


def test_shared_contract_is_strict_frozen_two_defined_options():
    value = contract()
    assert len(value.allowed_values) == 2
    assert all(x.definition and x.allowed_implementation_details for x in value.allowed_values)
    with pytest.raises(ValidationError):
        value.choose_exactly = 2
    bad = value.model_dump(); bad["unexpected"] = True
    with pytest.raises(ValidationError): SharedDecisionContract.model_validate(bad)


def test_w0_and_boundary_maker_share_exact_payload_and_hash():
    value, sc = contract(), scene()
    w0 = w0_prompt_snapshot(sc, value)
    maker = boundary_maker_prompt_snapshot(sc, value, 1)
    assert w0["payload"]["shared_decision_contract"] == maker["payload"]["shared_decision_contract"]
    assert w0["payload"]["decision_contract_hash"] == maker["payload"]["decision_contract_hash"] == contract_hash(value)


def test_both_routes_receive_all_values_and_definitions():
    value, sc = contract(), scene()
    texts = [
        w0_prompt_snapshot(sc, value)["messages"][0]["content"],
        boundary_maker_prompt_snapshot(sc, value, 1)["messages"][0]["content"],
    ]
    for text in texts:
        assert all(v in text for v in allowed_values(value))
        assert all(option.definition in text for option in value.allowed_values)


def test_selection_output_contract_is_exact_scalar_and_two_sided():
    text = boundary_maker_prompt_snapshot(scene(), contract(), 1)["messages"][0]["content"]
    assert '"selected_temporary_solution"' in text
    assert "值必须是字符串" in text and "禁止输出数组" in text
    for value in allowed_values(contract()):
        assert value in text


def test_w0_has_no_selected_value_and_realizer_has_no_unselected_option():
    value, sc = contract(), scene()
    w0 = w0_prompt_snapshot(sc, value)["messages"][0]["content"]
    selected, unselected = allowed_values(value)
    realizer = w2_realizer_prompt_snapshot(sc, selected)["messages"][0]["content"]
    assert "selected_temporary_solution" not in w0
    assert unselected not in realizer
    assert "shared_decision_contract" not in realizer


def test_post_write_audit_fields_never_enter_writer_prompts():
    value, sc = contract(), scene()
    texts = [
        w0_prompt_snapshot(sc, value)["messages"][0]["content"],
        w2_realizer_prompt_snapshot(sc, allowed_values(value)[0])["messages"][0]["content"],
    ]
    for text in texts:
        assert not (AUDIT_ONLY_FIELDS & {field for field in AUDIT_ONLY_FIELDS if field in text})


def test_solution_boundary_requires_two_signals_for_confirmation():
    base = {
        "candidate": "第二保护层",
        "classification": "confirmed_new_solution",
        "evidence_paragraphs": ["P04"],
        "description": "形成独立保护路径",
        "signals": ["adds_independent_protection_layer"],
    }
    with pytest.raises(ValidationError): SolutionCandidate.model_validate(base)
    base["signals"].append("creates_second_disposition_path")
    assert SolutionCandidate.model_validate(base).classification == "confirmed_new_solution"


def test_allowed_detail_cannot_carry_independent_solution_signal():
    with pytest.raises(ValidationError):
        SolutionCandidate.model_validate({
            "candidate": "摆位",
            "classification": "allowed_implementation_detail",
            "evidence_paragraphs": ["P04"],
            "description": "普通摆位",
            "signals": ["adds_resource_that_independently_changes_risk"],
        })


def test_mandatory_failure_requires_event_ids_and_evidence():
    raw = valid_review()
    raw["hard_checks"]["mandatory_events_complete"] = {
        "status": False, "failed_event_ids": [],
        "evidence_paragraphs": ["P05"], "description": "M5 失败",
    }
    with pytest.raises(ValidationError): PreflightReviewRecord.model_validate(raw)
    raw["hard_checks"]["mandatory_events_complete"]["failed_event_ids"] = ["M5"]
    assert PreflightReviewRecord.model_validate(raw).hard_checks.mandatory_events_complete.failed_event_ids == ["M5"]


def test_new_solution_status_must_match_confirmed_candidate():
    raw = valid_review()
    raw["hard_checks"]["new_solution"] = {
        "status": True, "evidence_paragraphs": ["P05"], "description": "发现新方案",
        "candidates": [{
            "candidate": "双层保护",
            "classification": "additional_solution_candidate",
            "evidence_paragraphs": ["P05"], "description": "候选",
            "signals": ["adds_independent_protection_layer"],
        }],
    }
    with pytest.raises(ValidationError): PreflightReviewRecord.model_validate(raw)


def test_execution_audit_is_strict_and_observed_solution_is_contract_checked():
    assert PreflightReviewRecord.model_validate(valid_review())
    assert validate_observed_solution("raised_mesh_rack", contract()) == "raised_mesh_rack"
    assert validate_observed_solution("other", contract()) == "other"
    with pytest.raises(ValueError): validate_observed_solution("double_bag", contract())


def test_build_outputs_only_preflight_artifacts_and_no_calls(tmp_path: Path):
    audit = build_preflight(tmp_path)
    assert audit["preflight_pass"] is True
    manifest = json.loads((tmp_path / "mock-manifest.json").read_text(encoding="utf-8"))
    assert manifest["model_calls"] == 0 and manifest["fiction_texts"] == 0
    assert manifest["new_scenes"] == 0 and manifest["route_evidence"] is False
    assert (tmp_path / "snapshots/w0-prompt.snapshot.json").exists()
    assert (tmp_path / "review/preflight-review-schema.json").exists()


def test_input_fixture_hashes_unchanged_during_build(tmp_path: Path):
    audit = build_preflight(tmp_path)
    assert audit["input_integrity"]["unchanged"] is True
    assert audit["historical_v1_1_write_targets"] == []


def test_preflight_runner_has_no_llm_client_dependency():
    source = (
        Path(__file__).resolve().parents[2]
        / "experiments/writer_boundary_v12_preflight/runner.py"
    ).read_text(encoding="utf-8")
    assert "get_llm_client" not in source
    assert "chat_completion" not in source

