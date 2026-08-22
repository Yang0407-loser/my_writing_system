from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from experiments.writer_boundary_v12_r31.builder import (
    R3_MATRIX,
    R3_PROTOCOL,
    build,
    materialize,
)
from experiments.writer_boundary_v12_r31.kernel import (
    STATES,
    assert_public_pack_neutral,
    bind_neutral_audit,
    bind_neutral_vote,
    build_public_packs,
    create_anonymity_map,
    digest_bytes,
    unblind_votes,
)
from experiments.writer_boundary_v12_r31.models import (
    HardCheckEvidence,
    NeutralExecutionAudit,
    NeutralPreferenceVote,
)


def fixture():
    _, protocol, matrix = materialize()
    text_bytes = {
        block["text_ids"][arm]: f"PLACEHOLDER {block['block_id']} {arm}".encode()
        for block in matrix["blocks"]
        for arm in ("A", "B", "C")
    }
    content_hashes = {key: digest_bytes(value) for key, value in text_bytes.items()}
    mapping = create_anonymity_map(matrix, content_hashes, entropy=b"x" * 32)
    execution, preference = build_public_packs(mapping, text_bytes)
    return protocol, matrix, text_bytes, mapping, execution, preference


def checks():
    return [
        HardCheckEvidence(
            check_id=check_id,
            passed=True,
            paragraph_ids=["P1"],
            explanation="evidence",
        )
        for check_id in (
            "mandatory_events",
            "unauthorized_new_character",
            "unauthorized_new_solution",
            "unauthorized_relationship_change",
        )
    ]


def make_votes(mapping, preference, *, selected_arm="C"):
    votes = []
    mapping_blocks = {
        item["public_block_id"]: item for item in mapping["preference_blocks"]
    }
    for reviewer in ("R1", "R2", "R3"):
        for public in preference["blocks"]:
            private = mapping_blocks[public["public_block_id"]]
            selected = (
                "candidate_1" if private["candidate_1_arm"] == selected_arm else "candidate_2"
            )
            votes.append(
                NeutralPreferenceVote(
                    reviewer_id=reviewer,
                    public_block_id=public["public_block_id"],
                    candidate_1_id=public["candidate_1"]["public_text_id"],
                    candidate_2_id=public["candidate_2"]["public_text_id"],
                    candidate_1_content_sha256=public["candidate_1"]["content_sha256"],
                    candidate_2_content_sha256=public["candidate_2"]["content_sha256"],
                    naturalness=selected,
                    less_template=selected,
                    overall_quality=selected,
                )
            )
    return votes


def test_public_schemas_reject_route_fields_and_identity_claims():
    _, _, _, _, execution, preference = fixture()
    item = execution["items"][0]
    payload = {
        "reviewer_id": "R",
        "public_text_id": item["public_text_id"],
        "scene_id": item["scene_id"],
        "content_sha256": item["content_sha256"],
        "observed_decision": "unclear",
        "hard_checks": [value.model_dump() for value in checks()],
        "arm": "A",
    }
    with pytest.raises(ValidationError):
        NeutralExecutionAudit.model_validate(payload)
    payload.pop("arm")
    payload["identity_accessed"] = True
    with pytest.raises(ValidationError):
        NeutralExecutionAudit.model_validate(payload)

    block = preference["blocks"][0]
    vote_payload = {
        "reviewer_id": "R",
        "public_block_id": block["public_block_id"],
        "candidate_1_id": block["candidate_1"]["public_text_id"],
        "candidate_2_id": block["candidate_2"]["public_text_id"],
        "candidate_1_content_sha256": block["candidate_1"]["content_sha256"],
        "candidate_2_content_sha256": block["candidate_2"]["content_sha256"],
        "naturalness": "tie",
        "less_template": "tie",
        "overall_quality": "tie",
        "public_a_id": "leak",
    }
    with pytest.raises(ValidationError):
        NeutralPreferenceVote.model_validate(vote_payload)


def test_failed_hard_check_requires_paragraph_explanation_and_m_id():
    with pytest.raises(ValidationError):
        HardCheckEvidence(
            check_id="mandatory_events",
            passed=False,
            paragraph_ids=["P1"],
            explanation="missing",
        )
    assert HardCheckEvidence(
        check_id="mandatory_events",
        passed=False,
        paragraph_ids=["P2"],
        explanation="missing",
        failure_m_ids=["M1"],
    )


def test_audit_binding_uses_public_id_scene_and_locked_content():
    protocol, _, text_bytes, mapping, execution, _ = fixture()
    item = execution["items"][0]
    audit = NeutralExecutionAudit(
        reviewer_id="R",
        public_text_id=item["public_text_id"],
        scene_id=item["scene_id"],
        content_sha256=item["content_sha256"],
        observed_decision="unclear",
        hard_checks=checks(),
    )
    allowed = {
        scene["scene_id"]: {
            option["value"] for option in scene["decision_contract"]["allowed_values"]
        }
        for scene in protocol["scenes"]
    }
    assert bind_neutral_audit(
        audit,
        anonymity_map=mapping,
        text_bytes=text_bytes,
        allowed_values_by_scene=allowed,
    )
    changed = dict(text_bytes)
    private_id = next(
        row["private_text_id"]
        for row in mapping["rows"]
        if row["public_text_id"] == item["public_text_id"]
    )
    changed[private_id] = b"changed"
    with pytest.raises(ValueError):
        bind_neutral_audit(
            audit,
            anonymity_map=mapping,
            text_bytes=changed,
            allowed_values_by_scene=allowed,
        )


