"""Persisted per-subsection StateFrame history contracts."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .state_frame_v1 import (
    STATE_FRAME_SCHEMA_VERSION,
    StateDelta,
    StateFrameSnapshot,
    StateSourceRef,
)


STATE_FRAME_HISTORY_KEY = "state_frame_history_v1"
STATE_FRAME_HISTORY_SCHEMA_VERSION = "state-frame-history-v1"

SourceGranularity = Literal[
    "subsection_exact",
    "section_level_only",
    "current_store_snapshot",
    "unavailable",
]
PersistenceStatus = Literal[
    "captured_before",
    "captured_after",
    "partial",
    "finalized",
    "persistence_error",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SubsectionStateFrameRecord(BaseModel):
    """One frozen before/after boundary for one successfully committed output."""

    model_config = ConfigDict(extra="forbid")

    record_id: str
    task_id_hash: str
    section: int = Field(ge=1)
    subsection: int = Field(ge=1)
    before_frame: StateFrameSnapshot
    after_frame: StateFrameSnapshot | None = None
    delta: StateDelta | None = None
    before_frame_hash: str
    after_frame_hash: str | None = None
    delta_id: str | None = None
    output_sha256: str | None = None
    prompt_messages_hash: str
    checkpoint_version: str | None = None
    commit_idempotency_key: str | None = None
    source_manifest: tuple[StateSourceRef, ...] = ()
    unavailable_source_types: tuple[str, ...] = ()
    pending_source_types: tuple[str, ...] = ()
    source_granularity: dict[str, SourceGranularity] = Field(default_factory=dict)
    persistence_status: PersistenceStatus
    created_at: str
    finalized_at: str | None = None
    schema_version: str = STATE_FRAME_HISTORY_SCHEMA_VERSION
    frame_schema_version: str = STATE_FRAME_SCHEMA_VERSION
    production_effect: bool = False


class StateFrameHistoryEnvelope(BaseModel):
    """Versioned logical artifact mirrored to Blackboard/checkpoint/TaskStore."""

    model_config = ConfigDict(extra="ignore")

    schema_version: str = STATE_FRAME_HISTORY_SCHEMA_VERSION
    records: dict[str, SubsectionStateFrameRecord] = Field(default_factory=dict)
    pending_before: dict[str, SubsectionStateFrameRecord] = Field(
        default_factory=dict
    )
    errors: tuple[dict, ...] = ()
    production_effect: bool = False
