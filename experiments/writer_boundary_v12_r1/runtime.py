from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .models import (
    DecisionTicket,
    R1PostWriteReview,
    R1Protocol,
    R1Scene,
    StateRecord,
    WorkflowState,
)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_protocol(path: Path) -> R1Protocol:
    return R1Protocol.model_validate_json(path.read_text(encoding="utf-8"))


def scene_registry(protocol: R1Protocol) -> dict[str, R1Scene]:
    return {scene.scene_id: scene for scene in protocol.scenes}


def allowed_observed_values(scene: R1Scene) -> set[str]:
    return {
        *(option.value for option in scene.decision_contract.allowed_values),
        "unclear",
        "other",
    }


def validate_review_against_protocol(
    review: R1PostWriteReview, protocol: R1Protocol
) -> R1PostWriteReview:
    scene = scene_registry(protocol)[review.scene_id]
    observed = review.execution_audit.observed_decision.value
    if observed not in allowed_observed_values(scene):
        raise ValueError(
            f"observed decision {observed!r} is not legal for {scene.scene_id}"
        )
    valid_event_ids = {
        item.split(maxsplit=1)[0] for item in scene.mandatory_events
    }
    if not set(review.hard_checks.failed_event_ids).issubset(valid_event_ids):
        raise ValueError("failed event ID is not defined by this scene")
    return review


def build_ticket(
    *,
    protocol: R1Protocol,
    triplet_id: str,
    scene_id: str,
    selected_value: str,
    source_a_text_sha256: str,
    source_a_audit_sha256: str,
) -> tuple[DecisionTicket, str]:
    scene = scene_registry(protocol)[scene_id]
    matches = [
        option
        for option in scene.decision_contract.allowed_values
        if option.value == selected_value
    ]
    if len(matches) != 1:
        raise ValueError("anchor decision must map to exactly one scene option")
    option = matches[0]
    ticket = DecisionTicket(
        triplet_id=triplet_id,
        scene_id=scene_id,
        decision_id=scene.decision_contract.decision_id,
        selected_value=option.value,
        selected_definition=option.definition,
        selected_summary=option.selected_summary,
        source_a_text_sha256=source_a_text_sha256,
        source_a_audit_sha256=source_a_audit_sha256,
    )
    return ticket, canonical_hash(ticket.model_dump(mode="json"))


def verify_ticket_consumption(
    ticket: DecisionTicket, expected_hash: str, scene: R1Scene
) -> None:
    actual_hash = canonical_hash(ticket.model_dump(mode="json"))
    if actual_hash != expected_hash:
        raise ValueError("ticket hash mismatch")
    if ticket.scene_id != scene.scene_id:
        raise ValueError("ticket scene mismatch")
    if ticket.decision_id != scene.decision_contract.decision_id:
        raise ValueError("ticket decision mismatch")
    values = {item.value for item in scene.decision_contract.allowed_values}
    if ticket.selected_value not in values:
        raise ValueError("ticket value is not legal for scene")
    option = next(
        item
        for item in scene.decision_contract.allowed_values
        if item.value == ticket.selected_value
    )
    if (
        ticket.selected_definition != option.definition
        or ticket.selected_summary != option.selected_summary
    ):
        raise ValueError("ticket text does not match canonical scene option")


STATE_ORDER = list(WorkflowState)


def append_state(
    records: list[StateRecord],
    *,
    triplet_id: str,
    state: WorkflowState,
    actor_id: str,
    input_hashes: list[str],
    output_hash: str,
) -> StateRecord:
    expected_index = len(records)
    if expected_index >= len(STATE_ORDER) or state != STATE_ORDER[expected_index]:
        raise ValueError("state transition is out of order")
    previous_hash = (
        canonical_hash(records[-1].model_dump(mode="json")) if records else None
    )
    record = StateRecord(
        triplet_id=triplet_id,
        state=state,
        actor_id=actor_id,
        input_hashes=input_hashes,
        output_hash=output_hash,
        previous_record_hash=previous_hash,
    )
    records.append(record)
    return record


def verify_state_chain(records: list[StateRecord]) -> None:
    if [record.state for record in records] != STATE_ORDER:
        raise ValueError("state chain is incomplete or out of order")
    for index, record in enumerate(records):
        expected = (
            canonical_hash(records[index - 1].model_dump(mode="json"))
            if index
            else None
        )
        if record.previous_record_hash != expected:
            raise ValueError("state chain hash mismatch")

