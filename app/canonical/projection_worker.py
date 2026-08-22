"""Shared fenced worker over PostgreSQL-authoritative projection Deliveries."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import ProjectionAttempt, ProjectionDelivery, ProjectionPartition
from .projection_delivery import DeliveryClaim, ProjectionDeliveryStore, ScanFilter
from .projection_locks import ProjectionLockScope, ProjectionMaintenanceLocks
from .projection_ports import ProjectionExecutor, ProjectionReceipt
from .projection_registry import DEFAULT_PROJECTOR_REGISTRY, ProjectorRegistry
from .projection_replay import CanonicalProjectionReplay


@dataclass(frozen=True)
class ScanSummary:
    claimed: int = 0
    published: int = 0
    retried: int = 0
    dead_lettered: int = 0
    stale: int = 0


class ProjectionWorker:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        executors: Mapping[str, ProjectionExecutor],
        *,
        worker_id: str,
        registry: ProjectorRegistry = DEFAULT_PROJECTOR_REGISTRY,
        failure_hook: Callable[[str], None] | None = None,
    ) -> None:
        if not worker_id.strip():
            raise ValueError("worker_id is required")
        self._session_factory = session_factory
        self.executors = dict(executors)
        self.worker_id = worker_id
        self.registry = registry
        self.failure_hook = failure_hook

    def _stage(self, stage: str) -> None:
        if self.failure_hook is not None:
            self.failure_hook(stage)

    def scan_once(self, filter: ScanFilter) -> ScanSummary:
        if filter.limit < 1:
            raise ValueError("limit must be positive")
        summary = ScanSummary()
        for _ in range(filter.limit):
            session = self._session_factory()
            try:
                store = ProjectionDeliveryStore(session, self.registry)
                claim = store.claim_next(self.worker_id, filter)
                if claim is None:
                    break
                summary = replace(summary, claimed=summary.claimed + 1)
                self._stage("after_claim")
                locks = ProjectionMaintenanceLocks(session.get_bind())
                scope = ProjectionLockScope(
                    claim.tenant_id, claim.project_id, claim.projector_id
                )
                with locks.shared(scope):
                    if not self._is_exact_current_active(session, claim):
                        summary = replace(summary, stale=summary.stale + 1)
                        continue
                    if not store.heartbeat(claim):
                        summary = replace(summary, stale=summary.stale + 1)
                        continue
                    try:
                        executor = self.executors.get(claim.projector_id)
                        if executor is None:
                            raise LookupError(
                                f"no executor registered for {claim.projector_id}"
                            )
                        message = CanonicalProjectionReplay(
                            session, registry=self.registry
                        ).message_for_delivery(claim.delivery_id)
                        self._validate_executor(executor, message)
                        receipt = executor.apply(message)
                        self._validate_receipt(receipt, message)
                        self._stage("after_receipt")
                    except Exception as exc:
                        if not store.record_failure(claim, exc):
                            summary = replace(summary, stale=summary.stale + 1)
                            continue
                        status = session.get(ProjectionDelivery, claim.delivery_id).status
                        field = (
                            "dead_lettered" if status == "dead_letter" else "retried"
                        )
                        summary = replace(
                            summary, **{field: getattr(summary, field) + 1}
                        )
                        continue
                    if not store.mark_published(claim, receipt.model_dump(mode="json")):
                        summary = replace(summary, stale=summary.stale + 1)
                        continue
                    summary = replace(summary, published=summary.published + 1)
                    self._stage("after_publish")
            finally:
                session.close()
        return summary

    @staticmethod
    def _is_exact_current_active(session: Session, claim: DeliveryClaim) -> bool:
        now = datetime.now(timezone.utc)
        return (
            session.scalar(
                select(ProjectionDelivery.id)
                .join(
                    ProjectionPartition,
                    (ProjectionPartition.tenant_id == ProjectionDelivery.tenant_id)
                    & (ProjectionPartition.project_id == ProjectionDelivery.project_id)
                    & (
                        ProjectionPartition.projector_id
                        == ProjectionDelivery.projector_id
                    ),
                )
                .join(
                    ProjectionAttempt,
                    (ProjectionAttempt.id == claim.attempt_id)
                    & (ProjectionAttempt.delivery_id == ProjectionDelivery.id),
                )
                .where(
                    ProjectionDelivery.id == claim.delivery_id,
                    ProjectionDelivery.status == "processing",
                    ProjectionDelivery.lease_token == claim.lease_token,
                    ProjectionDelivery.leased_by == claim.leased_by,
                    ProjectionDelivery.leased_until >= now,
                    ProjectionDelivery.attempt_count
                    == ProjectionAttempt.attempt_number,
                    ProjectionAttempt.lease_token == claim.lease_token,
                    ProjectionAttempt.leased_by == claim.leased_by,
                    ProjectionAttempt.outcome == "claimed",
                    ProjectionPartition.projector_version
                    == ProjectionDelivery.projector_version,
                    ProjectionPartition.enrollment_status == "active",
                    ProjectionPartition.runtime_status == "active",
                    ProjectionPartition.last_published_position
                    == ProjectionDelivery.stream_position - 1,
                )
            )
            is not None
        )

    @staticmethod
    def _validate_executor(executor, message) -> None:
        if (
            executor.spec.projector_id != message.projector_id
            or executor.spec.version != message.projector_version
            or executor.spec.barrier_kind != message.barrier_kind
        ):
            raise ValueError("projection executor does not match claimed message")

    @staticmethod
    def _validate_receipt(receipt, message) -> None:
        if not isinstance(receipt, ProjectionReceipt):
            raise TypeError("projection executor must return ProjectionReceipt")
        if (
            receipt.projection_event_id != message.projection_event_id
            or receipt.projector_id != message.projector_id
            or receipt.projector_version != message.projector_version
            or receipt.stream_position != message.stream_position
        ):
            raise ValueError("projection receipt does not match claimed message")


def build_production_projection_worker(
    worker_id: str,
    *,
    database_url: str | None = None,
) -> ProjectionWorker:
    """Construct the shared PostgreSQL scanner with all production adapters."""
    from ..config import settings
    from ..projections.factory import build_projection_adapters
    from .database import build_engine, build_session_factory

    engine = build_engine(database_url or settings.CANONICAL_DATABASE_URL)
    session_factory = build_session_factory(engine)
    return ProjectionWorker(
        session_factory,
        build_projection_adapters(
            session_factory,
            markdown_root=settings.PROJECTION_MARKDOWN_ROOT,
        ),
        worker_id=worker_id,
    )
