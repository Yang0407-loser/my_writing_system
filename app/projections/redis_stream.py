"""Rebuildable deterministic Redis Stream projection."""

from __future__ import annotations

from collections.abc import Iterable

from ..canonical.hashing import sha256_text
from ..canonical.projection_ports import ProjectionMessage, ProjectionRecord, ProjectionScope
from ..canonical.projection_registry import DEFAULT_PROJECTOR_REGISTRY
from .base import ProjectionAdapterBase, normalized_records


class RedisStreamProjectionAdapter(ProjectionAdapterBase):
    spec = DEFAULT_PROJECTOR_REGISTRY.get("redis_stream")

    def __init__(self, blackboard, scope: ProjectionScope, task_id: str) -> None:
        super().__init__(scope, task_id)
        self.blackboard = blackboard

    def _record(self, message: ProjectionMessage) -> ProjectionRecord:
        revision = self._validate_message(message)
        payload = {
            "event": message.event_type,
            "tenant_id": message.tenant_id,
            "project_id": message.project_id,
            "projection_event_id": message.projection_event_id,
            "commit_id": message.commit_id,
            "revision_id": message.revision_id,
            "content_hash": revision["content_hash"],
            "text": revision["content"],
            "stream_position": message.stream_position,
        }
        return ProjectionRecord(
            record_id=f"redis-stream:{sha256_text(message.projection_event_id)}",
            stream_position=message.stream_position,
            commit_id=message.commit_id,
            revision_id=message.revision_id,
            payload=payload,
        )

    def apply(self, message: ProjectionMessage):
        record = self._record(message)
        self.blackboard.xadd_canonical_event(
            self.scope.tenant_id, self.scope.project_id, message.stream_position, record.payload
        )
        return self._receipt(message, (record,))

    def expected_records(self, messages: Iterable[ProjectionMessage]):
        return normalized_records(self._record(message) for message in messages)

    def actual_records(self, scope: ProjectionScope):
        self._validate_actual_scope(scope)
        rows = self.blackboard.list_canonical_events(scope.tenant_id, scope.project_id)
        return normalized_records(
            ProjectionRecord(
                record_id=f"redis-stream:{sha256_text(str(row['projection_event_id']))}",
                stream_position=int(row["stream_position"]),
                commit_id=str(row["commit_id"]),
                revision_id=str(row["revision_id"]),
                payload={k: v for k, v in row.items() if k != "_redis_id"},
            )
            for row in rows
        )

    def clear(self, scope: ProjectionScope) -> None:
        self._validate_actual_scope(scope)
        self.blackboard.clear_canonical_events(scope.tenant_id, scope.project_id)
