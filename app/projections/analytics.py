"""PostgreSQL-derived analytics projection."""

from __future__ import annotations

import uuid
from collections.abc import Iterable

from sqlalchemy import delete, select

from ..canonical.models import ProjectionAnalyticsEvent
from ..canonical.projection_ports import ProjectionMessage, ProjectionRecord, ProjectionScope
from ..canonical.projection_registry import DEFAULT_PROJECTOR_REGISTRY
from .base import ProjectionAdapterBase, normalized_records


class AnalyticsProjectionAdapter(ProjectionAdapterBase):
    spec = DEFAULT_PROJECTOR_REGISTRY.get("analytics")

    def __init__(self, session_factory, scope: ProjectionScope, task_id: str) -> None:
        super().__init__(scope, task_id)
        self.session_factory = session_factory

    def _record(self, message: ProjectionMessage) -> ProjectionRecord:
        revision = self._validate_message(message)
        payload = {
            "event_type": message.event_type,
            "content_hash": revision["content_hash"],
            "content_length": len(revision["content"]),
            "metadata": revision["metadata"],
            "projection_event_id": message.projection_event_id,
            "commit_id": message.commit_id,
            "revision_id": message.revision_id,
        }
        return ProjectionRecord(
            record_id=f"analytics:{message.projection_event_id}",
            stream_position=message.stream_position,
            commit_id=message.commit_id,
            revision_id=message.revision_id,
            payload=payload,
        )

    def apply(self, message: ProjectionMessage):
        record = self._record(message)
        with self.session_factory() as session:
            row = session.scalar(select(ProjectionAnalyticsEvent).where(ProjectionAnalyticsEvent.projection_event_id == message.projection_event_id))
            values = dict(
                projection_event_id=message.projection_event_id,
                tenant_id=self.scope.tenant_id,
                project_id=self.scope.project_id,
                projector_id=message.projector_id,
                stream_position=message.stream_position,
                event_type=message.event_type,
                payload_json=record.payload,
            )
            if row is None:
                row = ProjectionAnalyticsEvent(id=str(uuid.uuid5(uuid.NAMESPACE_URL, message.projection_event_id)), **values)
                session.add(row)
            elif any(getattr(row, key) != value for key, value in values.items() if key != "payload_json") or row.payload_json != record.payload:
                raise ValueError("analytics projection conflict")
            session.commit()
        return self._receipt(message, (record,))

    def expected_records(self, messages: Iterable[ProjectionMessage]):
        return normalized_records(self._record(message) for message in messages)

    def actual_records(self, scope: ProjectionScope):
        self._validate_actual_scope(scope)
        with self.session_factory() as session:
            rows = session.scalars(select(ProjectionAnalyticsEvent).where(ProjectionAnalyticsEvent.tenant_id == scope.tenant_id, ProjectionAnalyticsEvent.project_id == scope.project_id)).all()
        return normalized_records(
            ProjectionRecord(
                record_id=f"analytics:{row.projection_event_id}",
                stream_position=row.stream_position,
                commit_id=str(row.payload_json["commit_id"]),
                revision_id=str(row.payload_json["revision_id"]),
                payload=row.payload_json,
            )
            for row in rows
        )

    def clear(self, scope: ProjectionScope) -> None:
        self._validate_actual_scope(scope)
        with self.session_factory() as session:
            session.execute(delete(ProjectionAnalyticsEvent).where(ProjectionAnalyticsEvent.tenant_id == scope.tenant_id, ProjectionAnalyticsEvent.project_id == scope.project_id))
            session.commit()
