"""Latest Canon revision preview stored independently from scheduling state."""

from __future__ import annotations

from collections.abc import Iterable

from ..canonical.projection_ports import ProjectionMessage, ProjectionRecord, ProjectionScope
from ..canonical.projection_registry import DEFAULT_PROJECTOR_REGISTRY
from .base import ProjectionAdapterBase, normalized_records


class TaskPreviewProjectionAdapter(ProjectionAdapterBase):
    spec = DEFAULT_PROJECTOR_REGISTRY.get("task_preview")

    def __init__(self, blackboard, scope: ProjectionScope, task_id: str) -> None:
        super().__init__(scope, task_id)
        self.blackboard = blackboard
        self.namespace = blackboard.canonical_preview_namespace(scope.tenant_id, scope.project_id)

    def _record(self, message: ProjectionMessage) -> ProjectionRecord:
        revision = self._validate_message(message)
        metadata = revision["metadata"]
        payload = {
            "task_id": self.task_id,
            "document_id": metadata.get("document_id", ""),
            "section": metadata.get("section", 1),
            "subsection": metadata.get("subsection", 1),
            "title": metadata.get("title", ""),
            "text": revision["content"],
            "content_hash": revision["content_hash"],
            "commit_id": message.commit_id,
            "revision_id": message.revision_id,
            "projection_event_id": message.projection_event_id,
            "stream_position": message.stream_position,
        }
        return ProjectionRecord(
            record_id=f"task-preview:{self.blackboard.canonical_scope_hash(self.scope.tenant_id, self.scope.project_id)}",
            stream_position=message.stream_position,
            commit_id=message.commit_id,
            revision_id=message.revision_id,
            payload=payload,
        )

    def apply(self, message: ProjectionMessage):
        record = self._record(message)
        result = self.blackboard.hash_upsert_by_position(
            self.namespace, "latest", record.payload, message.stream_position
        )
        if result in {"stale", "conflict"}:
            raise ValueError(f"task preview {result} at stream_position")
        return self._receipt(message, (record,))

    def expected_records(self, messages: Iterable[ProjectionMessage]):
        records = [self._record(message) for message in messages]
        if not records:
            return ()
        latest = max(records, key=lambda record: record.stream_position)
        conflicts = [
            record
            for record in records
            if record.stream_position == latest.stream_position and record != latest
        ]
        if conflicts:
            raise ValueError("task preview conflict at stream_position")
        return normalized_records((latest,))

    def actual_records(self, scope: ProjectionScope):
        self._validate_actual_scope(scope)
        payload = self.blackboard.hash_get(self.namespace, "latest")
        if not payload:
            return ()
        return normalized_records((ProjectionRecord(
            record_id=f"task-preview:{self.blackboard.canonical_scope_hash(scope.tenant_id, scope.project_id)}",
            stream_position=int(payload["stream_position"]),
            commit_id=str(payload["commit_id"]),
            revision_id=str(payload["revision_id"]),
            payload=payload,
        ),))

    def clear(self, scope: ProjectionScope) -> None:
        self._validate_actual_scope(scope)
        self.blackboard.hash_delete(self.namespace, "latest")
