from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from experiments.writer_boundary_v12_r1.builder import (
    AUDIT_ONLY_FIELDS,
    PROTOCOL_PATH,
    build,
    build_matrix,
    mock_hash,
    mock_state_chain,
)
from experiments.writer_boundary_v12_r1.models import (
    R1PostWriteReview,
    WorkflowState,
)
from experiments.writer_boundary_v12_r1.prompts import (
    arm_a_request,
    arm_b_request,
    arm_c_request,
)
from experiments.writer_boundary_v12_r1.runtime import (
    append_state,
    build_ticket,
    canonical_hash,
    load_protocol,
    validate_review_against_protocol,
    verify_state_chain,
    verify_ticket_consumption,
)


def protocol():
    return load_protocol(PROTOCOL_PATH)


def valid_review(scene_id: str, observed: str) -> dict:
    evidence = {"paragraph_ids": ["P01"], "description": "对应正文段落"}
    return {
        "schema_version": "1.2-r1-review",
        "text_id": "T001",
        "scene_id": scene_id,
        "paragraph_count": 6,
        "hard_checks": {
            "mandatory_events_complete": True,
            "mandatory_events_evidence": evidence,
            "failed_event_ids": [],
            "unauthorized_new_character_detected": False,
            "unauthorized_new_character_evidence": evidence,
            "unauthorized_new_solution_detected": False,
            "unauthorized_new_solution_evidence": evidence,
            "unauthorized_solution_candidates": [],
            "unauthorized_relationship_change_detected": False,
            "unauthorized_relationship_change_evidence": evidence,
            "ending_remains_temporary": True,
            "ending_evidence": evidence,
            "boundary_contract_satisfied": True,
            "boundary_contract_evidence": evidence,
        },
        "execution_audit": {
            "reviewer_id": "EXEC-01",
            "audited_at": "2026-07-30T12:00:00+08:00",
            "text_sha256": "a" * 64,
            "route_identity_accessed": False,
            "preference_votes_accessed": False,
            "locked": True,
            "observed_decision": {
                "value": observed,
                "evidence": evidence,
            },
        },
    }


def test_protocol_is_disabled_three_arm_and_estimands_are_separated():
    value = protocol()
    assert value.enabled is False
    assert value.generation_authorized is False
    assert value.arms == ["A", "B", "C"]
    assert [item.role for item in value.estimands] == [
        "primary",
        "secondary",
        "descriptive",
    ]
    assert [item.comparison for item in value.estimands] == [
        "A_vs_B",
        "B_vs_C",
        "A_vs_C",
    ]


def test_four_scenes_have_distinct_topologies_and_nonidentical_event_shapes():
    scenes = protocol().scenes
    assert len(scenes) == 4
    assert len({scene.topology for scene in scenes}) == 4
    assert {len(scene.mandatory_events) for scene in scenes} == {5, 6}
    assert all(len(scene.decision_contract.allowed_values) == 2 for scene in scenes)


def test_triplet_matrix_has_12_triplets_36_texts_and_same_seed_per_triplet():
    triplets, identity = build_matrix(protocol())
    rows = [row for row in identity if "text_id" in row]
    assert len(triplets) == 12
    assert len(rows) == 36
    assert len({row["text_id"] for row in rows}) == 36
    for triplet in triplets:
        matching = [
            row for row in rows if row["triplet_id"] == triplet["triplet_id"]
        ]
        assert {row["arm"] for row in matching} == {"A", "B", "C"}
        assert {row["paired_seed"] for row in matching} == {
            triplet["paired_seed"]
        }


def test_triplet_validity_thresholds_are_fail_closed_and_reserves_versioned():
    value = protocol().triplet_validity
    assert value.minimum_valid_triplets == 9
    assert value.minimum_valid_per_scene == 2
    assert value.silent_rerun_allowed is False
    assert value.fail_closed_when_below_threshold is True
    assert value.invalid_triplets_reported_in_full is True


