from __future__ import annotations

import inspect
import itertools
import json
import sqlite3
from pathlib import Path

import pytest
from pydantic import ValidationError

from experiments.writer_boundary_v12_r3.kernel import make_assignment
from experiments.writer_boundary_v12_r32.builder import (
    R3_MATRIX,
    R3_PROTOCOL,
    R3_REQUESTS,
    ROSTER,
    _synthetic_audits,
    _synthetic_votes,
    build,
    materialize,
)
from experiments.writer_boundary_v12_r32.kernel import (
    STATES,
    ReceiptLedger,
    assert_public_neutral,
    consensus,
    create_private_map,
    derive_outcomes,
    digest_bytes,
    make_execution_distribution,
    make_preference_distribution,
    unblind_from_ledger,
    validate_audit_against_distribution,
    validate_votes,
)
from experiments.writer_boundary_v12_r32.models import Evidence, NeutralAudit


def synthetic_fixture():
    _, protocol, matrix, _ = materialize()
    assignments = make_assignment(matrix, protocol)
    assignment_by_block = {item["block_id"]: item for item in assignments["assignments"]}
    texts = {}
    for block in matrix["blocks"]:
        scene = next(item for item in protocol["scenes"] if item["scene_id"] == block["scene_id"])
        for arm in ("A", "B", "C"):
            decision = (
                scene["decision_contract"]["allowed_values"][0]["value"]
                if arm == "A"
                else assignment_by_block[block["block_id"]]["selected_value"]
            )
            texts[block["text_ids"][arm]] = (
                f"中性合成段落，观测行动 {decision}。\n\n第二段。"
            ).encode()
    manifest = {
        "texts": [
            {"private_text_id": key, "content_sha256": digest_bytes(raw), "bytes": len(raw)}
            for key, raw in sorted(texts.items())
        ]
    }
    private_map = create_private_map(matrix, manifest, entropy=b"r32-test" * 4)
    execution = make_execution_distribution(private_map, texts, protocol)
    preference = make_preference_distribution(private_map, execution)
    return protocol, matrix, assignments, texts, private_map, execution, preference


def test_pinned_r3_request_corpus_and_history_are_unchanged():
    before = (R3_PROTOCOL.read_bytes(), R3_MATRIX.read_bytes(), R3_REQUESTS.read_bytes())
    materialize()
    assert (R3_PROTOCOL.read_bytes(), R3_MATRIX.read_bytes(), R3_REQUESTS.read_bytes()) == before


def test_evidence_rejects_blank_duplicate_and_bad_shape():
    with pytest.raises(ValidationError):
        Evidence(
            check_id="mandatory_events",
            passed=False,
            paragraph_ids=["P1", "P1"],
            explanation=" ",
            failure_m_ids=["M1", "M1"],
        )
    assert Evidence(
        check_id="mandatory_events",
        passed=False,
        paragraph_ids=["P1"],
        explanation="未完成",
        failure_m_ids=["M1"],
    )


def test_audit_evidence_is_bound_to_public_paragraphs_and_scene_m_catalog():
    _, _, _, _, _, execution, _ = synthetic_fixture()
    audit = _synthetic_audits(execution)[0]
    validate_audit_against_distribution(audit, distribution=execution)
    bad_p = audit.model_copy(
        update={
            "hard_checks": [
                audit.hard_checks[0].model_copy(update={"paragraph_ids": ["P9999"]}),
                *audit.hard_checks[1:],
            ]
        }
    )
    with pytest.raises(ValueError):
        validate_audit_against_distribution(bad_p, distribution=execution)
    failed = Evidence(
        check_id="mandatory_events",
        passed=False,
        paragraph_ids=["P1"],
        explanation="不存在的义务",
        failure_m_ids=["M9999"],
    )
    bad_m = audit.model_copy(update={"hard_checks": [failed, *audit.hard_checks[1:]]})
    with pytest.raises(ValueError):
        validate_audit_against_distribution(bad_m, distribution=execution)


def test_public_packages_have_no_private_values_or_fixed_abc_order():
    _, _, assignments, _, private_map, execution, preference = synthetic_fixture()
    identifiers = (
        {item["private_text_id"] for item in private_map["rows"]}
        | {item["private_block_id"] for item in private_map["rows"]}
        | {item["assignment_id"] for item in assignments["assignments"]}
    )
    arms = [item["arm"] for item in private_map["rows"]]
    assert arms != ["A", "B", "C"] * 12
    assert_public_neutral(execution, private_identifiers=identifiers, private_arm_sequence=arms)
    assert_public_neutral(preference, private_identifiers=identifiers)
    with pytest.raises(ValueError):
        assert_public_neutral({"text": next(iter(identifiers))}, private_identifiers=identifiers)
    with pytest.raises(ValueError):
        assert_public_neutral(
            {"items": []},
            private_identifiers=set(),
            private_arm_sequence=["A", "B", "C"] * 12,
        )


def test_vote_roster_membership_hash_and_exact_coverage():
    _, _, _, _, _, _, preference = synthetic_fixture()
    votes = _synthetic_votes(preference)
    validate_votes(votes, distribution=preference, reviewer_roster=ROSTER["preference_reviewers"])
    changed = votes[0].model_copy(update={"candidate_1_content_sha256": "0" * 64})
    with pytest.raises(ValueError):
        validate_votes(
            [changed, *votes[1:]],
            distribution=preference,
            reviewer_roster=ROSTER["preference_reviewers"],
        )
    foreign = votes[0].model_copy(update={"reviewer_id": "UNLOCKED-REVIEWER"})
    with pytest.raises(ValueError):
        validate_votes(
            [foreign, *votes[1:]],
            distribution=preference,
            reviewer_roster=ROSTER["preference_reviewers"],
        )


