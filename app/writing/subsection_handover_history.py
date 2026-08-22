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

# 原始 compact payload 持久化大小护栏（字符数，canonical JSON 编码后）。
# 输出上限 1000 token（约 4000 字符）下正常 payload 远小于此；超限视为异常，不持久化。
MAX_COMPACT_PAYLOAD_PERSIST_CHARS = 20_000


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


def payload_for_persistence(payload: Any) -> dict[str, Any] | None:
    """Return the parsed compact payload iff it is a dict within the size guard.

    补齐金标冻结记录在案的缺口：只有持久化解析后的原始 payload，
    未来的真实运行才能全量冻结到 validator 层重放。解析失败（非 dict）
    或超出大小护栏时返回 None——宁缺毋滥，不截断持久化。
    """
    if not isinstance(payload, dict):
        return None
    try:
        encoded = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":"), default=str
        )
    except (TypeError, ValueError):
        return None
    if len(encoded) > MAX_COMPACT_PAYLOAD_PERSIST_CHARS:
        return None
    return payload


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
    # arity 遥测：形状类拒绝的骨架分布（不含内容），2026-07-26 起
    rejection_shape_skeletons: dict[str, int] | None = None
    next_boundary_hash: str | None = None
    source_manifest: tuple[dict[str, str], ...] | None = None
    payload_version: str | None = None
    source_registry_hash: str | None = None
    compact_payload_hash: str | None = None
    # 原始 compact payload（解析成功即捕获，含未过形状层的 item），2026-07-27 起
    compact_payload: dict[str, Any] | None = None
    raw_output_tokens: int | None = Field(default=None, ge=0)
    finish_reason: str | None = None
    truncation_status: str | None = None
    restored_claim_count: int | None = Field(default=None, ge=0)
    locally_rejected_claim_count: int | None = Field(default=None, ge=0)


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
    canonical_tenant_id: str | None = None
    canonical_project_id: str | None = None
    stream_position: int | None = Field(default=None, ge=1)
    revision_id: str | None = None
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
    rejection_shape_skeletons: dict[str, int] | None = None
    next_boundary_hash: str | None = None
    source_manifest: tuple[dict[str, str], ...] | None = None
    payload_version: str | None = None
    source_registry_hash: str | None = None
    compact_payload_hash: str | None = None
    # 原始 compact payload（解析成功即捕获，含未过形状层的 item），2026-07-27 起
    compact_payload: dict[str, Any] | None = None
    raw_output_tokens: int | None = Field(default=None, ge=0)
    finish_reason: str | None = None
    truncation_status: str | None = None
    restored_claim_count: int | None = Field(default=None, ge=0)
    locally_rejected_claim_count: int | None = Field(default=None, ge=0)


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