def test_a_b_hold_contract_and_sampling_config_constant():
    value = protocol()
    scene = value.scenes[0]
    ticket, ticket_hash = build_ticket(
        protocol=value,
        triplet_id="TRIPLET-01",
        scene_id=scene.scene_id,
        selected_value=scene.decision_contract.allowed_values[0].value,
        source_a_text_sha256="a" * 64,
        source_a_audit_sha256="b" * 64,
    )
    a = arm_a_request(value, scene, 123)
    b = arm_b_request(value, scene, 123, ticket, ticket_hash)
    assert a["request_config"] == b["request_config"]
    assert (
        a["messages"][0]["content"]["input"]["shared_decision_contract"]
        == b["messages"][0]["content"]["input"]["shared_decision_contract"]
    )
    assert "locked_decision" not in json.dumps(a["messages"], ensure_ascii=False)
    assert ticket.selected_value in json.dumps(b["messages"], ensure_ascii=False)


def test_b_c_consume_same_ticket_but_c_excludes_contract_and_enum():
    value = protocol()
    scene = value.scenes[1]
    option = scene.decision_contract.allowed_values[1]
    ticket, ticket_hash = build_ticket(
        protocol=value,
        triplet_id="TRIPLET-04",
        scene_id=scene.scene_id,
        selected_value=option.value,
        source_a_text_sha256="a" * 64,
        source_a_audit_sha256="b" * 64,
    )
    b = arm_b_request(value, scene, 456, ticket, ticket_hash)
    c = arm_c_request(value, scene, 456, ticket, ticket_hash)
    rendered_c = json.dumps(c["messages"], ensure_ascii=False)
    assert b["consumed_ticket_hash"] == c["consumed_ticket_hash"] == ticket_hash
    assert option.selected_summary in rendered_c
    assert "shared_decision_contract" not in rendered_c
    assert option.value not in rendered_c


def test_private_route_and_audit_metadata_never_enter_messages():
    value = protocol()
    scene = value.scenes[0]
    ticket, ticket_hash = build_ticket(
        protocol=value,
        triplet_id="TRIPLET-01",
        scene_id=scene.scene_id,
        selected_value=scene.decision_contract.allowed_values[0].value,
        source_a_text_sha256="a" * 64,
        source_a_audit_sha256="b" * 64,
    )
    requests = [
        arm_a_request(value, scene, 1),
        arm_b_request(value, scene, 1, ticket, ticket_hash),
        arm_c_request(value, scene, 1, ticket, ticket_hash),
    ]
    for request in requests:
        rendered = json.dumps(request["messages"], ensure_ascii=False)
        assert '"route"' not in rendered
        assert "triplet_id" not in rendered
        assert "mock_selected_index" not in rendered
        assert all(field not in rendered for field in AUDIT_ONLY_FIELDS)


def test_ticket_binding_is_exact_and_fail_closed():
    value = protocol()
    scene = value.scenes[0]
    option = scene.decision_contract.allowed_values[0]
    ticket, ticket_hash = build_ticket(
        protocol=value,
        triplet_id="TRIPLET-01",
        scene_id=scene.scene_id,
        selected_value=option.value,
        source_a_text_sha256="a" * 64,
        source_a_audit_sha256="b" * 64,
    )
    verify_ticket_consumption(ticket, ticket_hash, scene)
    with pytest.raises(ValueError):
        build_ticket(
            protocol=value,
            triplet_id="TRIPLET-01",
            scene_id=scene.scene_id,
            selected_value="unclear",
            source_a_text_sha256="a" * 64,
            source_a_audit_sha256="b" * 64,
        )
    with pytest.raises(ValueError):
        verify_ticket_consumption(ticket, "0" * 64, scene)
    tampered = ticket.model_copy(update={"selected_summary": "被修改"})
    with pytest.raises(ValueError):
        verify_ticket_consumption(
            tampered,
            canonical_hash(tampered.model_dump(mode="json")),
            scene,
        )


