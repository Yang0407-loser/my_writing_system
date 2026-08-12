"""Shared deterministic record and receipt normalization for projections."""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from ..canonical.hashing import sha256_json
from ..canonical.projection_ports import (
    ProjectionMessage,
    ProjectionReceipt,
    ProjectionRecord,
    ProjectionScope,
)


def canonical_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return json.loads(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )


def normalized_records(records: Iterable[ProjectionRecord]) -> tuple[ProjectionRecord, ...]:
    normalized = tuple(
        record.model_copy(update={"payload": canonical_payload(record.payload)})
        for record in records
    )
    return tuple(sorted(normalized, key=lambda item: (item.stream_position, item.record_id)))


def records_digest(records: Iterable[ProjectionRecord]) -> str:
    return sha256_json(
        [record.model_dump(mode="json") for record in normalized_records(records)]
    )


class ProjectionAdapterBase:
    def __init__(self, scope: ProjectionScope, task_id: str) -> None:
        if not task_id.strip():
            raise ValueError("canonical task_id is required")
        self.scope = scope
        self.task_id = task_id

    def _validate_message(self, message: ProjectionMessage) -> dict[str, Any]:
        if not isinstance(message, ProjectionMessage):
            raise TypeError("projection input must be a ProjectionMessage")
        if (
            message.tenant_id != self.scope.tenant_id
            or message.project_id != self.scope.project_id
        ):
            raise ValueError("projection message is outside adapter scope")
        revision = message.payload.get("revision")
        if not isinstance(revision, dict):
            raise ValueError("projection revision payload is required")
        metadata = revision.get("metadata")
        if not isinstance(metadata, dict):
            raise ValueError("projection revision metadata is required")
        if str(metadata.get("task_id") or "") != self.task_id:
            raise ValueError("projection message canonical task does not match adapter task")
        return revision

    def _validate_actual_scope(self, scope: ProjectionScope) -> None:
        if scope != self.scope:
            raise ValueError("requested scope does not match adapter scope")

    def _receipt(
        self, message: ProjectionMessage, records: Iterable[ProjectionRecord]
    ) -> ProjectionReceipt:
        records = normalized_records(records)
        return ProjectionReceipt(
            projection_event_id=message.projection_event_id,
            projector_id=message.projector_id,
            projector_version=message.projector_version,
            stream_position=message.stream_position,
            record_count=len(records),
            content_digest=records_digest(records),
        )
