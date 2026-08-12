"""Minimal single-process dispatcher for durable canonical outbox rows."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timezone

from sqlalchemy import Select, case, select
from sqlalchemy.orm import Session

from .hashing import sha256_json
from .models import CanonicalCommit, OutboxEvent, ProjectionDelivery
from .projection_ports import ProjectionMessage, ProjectionPort
from .projection_registry import projection_event_id


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
                ProjectionDelivery.barrier_kind == "critical",
            )
        )

    def dispatch_non_blocking(self, commit_id: str) -> DispatchSummary:
        return self._dispatch(
            self._eligible().where(
                OutboxEvent.commit_id == commit_id,
                ProjectionDelivery.barrier_kind == "non_blocking",
            )
        )

    def dispatch_pending(self, limit: int = 100) -> DispatchSummary:
        if limit < 1:
            raise ValueError("limit must be positive")
        return self._dispatch(self._eligible().limit(limit))

    def _eligible(self) -> Select[tuple[ProjectionDelivery]]:
        return (
            select(ProjectionDelivery)
            .join(
                OutboxEvent,
                OutboxEvent.id == ProjectionDelivery.outbox_event_id,
            )
            .join(CanonicalCommit, CanonicalCommit.id == OutboxEvent.commit_id)
            .where(
                ProjectionDelivery.tenant_id == self.tenant_id,
                ProjectionDelivery.project_id == self.project_id,
                CanonicalCommit.tenant_id == self.tenant_id,
                CanonicalCommit.project_id == self.project_id,
                CanonicalCommit.status == "committed",
                ProjectionDelivery.status == "pending",
                ProjectionDelivery.available_at <= datetime.now(timezone.utc),
            )
            .order_by(
                ProjectionDelivery.available_at,
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
                    value=ProjectionDelivery.projector_id,
                    else_=99,
                ),
                ProjectionDelivery.id,
            )
        )

    def _dispatch(
        self, statement: Select[tuple[ProjectionDelivery]]
    ) -> DispatchSummary:
        session = self._session_factory()
        summary = {"published": 0, "failed": 0}
        delivery_ids = tuple(row.id for row in session.scalars(statement).all())
        # Snapshot IDs before commits expire ORM instances in default sessions.
        for delivery_id in delivery_ids:
            row = session.execute(
                select(ProjectionDelivery, OutboxEvent)
                .join(
                    OutboxEvent,
                    OutboxEvent.id == ProjectionDelivery.outbox_event_id,
                )
                .join(CanonicalCommit, CanonicalCommit.id == OutboxEvent.commit_id)
                .where(
                    ProjectionDelivery.id == delivery_id,
                    ProjectionDelivery.tenant_id == self.tenant_id,
                    ProjectionDelivery.project_id == self.project_id,
                    CanonicalCommit.tenant_id == self.tenant_id,
                    CanonicalCommit.project_id == self.project_id,
                    CanonicalCommit.status == "committed",
                    ProjectionDelivery.status == "pending",
                )
            ).one_or_none()
            if row is None:
                continue
            delivery, event = row
            now = datetime.now(timezone.utc)
            delivery.attempt_count += 1
            delivery.last_attempt_at = now
            projector = self.projectors.get(delivery.projector_id)
            try:
                if projector is None:
                    raise LookupError(
                        f"no projector registered for {delivery.projector_id}"
                    )
                projector(
                    ProjectionMessage(
                        projection_event_id=projection_event_id(
                            delivery.projector_id, event.commit_id
                        ),
                        outbox_event_id=event.id,
                        delivery_id=delivery.id,
                        tenant_id=event.tenant_id,
                        project_id=event.project_id,
                        commit_id=event.commit_id,
                        revision_id=event.payload_json["revision_id"],
                        state_version_id=event.payload_json["state_version_id"],
                        projector_id=delivery.projector_id,
                        barrier_kind=delivery.barrier_kind,
                        event_type=event.event_type,
                        stream_position=delivery.stream_position,
                        payload=event.payload_json,
                    )
                )
            except Exception as exc:  # projection failures are durable state
                delivery.status = "pending"
                delivery.published_at = None
                delivery.last_error_class = type(exc).__name__[:255]
                delivery.last_error_message = f"{type(exc).__name__}: {exc}"[:4000]
                delivery.receipt_json = None
                delivery.receipt_digest = None
                summary["failed"] += 1
            else:
                receipt = {
                    "kind": "synchronous_dispatch_compatibility_receipt",
                    "outbox_event_id": event.id,
                    "projector_id": delivery.projector_id,
                    "stream_position": delivery.stream_position,
                }
                delivery.status = "published"
                delivery.published_at = now
                delivery.last_error_code = None
                delivery.last_error_class = None
                delivery.last_error_message = None
                delivery.receipt_json = receipt
                delivery.receipt_digest = sha256_json(receipt)
                summary["published"] += 1
            session.commit()
        return summary
