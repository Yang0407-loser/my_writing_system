from __future__ import annotations

import json
from pathlib import Path

import pytest

from experiments.writer_boundary_v12_r2.builder import (
    DESIGN_PATH,
    R1_PATH,
    build,
    build_assignments,
    build_join,
    build_matrix,
    materialize_protocol,
)
from experiments.writer_boundary_v12_r2.models import (
    LedgerState,
    PilotBlockOutcome,
    PrivateJoinRow,
)
from experiments.writer_boundary_v12_r2.prompts import BC_INSTRUCTION, build_envelope
from experiments.writer_boundary_v12_r2.runtime import (
    STATE_ARTIFACT,
    STATE_ORDER,
    STATE_ROLE,
    canonical_hash,
    commit_ledger_write_once,
    envelope_hash,
    evaluate_primary_pilot,
    make_ledger_record,
    validate_join,
    validate_public_shell,
    verify_assignment,
    verify_ledger,
)


def protocol():
    return materialize_protocol()


def setup_block():
    value = protocol()
    matrix = build_matrix(value)
    assignments = build_assignments(value, matrix)
    block = matrix["blocks"][0]
    scene = next(item for item in value.scenes if item.scene_id == block["scene_id"])
    ticket, ticket_hash = assignments[0]
    return value, matrix, block, scene, ticket, ticket_hash


def test_r2_is_disabled_fixed_denominator_pilot_without_reruns():
    value = protocol()
    assert value.enabled is False
    assert value.generation_authorized is False
    assert value.pilot_rule.fixed_denominator == 12
    assert value.pilot_rule.silent_reruns_allowed is False
    assert value.pilot_rule.reserve_runs_allowed is False
    assert value.pilot_rule.confirmatory_causal_claims_allowed is False


def test_materialization_is_bound_to_r1_hash_and_repairs_sc9_sc11():
    value = protocol()
    sc9 = next(scene for scene in value.scenes if scene.scene_id == "SC9")
    sc11 = next(scene for scene in value.scenes if scene.scene_id == "SC11")
    sc9_values = {item.value for item in sc9.decision_contract.allowed_values}
    assert "dry_rolling_cart_top" in sc9_values
    assert "single_acid_free_absorbent_sheet" not in sc9_values
    assert all("十分钟" in item.definition for item in sc11.decision_contract.allowed_values)


def test_matrix_has_12_fixed_blocks_and_a_does_not_control_assignment():
    value = protocol()
    matrix = build_matrix(value)
    assert len(matrix["blocks"]) == 12
    assert matrix["fixed_denominator"] == 12
    assert matrix["reruns_allowed"] is False
    assert [b["assigned_option_index_for_b_c"] for b in matrix["blocks"]].count(0) == 6
    assert [b["assigned_option_index_for_b_c"] for b in matrix["blocks"]].count(1) == 6
    assert all(set(block["text_ids"]) == {"A", "B", "C"} for block in matrix["blocks"])


def test_assignment_is_precomputed_and_exactly_bound_to_block_scene_matrix():
    value, matrix, block, scene, ticket, _ = setup_block()
    matrix_hash = canonical_hash(matrix)
    verify_assignment(
        ticket,
        expected_block_id=block["block_id"],
        expected_scene_id=scene.scene_id,
        expected_matrix_hash=matrix_hash,
        scene=scene,
    )
    with pytest.raises(ValueError):
        verify_assignment(
            ticket,
            expected_block_id="BLOCK-02",
            expected_scene_id=scene.scene_id,
            expected_matrix_hash=matrix_hash,
            scene=scene,
        )
    with pytest.raises(ValueError):
        verify_assignment(
            ticket,
            expected_block_id=block["block_id"],
            expected_scene_id=scene.scene_id,
            expected_matrix_hash="0" * 64,
            scene=scene,
        )