def test_dynamic_observed_decision_validation_is_scene_specific():
    value = protocol()
    scene_a, scene_b = value.scenes[:2]
    own = scene_a.decision_contract.allowed_values[0].value
    other_scene = scene_b.decision_contract.allowed_values[0].value
    assert validate_review_against_protocol(
        R1PostWriteReview.model_validate(valid_review(scene_a.scene_id, own)),
        value,
    )
    for audit_value in ("unclear", "other"):
        assert validate_review_against_protocol(
            R1PostWriteReview.model_validate(
                valid_review(scene_a.scene_id, audit_value)
            ),
            value,
        )
    with pytest.raises(ValueError):
        validate_review_against_protocol(
            R1PostWriteReview.model_validate(
                valid_review(scene_a.scene_id, other_scene)
            ),
            value,
        )
    with pytest.raises(ValueError):
        validate_review_against_protocol(
            R1PostWriteReview.model_validate(
                valid_review(scene_a.scene_id, "arbitrary_nonempty")
            ),
            value,
        )


def test_failed_mandatory_ids_are_checked_against_scene():
    value = protocol()
    scene = value.scenes[0]
    raw = valid_review(
        scene.scene_id, scene.decision_contract.allowed_values[0].value
    )
    raw["hard_checks"]["mandatory_events_complete"] = False
    raw["hard_checks"]["failed_event_ids"] = ["M6"]
    review = R1PostWriteReview.model_validate(raw)
    with pytest.raises(ValueError):
        validate_review_against_protocol(review, value)


def test_hard_check_field_names_have_unambiguous_true_semantics():
    raw = valid_review("SC9", "unclear")
    assert R1PostWriteReview.model_validate(raw)
    raw["hard_checks"]["unauthorized_new_solution_detected"] = True
    with pytest.raises(ValidationError):
        R1PostWriteReview.model_validate(raw)


def test_state_machine_requires_order_and_hash_chain():
    records = mock_state_chain("TRIPLET-01")
    verify_state_chain(records)
    with pytest.raises(ValueError):
        append_state(
            [],
            triplet_id="TRIPLET-01",
            state=WorkflowState.A_TEXT_LOCKED,
            actor_id="bad",
            input_hashes=[],
            output_hash=mock_hash("bad"),
        )
    tampered = copy.deepcopy(records)
    tampered[2].previous_record_hash = "0" * 64
    with pytest.raises(ValueError):
        verify_state_chain(tampered)


def test_build_outputs_static_r1_artifacts_and_no_generation(tmp_path: Path):
    audit = build(tmp_path)
    assert audit["r1_static_audit_pass"] is True
    assert audit["model_calls"] == 0
    assert audit["fiction_texts"] == 0
    assert audit["planned_texts"] == 36
    assert audit["all_binding_checks_pass"] is True
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["generation_authorized"] is False
    assert manifest["next_stage_authorized"] == "independent_r1_design_red_team"
    assert (tmp_path / "review/reviewer-semantics.json").exists()
    assert (tmp_path / "dry-run/state-chains.mock.json").exists()


def test_build_does_not_modify_protocol_or_historical_layers(tmp_path: Path):
    before = PROTOCOL_PATH.read_bytes()
    audit = build(tmp_path)
    assert PROTOCOL_PATH.read_bytes() == before
    assert audit["input_integrity"]["unchanged"] is True
    assert audit["historical_v1_2_design_write_targets"] == []
    assert audit["historical_v1_1_write_targets"] == []
    assert audit["preflight_write_targets"] == []


def test_builder_and_runtime_have_no_model_client_dependency():
    root = Path(__file__).resolve().parents[2]
    source = "\n".join(
        (root / path).read_text(encoding="utf-8")
        for path in (
            "experiments/writer_boundary_v12_r1/builder.py",
            "experiments/writer_boundary_v12_r1/runtime.py",
        )
    )
    assert "get_llm_client" not in source
    assert "chat_completion" not in source
    assert "generate_text" not in source

