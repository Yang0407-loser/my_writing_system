from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .models import (
    AssignmentTicket,
    LedgerRecord,
    LedgerState,
    PrivateJoinRow,
    PilotBlockOutcome,
    RequestEnvelope,
    R2Protocol,
)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def envelope_hash(envelope: RequestEnvelope) -> str:
    return canonical_hash(envelope.model_dump(mode="json"))


def verify_assignment(
    ticket: AssignmentTicket,
    *,
    expected_block_id: str,
    expected_scene_id: str,
    expected_matrix_hash: str,
    scene,
) -> None:
    if ticket.block_id != expected_block_id:
        raise ValueError("assignment block mismatch")
    if ticket.scene_id != expected_scene_id or ticket.scene_id != scene.scene_id:
        raise ValueError("assignment scene mismatch")
    if ticket.matrix_sha256 != expected_matrix_hash:
        raise ValueError("assignment matrix mismatch")
    option = next(
        (
            value
            for value in scene.decision_contract.allowed_values
            if value.value == ticket.selected_value
        ),
        None,
    )
    if option is None:
        raise ValueError("assignment value is not legal")
    if (
        ticket.decision_id != scene.decision_contract.decision_id
        or ticket.selected_definition != option.definition
        or ticket.selected_summary != option.selected_summary
    ):
        raise ValueError("assignment canonical text mismatch")


STATE_ORDER = list(LedgerState)
STATE_ROLE = {
    LedgerState.DESIGN_LOCKED: "designer",
    LedgerState.ASSIGNMENT_LEDGER_LOCKED: "assignment_builder",
    LedgerState.REQUEST_LEDGER_LOCKED: "request_builder",
    LedgerState.ALL_TEXTS_LOCKED: "text_ingestor",
    LedgerState.EXECUTION_AUDITS_LOCKED: "execution_auditor",
    LedgerState.BLIND_JOIN_LOCKED: "blind_pack_builder",
    LedgerState.PREFERENCE_VOTES_LOCKED: "preference_reviewer",
    LedgerState.IDENTITY_UNBLINDED: "identity_custodian",
    LedgerState.AGGREGATED: "aggregator",
}
STATE_ARTIFACT = {
    LedgerState.DESIGN_LOCKED: "protocol_sha256",
    LedgerState.ASSIGNMENT_LEDGER_LOCKED: "assignment_ledger_sha256",
    LedgerState.REQUEST_LEDGER_LOCKED: "request_ledger_sha256",
    LedgerState.ALL_TEXTS_LOCKED: "text_manifest_sha256",
    LedgerState.EXECUTION_AUDITS_LOCKED: "audit_manifest_sha256",
    LedgerState.BLIND_JOIN_LOCKED: "blind_join_sha256",
    LedgerState.PREFERENCE_VOTES_LOCKED: "vote_manifest_sha256",
    LedgerState.IDENTITY_UNBLINDED: "identity_join_sha256",
    LedgerState.AGGREGATED: "aggregate_sha256",
}


def make_ledger_record(
    records: list[LedgerRecord],
    *,
    state: LedgerState,
    actor_role: str,
    artifact_hashes: dict[str, str],
) -> LedgerRecord:
    sequence = len(records)
    if sequence >= len(STATE_ORDER) or state != STATE_ORDER[sequence]:
        raise ValueError("ledger transition out of order")
    if actor_role != STATE_ROLE[state]:
        raise ValueError("actor role not authorized for state")
    required = STATE_ARTIFACT[state]
    if set(artifact_hashes) != {required}:
        raise ValueError("state requires exactly one typed artifact hash")
    if len(artifact_hashes[required]) != 64:
        raise ValueError("artifact hash must be sha256")
    previous = records[-1].record_sha256 if records else None
    unsigned = {
        "schema_version": "1.2-r2-ledger",
        "experiment_id": "writer-boundary-v1-2-r2",
        "sequence": sequence,
        "state": state.value,
        "actor_role": actor_role,
        "artifact_hashes": artifact_hashes,
        "previous_chain_head": previous,
    }
    record = LedgerRecord(**unsigned, record_sha256=canonical_hash(unsigned))
    records.append(record)
    return record


def verify_ledger(records: list[LedgerRecord]) -> None:
    if [record.state for record in records] != STATE_ORDER:
        raise ValueError("ledger incomplete")
    rebuilt: list[LedgerRecord] = []
    for record in records:
        expected = make_ledger_record(
            rebuilt,
            state=record.state,
            actor_role=record.actor_role,
            artifact_hashes=record.artifact_hashes,
        )
        if expected != record:
            raise ValueError("ledger record mismatch")