def test_vote_rejects_cross_block_candidate_and_hash_mismatch():
    _, _, _, mapping, execution, preference = fixture()
    block1, block2 = preference["blocks"][:2]
    contents = {
        item["public_text_id"]: item["text"].encode() for item in execution["items"]
    }
    vote = NeutralPreferenceVote(
        reviewer_id="R",
        public_block_id=block1["public_block_id"],
        candidate_1_id=block1["candidate_1"]["public_text_id"],
        candidate_2_id=block2["candidate_2"]["public_text_id"],
        candidate_1_content_sha256=block1["candidate_1"]["content_sha256"],
        candidate_2_content_sha256=block2["candidate_2"]["content_sha256"],
        naturalness="tie",
        less_template="tie",
        overall_quality="tie",
    )
    with pytest.raises(ValueError):
        bind_neutral_vote(vote, anonymity_map=mapping, public_contents=contents)
    valid = make_votes(mapping, preference)[0]
    changed = dict(contents)
    changed[valid.candidate_1_id] = b"changed after lock"
    with pytest.raises(ValueError):
        bind_neutral_vote(valid, anonymity_map=mapping, public_contents=changed)


def test_unblind_is_delayed_and_rejects_duplicate_or_missing_ballots():
    _, _, _, mapping, _, preference = fixture()
    votes = make_votes(mapping, preference)
    with pytest.raises(ValueError):
        unblind_votes(votes, anonymity_map=mapping, locked_states=STATES[:8])
    normalized = unblind_votes(votes, anonymity_map=mapping, locked_states=STATES[:9])
    assert len(normalized) == 36
    assert {item["naturalness"] for item in normalized} == {"C"}
    with pytest.raises(ValueError):
        unblind_votes(votes[:-1], anonymity_map=mapping, locked_states=STATES[:9])
    duplicate = votes[:-1] + [votes[0]]
    with pytest.raises(ValueError):
        unblind_votes(duplicate, anonymity_map=mapping, locked_states=STATES[:9])


def test_different_display_orders_normalize_to_same_arm():
    _, _, text_bytes, mapping1, _, preference1 = fixture()
    _, _, matrix = materialize()
    hashes = {key: digest_bytes(value) for key, value in text_bytes.items()}
    mapping2 = create_anonymity_map(matrix, hashes, entropy=b"y" * 32)
    _, preference2 = build_public_packs(mapping2, text_bytes)
    result1 = unblind_votes(
        make_votes(mapping1, preference1),
        anonymity_map=mapping1,
        locked_states=STATES[:9],
    )
    result2 = unblind_votes(
        make_votes(mapping2, preference2),
        anonymity_map=mapping2,
        locked_states=STATES[:9],
    )
    assert {item["naturalness"] for item in result1 + result2} == {"C"}


def test_public_packs_have_no_private_join_keys():
    _, _, _, _, execution, preference = fixture()
    assert_public_pack_neutral(execution)
    assert_public_pack_neutral(preference)
    with pytest.raises(ValueError):
        assert_public_pack_neutral({"private_text_id": "leak"})


def test_default_anonymity_uses_fresh_csprng():
    _, matrix, text_bytes, _, _, _ = fixture()
    hashes = {key: digest_bytes(value) for key, value in text_bytes.items()}
    assert create_anonymity_map(matrix, hashes) != create_anonymity_map(matrix, hashes)


def test_full_r31_build_is_zero_call_and_exports_no_join(tmp_path: Path):
    output = tmp_path / "output"
    report = tmp_path / "report.md"
    result = build(output, report)
    assert result["r3_1_static_pass"] is True
    assert result["transaction_states"] == STATES
    assert result["model_calls"] == 0
    assert result["fiction_texts"] == 0
    assert not list((output / "public").rglob("*join*"))
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["next_stage_authorized"] == "independent_r3_1_three_party_review"
    assert manifest["generation_package_authorized"] is False


def test_r31_preserves_r3_inputs(tmp_path: Path):
    before = (R3_PROTOCOL.read_bytes(), R3_MATRIX.read_bytes())
    build(tmp_path / "output", tmp_path / "report.md")
    assert (R3_PROTOCOL.read_bytes(), R3_MATRIX.read_bytes()) == before


def test_r31_has_no_real_model_client():
    root = Path(__file__).resolve().parents[2]
    source = "\n".join(
        (root / path).read_text(encoding="utf-8")
        for path in (
            "experiments/writer_boundary_v12_r31/kernel.py",
            "experiments/writer_boundary_v12_r31/builder.py",
        )
    )
    assert "get_llm_client" not in source
    assert "chat_completion" not in source
