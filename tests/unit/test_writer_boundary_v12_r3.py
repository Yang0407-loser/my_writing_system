from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from experiments.writer_boundary_v12_r3.builder import (
    CONFIG_PATH,
    R2_MATRIX,
    R2_PROTOCOL,
    build,
    materialize,
)
from experiments.writer_boundary_v12_r3.kernel import (
    FakeProviderGateway,
    STATES,
    TransactionLedger,
    aggregate_primary,
    bind_audit,
    bind_vote,
    build_request,
    create_private_join,
    digest_bytes,
    digest_json,
    make_assignment,
)
from experiments.writer_boundary_v12_r3.models import (
    ExecutionAudit,
    HardOutcome,
    PreferenceVote,
)


def base():
    protocol, matrix = materialize()
    assignments = make_assignment(matrix, protocol)
    return protocol, matrix, assignments


def test_materialized_r3_is_disabled_and_repairs_sc11_sc12():
    protocol, _, _ = base()
    assert protocol["enabled"] is False
    assert protocol["generation_authorized"] is False
    sc11 = next(item for item in protocol["scenes"] if item["scene_id"] == "SC11")
    sc12 = next(item for item in protocol["scenes"] if item["scene_id"] == "SC12")
    assert {item["value"] for item in sc11["decision_contract"]["allowed_values"]} == {
        "close_north_entry_two_point_log",
        "close_south_entry_two_point_log",
    }
    assert all(
        "透明密封观察盒" in item["definition"]
        for item in sc12["decision_contract"]["allowed_values"]
    )


def test_assignment_is_canonical_and_request_rejects_tampering():
    protocol, matrix, assignments = base()
    envelope, digest = build_request(protocol, matrix, assignments, "BLOCK-01", "B")
    assert digest == digest_json(envelope)
    bad = json.loads(json.dumps(assignments))
    bad["assignments"][0]["selected_value"] = "forged"
    with pytest.raises(ValueError):
        build_request(protocol, matrix, bad, "BLOCK-01", "B")


def test_a_has_no_assignment_and_all_arms_share_boundary_sentence():
    protocol, matrix, assignments = base()
    requests = {
        arm: build_request(protocol, matrix, assignments, "BLOCK-01", arm)[0]
        for arm in ("A", "B", "C")
    }
    assert requests["A"]["assignment_sha256"] is None
    for request in requests.values():
        assert "一旦方案确定，不得改变该内容边界。" in request["messages"][0]["content"]["instruction"]


def test_request_is_bound_to_registered_block_arm_and_text():
    protocol, matrix, assignments = base()
    with pytest.raises(ValueError):
        build_request(protocol, matrix, assignments, "BLOCK-50", "A")
    request, _ = build_request(protocol, matrix, assignments, "BLOCK-01", "C")
    block = matrix["blocks"][0]
    assert request["text_id"] == block["text_ids"]["C"]


def test_fake_gateway_fails_consumed_envelope_mismatch():
    protocol, matrix, assignments = base()
    envelope, digest = build_request(protocol, matrix, assignments, "BLOCK-01", "A")
    assert FakeProviderGateway().consume(envelope, digest).synthetic is True
    with pytest.raises(ValueError):
        FakeProviderGateway().consume(envelope, digest, mismatch=True)


def test_hard_outcome_forbids_missing_text_hard_pass():
    with pytest.raises(ValueError):
        HardOutcome(
            artifact_status="content_missing",
            mandatory_events_complete=True,
            unauthorized_new_character_detected=False,
            unauthorized_new_solution_detected=False,
            unauthorized_relationship_change_detected=False,
        )
    assert HardOutcome(
        artifact_status="content_missing",
        mandatory_events_complete=None,
        unauthorized_new_character_detected=None,
        unauthorized_new_solution_detected=None,
        unauthorized_relationship_change_detected=None,
    )


def test_audit_recomputes_text_request_and_matrix_identity():
    protocol, matrix, assignments = base()
    envelope, request_hash = build_request(
        protocol, matrix, assignments, "BLOCK-01", "A"
    )
    raw = b"SYNTHETIC"
    scene = protocol["scenes"][0]
    audit = ExecutionAudit(
        reviewer_id="R",
        block_id="BLOCK-01",
        scene_id=scene["scene_id"],
        text_id=matrix["blocks"][0]["text_ids"]["A"],
        arm="A",
        request_sha256=request_hash,
        content_sha256=digest_bytes(raw),
        observed_decision=scene["decision_contract"]["allowed_values"][0]["value"],
        hard=HardOutcome(
            artifact_status="present",
            mandatory_events_complete=True,
            unauthorized_new_character_detected=False,
            unauthorized_new_solution_detected=False,
            unauthorized_relationship_change_detected=False,
        ),
    )
    assert bind_audit(
        audit,
        text_bytes=raw,
        envelope=envelope,
        matrix=matrix,
        allowed_values={
            item["value"] for item in scene["decision_contract"]["allowed_values"]
        },
    )
    with pytest.raises(ValueError):
        bind_audit(
            audit,
            text_bytes=b"CHANGED",
            envelope=envelope,
            matrix=matrix,
            allowed_values=set(),
        )


