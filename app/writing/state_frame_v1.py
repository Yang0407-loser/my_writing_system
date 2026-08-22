"""Traceable subsection state snapshots built as read-only materialized views."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


STATE_FRAME_SCHEMA_VERSION = "state-frame-snapshot-v1"

FactStatus = Literal["confirmed", "unknown", "conflicted", "pending"]
FactType = Literal[
    "character_state",
    "relationship_state",
    "location_state",
    "temporal_state",
    "presence_state",
    "open_event_chain",
    "foreshadow_state",
    "continuity_state",
]
Durability = Literal[
    "transient", "subsection", "chapter", "persistent", "until_resolved"
]
ExpectationRequiredness = Literal["hard", "soft", "observational"]
ExpectationStatus = Literal[
    "planned", "supported", "partially_supported", "contradicted", "unassessable"
]
FramePhase = Literal["before_generation", "after_commit"]
FrameStatus = Literal["complete", "pending_sources", "partial", "unavailable"]
EvaluationBasis = Literal[
    "deterministic_confirmed",
    "authoritative_state_delta",
    "extractor_reported",
    "codex_assisted_review",
    "human_confirmed",
    "insufficient_evidence",
]


def canonical_hash(value: Any) -> str:
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def task_id_hash(task_id: str) -> str:
    return hashlib.sha256(str(task_id).encode("utf-8")).hexdigest()


class FrozenStateModel(BaseModel):
    model_config = ConfigDict(frozen=True)


class StateSourceRef(FrozenStateModel):
    source_type: str
    source_id: str
    source_hash: str
    producer: str
    section: int | None = None
    subsection: int | None = None
    availability: Literal["available", "pending", "unavailable"] = "available"


class StateFact(FrozenStateModel):
    fact_id: str
    fact_type: FactType
    subject: str
    predicate: str
    value: Any
    status: FactStatus
    durability: Durability
    valid_from: str | None = None
    valid_until: str | None = None
    section: int | None = None
    subsection: int | None = None
    source_type: str
    source_id: str
    source_hash: str
    evidence_start: int | None = Field(default=None, ge=0)
    evidence_end: int | None = Field(default=None, ge=0)
    evidence_excerpt: str = Field(default="", max_length=140)
    producer: str
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    provenance: str
    schema_version: str = STATE_FRAME_SCHEMA_VERSION

    @model_validator(mode="after")
    def validate_span(self):
        if (self.evidence_start is None) != (self.evidence_end is None):
            raise ValueError("evidence span requires both start and end")
        if (
            self.evidence_start is not None
            and self.evidence_end is not None
            and self.evidence_end < self.evidence_start
        ):
            raise ValueError("evidence end precedes start")
        return self

    @property
    def identity_key(self) -> tuple[str, str, str]:
        return self.fact_type, self.subject, self.predicate


class StateExpectation(FrozenStateModel):
    expectation_id: str
    expectation_type: str
    subject: str
    expected_transition: str
    requiredness: ExpectationRequiredness
    section: int
    subsection: int
    source_id: str
    source_hash: str
    status: ExpectationStatus = "planned"
    matched_fact_ids: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    provenance: str
    schema_version: str = STATE_FRAME_SCHEMA_VERSION


class StateFrameSnapshot(FrozenStateModel):
    frame_id: str
    task_id_hash: str
    section: int
    subsection: int
    checkpoint_id: str | None = None
    checkpoint_version: str | None = None
    frame_phase: FramePhase
    frame_status: FrameStatus
    facts: tuple[StateFact, ...] = ()
    expectations: tuple[StateExpectation, ...] = ()
    pending_source_types: tuple[str, ...] = ()
    unavailable_source_types: tuple[str, ...] = ()
    source_manifest: tuple[StateSourceRef, ...] = ()
    conflicts: tuple[str, ...] = ()
    created_at: str | None = None
    finalized_at: str | None = None
    frame_hash: str
    schema_version: str = STATE_FRAME_SCHEMA_VERSION


class FactChange(FrozenStateModel):
    before: StateFact | None = None
    after: StateFact | None = None


class StateDelta(FrozenStateModel):
    delta_id: str
    before_frame_hash: str
    after_frame_hash: str
    added_facts: tuple[StateFact, ...] = ()
    changed_facts: tuple[FactChange, ...] = ()
    resolved_facts: tuple[StateFact, ...] = ()
    unchanged_facts: tuple[StateFact, ...] = ()
    new_conflicts: tuple[str, ...] = ()
    resolved_expectations: tuple[str, ...] = ()
    unresolved_expectations: tuple[str, ...] = ()
    provenance: str = "deterministic_state_frame_diff"
    schema_version: str = STATE_FRAME_SCHEMA_VERSION


class QualityMetric(FrozenStateModel):
    dimension: Literal[
        "handover_continuity",
        "character_state_transition",
        "foreshadow_health",
    ]
    counts: dict[str, int | float | None]
    evaluation_basis: EvaluationBasis
    attributions: tuple[str, ...] = ()
    unavailable_reasons: tuple[str, ...] = ()


class StateFrameQualityObservation(FrozenStateModel):
    task_id_hash: str
    section: int
    subsection: int
    before_frame_hash: str
    after_frame_hash: str
    delta_id: str
    metrics: tuple[QualityMetric, ...]
    source_traceability_rate: float = Field(ge=0.0, le=1.0)
    schema_version: str = STATE_FRAME_SCHEMA_VERSION