def test_consensus_is_permutation_invariant_for_three_way_split():
    results = {
        consensus(list(order))
        for order in itertools.permutations(["A", "C", "tie"])
    }
    assert results == {"tie"}


def test_hard_outcome_is_derived_from_c_audit_not_constant():
    _, matrix, assignments, _, private_map, execution, preference = synthetic_fixture()
    audits = _synthetic_audits(execution)
    votes = _synthetic_votes(preference)
    block_map = private_map["preference_blocks"]
    normalized = []
    for vote in votes:
        block = next(item for item in block_map if item["public_block_id"] == vote.public_block_id)
        normalized.append(
            {
                "reviewer_id": vote.reviewer_id,
                "private_block_id": block["private_block_id"],
                "scene_id": block["scene_id"],
                "naturalness": "tie",
                "less_template": "tie",
                "overall_quality": "tie",
            }
        )
    target_row = next(
        row
        for row in private_map["rows"]
        if row["private_block_id"] == matrix["blocks"][0]["block_id"] and row["arm"] == "C"
    )
    index = next(i for i, audit in enumerate(audits) if audit.public_text_id == target_row["public_text_id"])
    failed = Evidence(
        check_id="mandatory_events",
        passed=False,
        paragraph_ids=["P1"],
        explanation="M1 未完成",
        failure_m_ids=["M1"],
    )
    audits[index] = audits[index].model_copy(
        update={"hard_checks": [failed, *audits[index].hard_checks[1:]]}
    )
    outcomes = derive_outcomes(
        matrix=matrix,
        assignments=assignments,
        private_map=private_map,
        audits=audits,
        normalized_votes=normalized,
    )
    assert outcomes[0]["hard_non_degradation"] is False


def test_ledger_enforces_role_terminal_visibility_and_checkpoint(tmp_path: Path):
    path = tmp_path / "ledger.sqlite"
    ledger = ReceiptLedger(path, ROSTER)
    with pytest.raises(PermissionError):
        ledger.commit(
            "DESIGN_LOCKED",
            actor_id=ROSTER["actors"]["aggregator"],
            role="aggregator",
            objects={"x": (b"x", "private")},
            payload={},
        )
    receipt = ledger.commit(
        "DESIGN_LOCKED",
        actor_id=ROSTER["actors"]["custodian"],
        role="custodian",
        objects={"x": (b"x", "private")},
        payload={},
    )
    ledger.verify(expected_terminal_state="DESIGN_LOCKED", checkpoint_sha256=receipt)
    with pytest.raises(ValueError):
        ledger.verify(expected_terminal_state="AGGREGATED", checkpoint_sha256=receipt)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE objects SET visibility='public'")
        db.commit()
    with pytest.raises(ValueError):
        ledger.verify(expected_terminal_state="DESIGN_LOCKED", checkpoint_sha256=receipt)


def test_unblind_has_no_free_votes_or_locked_states_and_rejects_wrong_terminal(tmp_path: Path):
    signature = inspect.signature(unblind_from_ledger)
    assert "votes" not in signature.parameters
    assert "locked_states" not in signature.parameters
    ledger = ReceiptLedger(tmp_path / "ledger.sqlite", ROSTER)
    receipt = ledger.commit(
        "DESIGN_LOCKED",
        actor_id=ROSTER["actors"]["custodian"],
        role="custodian",
        objects={"role_roster": (json.dumps(ROSTER).encode(), "private")},
        payload={},
    )
    with pytest.raises(ValueError):
        unblind_from_ledger(
            ledger,
            checkpoint_sha256=receipt,
            actor_id=ROSTER["actors"]["identity_custodian"],
        )


def test_full_r32_build_is_zero_call_and_role_specific(tmp_path: Path):
    output = tmp_path / "output"
    result = build(output, tmp_path / "report.md")
    assert result["r3_2_static_pass"] is True
    assert result["transaction_states"] == STATES
    assert result["model_calls"] == result["fiction_texts"] == 0
    assert result["request_mismatch_count"] == 0
    assert result["hard_outcomes_derived_from_locked_audits"] is True
    execution_manifest = json.loads(
        (output / "public/execution-reviewer/distribution-manifest.json").read_text(encoding="utf-8")
    )
    preference_manifest = json.loads(
        (output / "public/preference-reviewer/distribution-manifest.json").read_text(encoding="utf-8")
    )
    assert execution_manifest["recipient_role"] == "execution_auditor"
    assert preference_manifest["recipient_role"] == "preference_reviewer"
    assert not list((output / "public").rglob("*ledger*"))
    assert not list((output / "public").rglob("*map*"))


def test_r32_has_no_real_model_client():
    root = Path(__file__).resolve().parents[2]
    source = "\n".join(
        (root / path).read_text(encoding="utf-8")
        for path in (
            "experiments/writer_boundary_v12_r32/kernel.py",
            "experiments/writer_boundary_v12_r32/builder.py",
        )
    )
    assert "get_llm_client" not in source
    assert "chat_completion" not in source