def test_csprng_join_changes_without_injected_entropy_and_is_committed():
    _, matrix, _ = base()
    rows1, commitment1 = create_private_join(matrix)
    rows2, commitment2 = create_private_join(matrix)
    assert commitment1 != commitment2
    assert rows1 != rows2
    fixed1 = create_private_join(matrix, b"x" * 32)
    fixed2 = create_private_join(matrix, b"x" * 32)
    assert fixed1 == fixed2


def test_vote_recomputes_public_content_hashes():
    contents = {"P1": b"A", "P2": b"C"}
    vote = PreferenceVote(
        reviewer_id="R",
        public_block_id="PB1",
        public_a_id="P1",
        public_c_id="P2",
        public_a_content_sha256=digest_bytes(b"A"),
        public_c_content_sha256=digest_bytes(b"C"),
        naturalness="tie",
        less_template="tie",
        overall_quality="tie",
    )
    assert bind_vote(vote, contents)
    with pytest.raises(ValueError):
        bind_vote(vote, {"P1": b"A", "P2": b"changed"})


def test_sqlite_ledger_is_ordered_atomic_and_tamper_evident(tmp_path: Path):
    ledger = TransactionLedger(tmp_path / "ledger.sqlite")
    ledger.commit("DESIGN_LOCKED", {"protocol": b"p"}, {})
    with pytest.raises(ValueError):
        ledger.commit("REQUESTS_LOCKED", {"request": b"r"}, {})
    ledger.commit("ASSIGNMENTS_LOCKED", {"assignments": b"a"}, {})
    ledger.verify()
    with sqlite3.connect(ledger.path) as db:
        db.execute("UPDATE receipts SET receipt_sha256=? WHERE sequence=1", ("0" * 64,))
        db.commit()
    with pytest.raises(ValueError):
        ledger.verify()


def outcomes(matrix, choice="C"):
    return [
        {
            "block_id": block["block_id"],
            "scene_id": block["scene_id"],
            "a_status": "present",
            "c_status": "present",
            "naturalness": choice,
            "less_template": choice,
            "overall_quality": choice,
            "hard_non_degradation": True,
        }
        for block in matrix["blocks"]
    ]


def test_aggregate_requires_exact_matrix_analysis_set():
    _, matrix, _ = base()
    result = aggregate_primary(
        matrix=matrix, locked_matrix_hash=digest_json(matrix), outcomes=outcomes(matrix)
    )
    assert result["conclusion"] == "directional_expand_signal"
    foreign = outcomes(matrix)
    foreign[0]["block_id"] = "BLOCK-50"
    with pytest.raises(ValueError):
        aggregate_primary(
            matrix=matrix, locked_matrix_hash=digest_json(matrix), outcomes=foreign
        )


def test_missing_pairs_cannot_add_positive_evidence():
    _, matrix, _ = base()
    values = outcomes(matrix)
    for item in values[:8]:
        item.update(
            {
                "a_status": "content_missing",
                "c_status": "content_missing",
                "naturalness": "no_evidence",
                "less_template": "no_evidence",
                "overall_quality": "no_evidence",
                "hard_non_degradation": False,
            }
        )
    result = aggregate_primary(
        matrix=matrix, locked_matrix_hash=digest_json(matrix), outcomes=values
    )
    assert result["conclusion"] == "do_not_expand"
    assert result["evaluable_pairs"] == 4


def test_missing_truth_table_rejects_positive_vote():
    _, matrix, _ = base()
    values = outcomes(matrix)
    values[0].update({"c_status": "content_missing", "hard_non_degradation": False})
    with pytest.raises(ValueError):
        aggregate_primary(
            matrix=matrix, locked_matrix_hash=digest_json(matrix), outcomes=values
        )


def test_full_synthetic_build_is_zero_call(tmp_path: Path):
    audit = build(tmp_path)
    assert audit["r3_static_pass"] is True
    assert audit["model_calls"] == 0
    assert audit["fiction_texts"] == 0
    assert audit["transaction_states"] == STATES
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["next_stage_authorized"] == "independent_r3_three_party_review"
    assert manifest["generation_package_authorized"] is False


def test_r3_preserves_r2_inputs(tmp_path: Path):
    before = (CONFIG_PATH.read_bytes(), R2_PROTOCOL.read_bytes(), R2_MATRIX.read_bytes())
    build(tmp_path)
    assert (CONFIG_PATH.read_bytes(), R2_PROTOCOL.read_bytes(), R2_MATRIX.read_bytes()) == before


def test_r3_has_no_real_model_client():
    root = Path(__file__).resolve().parents[2]
    source = "\n".join(
        (root / path).read_text(encoding="utf-8")
        for path in (
            "experiments/writer_boundary_v12_r3/kernel.py",
            "experiments/writer_boundary_v12_r3/builder.py",
        )
    )
    assert "get_llm_client" not in source
    assert "chat_completion" not in source

