from __future__ import annotations

from app.canonical.hashing import sha256_json
from app.canonical.projection_ports import (
    ProjectionMessage,
    ProjectionReceipt,
    ProjectionRecord,
    ProjectionScope,
)
from app.projections.base import normalized_records, records_digest


class MemoryProjectionAdapter:
    def __init__(self, spec, scope: ProjectionScope, task_id: str) -> None:
        self.spec = spec
        self.scope = scope
        self.task_id = task_id
        self.rows: dict[str, ProjectionRecord] = {}
        self.corrupt_actual = False

    def _record(self, message: ProjectionMessage) -> ProjectionRecord:
        revision = message.payload["revision"]
        metadata = revision["metadata"]
        if str(metadata.get("task_id") or "") != self.task_id:
            raise ValueError("task mismatch")
        return ProjectionRecord(
            record_id=f"search:{message.projection_event_id}",
            stream_position=message.stream_position,
            commit_id=message.commit_id,
            revision_id=message.revision_id,
            payload={
                "content_hash": revision["content_hash"],
                "projection_event_id": message.projection_event_id,
            },
        )

    def apply(self, message: ProjectionMessage) -> ProjectionReceipt:
        record = self._record(message)
        self.rows[record.record_id] = record
        return ProjectionReceipt(
            projection_event_id=message.projection_event_id,
            projector_id=message.projector_id,
            projector_version=message.projector_version,
            stream_position=message.stream_position,
            record_count=1,
            content_digest=records_digest((record,)),
        )

    def expected_records(self, messages):
        return normalized_records(self._record(message) for message in messages)

    def actual_records(self, scope: ProjectionScope):
        if scope != self.scope:
            raise ValueError("scope mismatch")
        records = normalized_records(self.rows.values())
        if self.corrupt_actual and records:
            first = records[0]
            records = (
                first.model_copy(
                    update={"payload": {**first.payload, "corrupt": sha256_json("x")}}
                ),
                *records[1:],
            )
        return records

    def clear(self, scope: ProjectionScope) -> None:
        if scope != self.scope:
            raise ValueError("scope mismatch")
        self.rows.clear()