def test_arm_a_never_consumes_assignment_and_b_c_share_one():
    value, matrix, block, scene, ticket, ticket_hash = setup_block()
    protocol_hash = canonical_hash(value.model_dump(mode="json"))
    matrix_hash = canonical_hash(matrix)
    a, _ = build_envelope(
        protocol=value,
        protocol_hash=protocol_hash,
        block=block,
        arm="A",
        text_id=block["text_ids"]["A"],
        scene=scene,
        ticket=None,
        assignment_hash=None,
        matrix_hash=matrix_hash,
    )
    b, _ = build_envelope(
        protocol=value,
        protocol_hash=protocol_hash,
        block=block,
        arm="B",
        text_id=block["text_ids"]["B"],
        scene=scene,
        ticket=ticket,
        assignment_hash=ticket_hash,
        matrix_hash=matrix_hash,
    )
    c, _ = build_envelope(
        protocol=value,
        protocol_hash=protocol_hash,
        block=block,
        arm="C",
        text_id=block["text_ids"]["C"],
        scene=scene,
        ticket=ticket,
        assignment_hash=ticket_hash,
        matrix_hash=matrix_hash,
    )
    assert a.assignment_sha256 is None
    assert b.assignment_sha256 == c.assignment_sha256 == ticket_hash


def test_b_c_instruction_is_byte_identical_and_only_payload_differs():
    value, matrix, block, scene, ticket, ticket_hash = setup_block()
    kwargs = {
        "protocol": value,
        "protocol_hash": canonical_hash(value.model_dump(mode="json")),
        "block": block,
        "scene": scene,
        "ticket": ticket,
        "assignment_hash": ticket_hash,
        "matrix_hash": canonical_hash(matrix),
    }
    b, _ = build_envelope(arm="B", text_id=block["text_ids"]["B"], **kwargs)
    c, _ = build_envelope(arm="C", text_id=block["text_ids"]["C"], **kwargs)
    ib = b.messages[0]["content"]["instruction"]
    ic = c.messages[0]["content"]["instruction"]
    assert ib == ic == BC_INSTRUCTION
    assert ib.encode("utf-8") == ic.encode("utf-8")
    rendered_c = json.dumps(c.messages, ensure_ascii=False)
    assert "shared_decision_contract" not in rendered_c
    assert ticket.selected_value not in rendered_c


def test_full_envelope_hash_covers_config_messages_and_assignment():
    value, matrix, block, scene, ticket, ticket_hash = setup_block()
    envelope, digest = build_envelope(
        protocol=value,
        protocol_hash=canonical_hash(value.model_dump(mode="json")),
        block=block,
        arm="B",
        text_id=block["text_ids"]["B"],
        scene=scene,
        ticket=ticket,
        assignment_hash=ticket_hash,
        matrix_hash=canonical_hash(matrix),
    )
    assert digest == envelope_hash(envelope)
    changed = envelope.model_copy(
        update={
            "request_nonce": "changed",
        }
    )
    assert envelope_hash(changed) != digest


def test_ledger_requires_order_role_and_typed_artifact():
    records = []
    for state in STATE_ORDER:
        make_ledger_record(
            records,
            state=state,
            actor_role=STATE_ROLE[state],
            artifact_hashes={STATE_ARTIFACT[state]: canonical_hash({"state": state})},
        )
    verify_ledger(records)
    with pytest.raises(ValueError):
        make_ledger_record(
            [],
            state=LedgerState.ASSIGNMENT_LEDGER_LOCKED,
            actor_role="assignment_builder",
            artifact_hashes={"assignment_ledger_sha256": "a" * 64},
        )
    with pytest.raises(ValueError):
        make_ledger_record(
            [],
            state=LedgerState.DESIGN_LOCKED,
            actor_role="aggregator",
            artifact_hashes={"protocol_sha256": "a" * 64},
        )


def test_ledger_commit_is_write_once(tmp_path: Path):
    records = []
    record = make_ledger_record(
        records,
        state=LedgerState.DESIGN_LOCKED,
        actor_role="designer",
        artifact_hashes={"protocol_sha256": "a" * 64},
    )
    commit_ledger_write_once(tmp_path, record)
    with pytest.raises(FileExistsError):
        commit_ledger_write_once(tmp_path, record)


def test_ledger_commit_checks_external_previous_head(tmp_path: Path):
    records = []
    first = make_ledger_record(
        records,
        state=LedgerState.DESIGN_LOCKED,
        actor_role="designer",
        artifact_hashes={"protocol_sha256": "a" * 64},
    )
    second = make_ledger_record(
        records,
        state=LedgerState.ASSIGNMENT_LEDGER_LOCKED,
        actor_role="assignment_builder",
        artifact_hashes={"assignment_ledger_sha256": "b" * 64},
    )
    with pytest.raises(ValueError):
        commit_ledger_write_once(tmp_path, second)
    commit_ledger_write_once(tmp_path, first)
    commit_ledger_write_once(tmp_path, second)