def commit_ledger_write_once(directory: Path, record: LedgerRecord) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{record.sequence:02d}-{record.state.value}.json"
    head = directory / f"head-{record.sequence:02d}.json"
    if path.exists() or head.exists():
        raise FileExistsError("ledger sequence already committed")
    if record.sequence:
        previous_head_path = directory / f"head-{record.sequence - 1:02d}.json"
        if not previous_head_path.exists():
            raise ValueError("previous external chain head is missing")
        previous = json.loads(previous_head_path.read_text(encoding="utf-8"))
        if previous["chain_head"] != record.previous_chain_head:
            raise ValueError("previous external chain head mismatch")
    with path.open("x", encoding="utf-8") as handle:
        json.dump(record.model_dump(mode="json"), handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    with head.open("x", encoding="utf-8") as handle:
        json.dump(
            {"sequence": record.sequence, "chain_head": record.record_sha256},
            handle,
            ensure_ascii=False,
            indent=2,
        )
        handle.write("\n")
    return path


def validate_join(rows: list[PrivateJoinRow], require_content: bool) -> None:
    if len(rows) != 36:
        raise ValueError("join must contain exactly 36 texts")
    for key in ("private_text_id", "public_text_id"):
        if len({getattr(row, key) for row in rows}) != 36:
            raise ValueError(f"{key} must be unique")
    blocks: dict[str, list[PrivateJoinRow]] = {}
    for row in rows:
        blocks.setdefault(row.block_id, []).append(row)
        if require_content and row.content_sha256 is None:
            raise ValueError("content hash required")
    if len(blocks) != 12:
        raise ValueError("join must contain 12 blocks")
    for values in blocks.values():
        if len({row.public_block_id for row in values}) != 1:
            raise ValueError("private block must map to one public block")
        if {row.arm for row in values} != {"A", "B", "C"}:
            raise ValueError("each block requires all arms")
        if {row.public_position for row in values} != {1, 2, 3}:
            raise ValueError("each block requires randomized positions 1-3")


def validate_public_shell(
    rows: list[PrivateJoinRow], public_shell: dict[str, Any]
) -> None:
    expected = {
        (row.public_block_id, row.public_text_id, row.public_position) for row in rows
    }
    observed = {
        (block["public_block_id"], text["public_text_id"], text["position"])
        for block in public_shell["blocks"]
        for text in block["texts"]
    }
    if expected != observed:
        raise ValueError("public shell and sealed join do not match")


def evaluate_primary_pilot(
    outcomes: list[PilotBlockOutcome],
    protocol: R2Protocol,
) -> dict[str, Any]:
    if len(outcomes) != 12 or len({item.block_id for item in outcomes}) != 12:
        raise ValueError("primary pilot requires exactly 12 unique blocks")
    scene_counts = {
        scene.scene_id: sum(item.scene_id == scene.scene_id for item in outcomes)
        for scene in protocol.scenes
    }
    if any(value != 3 for value in scene_counts.values()):
        raise ValueError("each scene requires exactly three fixed-denominator blocks")
    metrics = ("naturalness", "less_template", "overall_quality")
    score_map = {"A": 0.0, "tie": 0.5, "C": 1.0}
    metric_results = {}
    for metric in metrics:
        score = sum(score_map[getattr(item, metric)] for item in outcomes)
        consistent_scenes = sum(
            sum(
                getattr(item, metric) in {"C", "tie"}
                for item in outcomes
                if item.scene_id == scene.scene_id
            )
            >= protocol.pilot_rule.scene_noninferior_blocks_min
            for scene in protocol.scenes
        )
        metric_results[metric] = {
            "score_fixed_denominator_12": score,
            "directional_threshold_met": (
                score >= protocol.pilot_rule.primary_directional_score_min
            ),
            "scene_consistency_count": consistent_scenes,
            "scene_consistency_met": (
                consistent_scenes
                >= protocol.pilot_rule.scene_consistency_min_scenes
            ),
        }
    a_mandatory = sum(item.arm_a_hard.mandatory_events_complete for item in outcomes)
    c_mandatory = sum(item.arm_c_hard.mandatory_events_complete for item in outcomes)
    violation_fields = (
        "unauthorized_new_character_detected",
        "unauthorized_new_solution_detected",
        "unauthorized_relationship_change_detected",
    )
    violations = {
        field: {
            "A": sum(getattr(item.arm_a_hard, field) for item in outcomes),
            "C": sum(getattr(item.arm_c_hard, field) for item in outcomes),
        }
        for field in violation_fields
    }
    hard_non_degradation = c_mandatory >= a_mandatory and all(
        value["C"] <= value["A"] for value in violations.values()
    )
    expand = (
        all(
            value["directional_threshold_met"] and value["scene_consistency_met"]
            for value in metric_results.values()
        )
        and hard_non_degradation
    )
    return {
        "fixed_denominator": 12,
        "metric_results": metric_results,
        "hard_task": {
            "mandatory_complete": {"A": a_mandatory, "C": c_mandatory},
            "violations": violations,
            "non_degradation": hard_non_degradation,
        },
        "conclusion": "directional_expand_signal" if expand else "do_not_expand",
        "confirmatory_causal_claim_allowed": False,
        "single_composite_score": None,
    }
