"""Minimal single-process dispatcher for durable canonical outbox rows."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timezone

from sqlalchemy import Select, case, select
from sqlalchemy.orm import Session

from .models import CanonicalCommit, OutboxEvent
from .projection_ports import ProjectionMessage, ProjectionPort


DispatchSummary = dict[str, int]


class OutboxDispatcher:
    """Retry visible pending/failed events without changing canonical facts.

    P2 intentionally provides a synchronous, single-process dispatcher. Worker
    leases, sharding, dead-letter policy and rebuild orchestration remain P3A.
    """

    def __init__(
        self,
        session_factory: Callable[[], Session],
        tenant_id: str,
        project_id: str,
        projectors: Mapping[str, ProjectionPort],
    ) -> None:
        if not tenant_id or not project_id:
            raise ValueError("tenant_id and project_id are required")
        self._session_factory = session_factory
        self.tenant_id = tenant_id
        self.project_id = project_id
        self.projectors = projectors

    def dispatch_critical(self, commit_id: str) -> DispatchSummary:
        return self._dispatch(
            self._eligible().where(
                OutboxEvent.commit_id == commit_id,
                OutboxEvent.barrier_kind == "critical",
            )
        )

    def dispatch_non_blocking(self, commit_id: str) -> DispatchSummary:
        return self._dispatch(
            self._eligible().where(
                OutboxEvent.commit_id == commit_id,
                OutboxEvent.barrier_kind == "non_blocking",
            )
        )

    def dispatch_pending(self, limit: int = 100) -> DispatchSummary:
        if limit < 1:
            raise ValueError("limit must be positive")
        return self._dispatch(self._eligible().limit(limit))

    def _eligible(self) -> Select[tuple[OutboxEvent]]:
        return (
            select(OutboxEvent)
            .join(CanonicalCommit, CanonicalCommit.id == OutboxEvent.commit_id)
            .where(
                OutboxEvent.tenant_id == self.tenant_id,
                OutboxEvent.project_id == self.project_id,
                CanonicalCommit.tenant_id == self.tenant_id,
                CanonicalCommit.project_id == self.project_id,
                CanonicalCommit.status == "committed",
                OutboxEvent.status.in_(("pending", "failed")),
                OutboxEvent.available_at <= datetime.now(timezone.utc),
            )
            .order_by(
                OutboxEvent.available_at,
                case(
                    {
                        "legacy_world_event": 1,
                        "handover_context": 2,
                        "chroma_story_chunks": 3,
                        "redis_stream": 4,
                        "task_preview": 5,
                        "markdown_export": 6,
                        "analytics": 7,
                    },
                    value=OutboxEvent.projection_name,
                    else_=99,
                ),
                OutboxEvent.id,
            )
        )

    def _dispatch(self, statement: Select[tuple[OutboxEvent]]) -> DispatchSummary:
        session = self._session_factory()
        summary = {"published": 0, "failed": 0}
        event_ids = tuple(session.scalars(statement).all())
        # Snapshot IDs before commits expire ORM instances in default sessions.
        for event_or_id in event_ids:
            event_id = event_or_id.id
            event = session.scalar(
                select(OutboxEvent)
                .join(CanonicalCommit, CanonicalCommit.id == OutboxEvent.commit_id)
                .where(
                    OutboxEvent.id == event_id,
                    OutboxEvent.tenant_id == self.tenant_id,
                    OutboxEvent.project_id == self.project_id,
                    CanonicalCommit.tenant_id == self.tenant_id,
                    CanonicalCommit.project_id == self.project_id,
                    CanonicalCommit.status == "committed",
                    OutboxEvent.status.in_(("pending", "failed")),
                )
            )
            if event is None:
                continue
            event.attempts += 1
            projector = self.projectors.get(event.projection_name)
            try:
                if projector is None:
                    raise LookupError(
                        f"no projector registered for {event.projection_name}"
                    )
                projector(
                    ProjectionMessage(
                        event_id=event.id,
                        tenant_id=event.tenant_id,
                        project_id=event.project_id,
                        commit_id=event.commit_id,
                        projection_name=event.projection_name,
                        barrier_kind=event.barrier_kind,
                        event_type=event.event_type,
                        payload=event.payload_json,
                    )
                )
            except Exception as exc:  # projection failures are durable state
                event.status = "failed"
                event.published_at = None
                event.last_error = f"{type(exc).__name__}: {exc}"[:4000]
                summary["failed"] += 1
            else:
                event.status = "published"
                event.published_at = datetime.now(timezone.utc)
                event.last_error = None
                summary["published"] += 1
            session.commit()
        return summary