def test_join_is_one_to_one_randomized_and_requires_content_when_locked():
    matrix = build_matrix(protocol())
    rows = build_join(matrix)
    validate_join(rows, require_content=False)
    assert len({row.private_text_id for row in rows}) == 36
    assert len({row.public_text_id for row in rows}) == 36
    with pytest.raises(ValueError):
        validate_join(rows, require_content=True)
    complete = [
        row.model_copy(update={"content_sha256": canonical_hash({"id": row.private_text_id})})
        for row in rows
    ]
    validate_join(complete, require_content=True)


def test_public_shell_must_match_sealed_join(tmp_path: Path):
    build(tmp_path)
    shell = json.loads(
        (tmp_path / "blind/public-shell.json").read_text(encoding="utf-8")
    )
    rows = [
        PrivateJoinRow.model_validate(value)
        for value in json.loads(
            (tmp_path / "blind/private-join-template.private.json").read_text(
                encoding="utf-8"
            )
        )
    ]
    validate_public_shell(rows, shell)
    shell["blocks"][0]["texts"][0]["public_text_id"] = "PUB-999"
    with pytest.raises(ValueError):
        validate_public_shell(rows, shell)


def _pilot_outcome(block, choice="C", a_present=True, c_present=True):
    hard = {
        "text_present": True,
        "mandatory_events_complete": True,
        "unauthorized_new_character_detected": False,
        "unauthorized_new_solution_detected": False,
        "unauthorized_relationship_change_detected": False,
    }
    return {
        "block_id": block["block_id"],
        "scene_id": block["scene_id"],
        "naturalness": choice,
        "less_template": choice,
        "overall_quality": choice,
        "arm_a_hard": {**hard, "text_present": a_present},
        "arm_c_hard": {**hard, "text_present": c_present},
    }


def test_fixed_denominator_evaluator_has_executable_directional_gate():
    value = protocol()
    blocks = build_matrix(value)["blocks"]
    outcomes = [
        PilotBlockOutcome.model_validate(
            _pilot_outcome(block, choice="C" if index < 8 else "tie")
        )
        for index, block in enumerate(blocks)
    ]
    result = evaluate_primary_pilot(outcomes, value)
    assert result["fixed_denominator"] == 12
    assert result["conclusion"] == "directional_expand_signal"
    assert result["single_composite_score"] is None
    assert result["confirmatory_causal_claim_allowed"] is False


def test_missing_text_is_retained_with_automatic_preference():
    block = build_matrix(protocol())["blocks"][0]
    with pytest.raises(ValueError):
        PilotBlockOutcome.model_validate(
            _pilot_outcome(block, choice="C", a_present=True, c_present=False)
        )
    valid = PilotBlockOutcome.model_validate(
        _pilot_outcome(block, choice="A", a_present=True, c_present=False)
    )
    assert valid.overall_quality == "A"


def test_build_is_zero_call_and_authorizes_only_independent_review(tmp_path: Path):
    audit = build(tmp_path)
    assert audit["r2_static_audit_pass"] is True
    assert audit["model_calls"] == 0
    assert audit["fiction_texts"] == 0
    assert audit["fixed_denominator"] == 12
    assert audit["a_controls_b_c_eligibility"] is False
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["next_stage_authorized"] == "independent_r2_three_party_review"
    assert manifest["generation_package_authorized"] is False


def test_build_preserves_r1_and_r2_inputs(tmp_path: Path):
    before = (DESIGN_PATH.read_bytes(), R1_PATH.read_bytes())
    audit = build(tmp_path)
    assert (DESIGN_PATH.read_bytes(), R1_PATH.read_bytes()) == before
    assert audit["input_integrity"]["unchanged"] is True
    assert audit["historical_r1_write_targets"] == []
    assert audit["historical_v1_2_write_targets"] == []


def test_r2_has_no_model_client_or_generation_call():
    root = Path(__file__).resolve().parents[2]
    source = "\n".join(
        (root / path).read_text(encoding="utf-8")
        for path in (
            "experiments/writer_boundary_v12_r2/builder.py",
            "experiments/writer_boundary_v12_r2/runtime.py",
        )
    )
    assert "get_llm_client" not in source
    assert "chat_completion" not in source
    assert "generate_text" not in source
