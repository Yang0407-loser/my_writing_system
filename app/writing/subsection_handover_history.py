"""Typed contracts for persisted per-subsection handover artifacts."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


SUBSECTION_HANDOVER_HISTORY_KEY = "subsection_handover_history_v1"
SUBSECTION_HANDOVER_SCHEMA_VERSION = "subsection-handover-history-v1"
HANDOVER_PRODUCER_VERSION = "writer-handover-v3"

HandoverExecutionStatus = Literal[
    "completed_with_changes",
    "completed_no_change",
    "skipped",
    "error",
]
HandoverFieldName = Literal[
    "foreshadowing",
    "character_state",
    "open_threads",
    "new_facts",
    "found_contradictions",
    "arc_progress",
]
PersistenceStatus = Literal["finalized", "persistence_error"]

HANDOVER_FIELD_NAMES: tuple[str, ...] = (
    "foreshadowing",
    "character_state",
    "open_threads",
    "new_facts",
    "found_contradictions",
    "arc_progress",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def task_id_hash(task_id: str) -> str:
    return hashlib.sha256(task_id.encode("utf-8")).hexdigest()


def value_item_count(value: Any) -> int:
    if value is None or value == "":
        return 0
    if isinstance(value, (list, tuple, set, dict)):
        return len(value)
    return 1


def value_has_change(value: Any) -> bool:
    return value_item_count(value) > 0


class HandoverExtractionObservation(BaseModel):
    """Execution telemetry kept separate from the legacy note return value."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    executed: bool
    execution_status: HandoverExecutionStatus
    note_hash: str | None = None
    valid_field_count: int = Field(default=0, ge=0)
    error_type: str | None = None
    skip_reason: str | None = None
    producer_version: str = HANDOVER_PRODUCER_VERSION
    contract_version: str | None = None
    typed_contract_hash: str | None = None
    accepted_claim_count: int | None = Field(default=None, ge=0)
    rejected_claim_count: int | None = Field(default=None, ge=0)
    rejection_counts: dict[str, int] | None = None
    next_boundary_hash: str | None = None
    source_manifest: tuple[dict[str, str], ...] | None = None


class HandoverFieldArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    field_name: HandoverFieldName
    value: Any
    value_hash: str
    item_count: int = Field(ge=0)
    source_id: str
    source_hash: str
    provenance: str = "writer_handover_extractor"


class SubsectionHandoverRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = SUBSECTION_HANDOVER_SCHEMA_VERSION
    record_id: str
    task_id_hash: str
    section: int = Field(ge=1)
    subsection: int = Field(ge=1)
    output_sha256: str
    prompt_messages_hash: str
    commit_idempotency_key: str
    handover_source_id: str
    handover_note_hash: str | None = None
    execution_status: HandoverExecutionStatus
    fields: tuple[HandoverFieldArtifact, ...] = ()
    field_count: int = Field(ge=0)
    producer: str = "Writer._extract_handover"
    producer_version: str = HANDOVER_PRODUCER_VERSION
    error_type: str | None = None
    skip_reason: str | None = None
    created_at: str
    persistence_status: PersistenceStatus = "finalized"
    production_effect: bool = False
    contract_version: str | None = None
    typed_contract_hash: str | None = None
    accepted_claim_count: int | None = Field(default=None, ge=0)
    rejected_claim_count: int | None = Field(default=None, ge=0)
    rejection_counts: dict[str, int] | None = None
    next_boundary_hash: str | None = None
    source_manifest: tuple[dict[str, str], ...] | None = None


class SubsectionHandoverHistoryEnvelope(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    schema_version: str = SUBSECTION_HANDOVER_SCHEMA_VERSION
    records: dict[str, SubsectionHandoverRecord] = Field(default_factory=dict)
    pending: dict[str, dict] = Field(default_factory=dict)
    errors: tuple[dict, ...] = ()
    production_effect: bool = False


def observation_from_note(note: Any) -> HandoverExtractionObservation:
    """Classify a returned note without changing the legacy return value."""
    if not isinstance(note, dict):
        return HandoverExtractionObservation(
            executed=True,
            execution_status="error",
            error_type="InvalidHandoverPayload",
        )
    present_fields = [name for name in HANDOVER_FIELD_NAMES if name in note]
    if not present_fields:
        return HandoverExtractionObservation(
            executed=True,
            execution_status="error",
            note_hash=sha256_json(note),
            error_type="InvalidHandoverPayload",
        )
    changed = sum(value_has_change(note[name]) for name in present_fields)
    return HandoverExtractionObservation(
        executed=True,
        execution_status=(
            "completed_with_changes" if changed else "completed_no_change"
        ),
        note_hash=sha256_json(note),
        valid_field_count=changed,
    )


def skipped_observation(reason: str) -> HandoverExtractionObservation:
    return HandoverExtractionObservation(
        executed=False,
        execution_status="skipped",
        skip_reason=reason,
    )
