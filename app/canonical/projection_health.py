"""Database-derived health for the projection runtime."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from .contracts import FrozenArtifact
from .models import (
    CanonicalCommit,
    ProjectionAttempt,
    ProjectionDelivery,
    ProjectionPartition,
    ProjectionRebuildRun,
    ProjectionReconciliation,
)
from .projection_delivery import ScanFilter
from .projection_registry import DEFAULT_PROJECTOR_REGISTRY, ProjectorRegistry


class ProjectionHealthSnapshot(FrozenArtifact):
    lag_events: int
    lag_seconds: float
    oldest_pending_age_seconds: float
    processing_count: int
    expired_lease_count: int
    dead_letter_count: int
    retry_count: int
    rebuild_status_counts: dict[str, int]
    reconciliation_mismatch_count: int
    wakeup_failure_count: int


def projection_health_snapshot(
    session: Session,
    scan_filter: ScanFilter = ScanFilter(),
    *,
    now: datetime | None = None,
    wakeup_failures: int = 0,
    registry: ProjectorRegistry = DEFAULT_PROJECTOR_REGISTRY,
) -> ProjectionHealthSnapshot:
    now = now or datetime.now(timezone.utc)
    delivery_filters = _delivery_filters(scan_filter)
    partition_filters = _partition_filters(scan_filter, registry)
    rebuild_filters = _rebuild_filters(scan_filter)
    reconciliation_filters = _reconciliation_filters(scan_filter)

    heads = (
        select(
            CanonicalCommit.tenant_id.label("tenant_id"),
            CanonicalCommit.project_id.label("project_id"),
            func.max(CanonicalCommit.stream_position).label("head"),
        )
        .where(CanonicalCommit.status == "committed")
        .group_by(CanonicalCommit.tenant_id, CanonicalCommit.project_id)
        .subquery()
    )
    lag_events = session.scalar(
        select(
            func.coalesce(
                func.sum(
                    case(
                        (
                            heads.c.head
                            > ProjectionPartition.last_published_position,
                            heads.c.head
                            - ProjectionPartition.last_published_position,
                        ),
                        else_=0,
                    )
                ),
                0,
            )
        )
        .select_from(ProjectionPartition)
        .join(
            heads,
            (heads.c.tenant_id == ProjectionPartition.tenant_id)
            & (heads.c.project_id == ProjectionPartition.project_id),
        )
        .where(
            ProjectionPartition.enrollment_status == "active",
            *partition_filters,
        )
    )

    oldest_pending = session.scalar(
        select(func.min(ProjectionDelivery.created_at)).where(
            ProjectionDelivery.status == "pending", *delivery_filters
        )
    )
    oldest_lagging = session.scalar(
        select(func.min(ProjectionDelivery.created_at)).where(
            ProjectionDelivery.status.in_(("pending", "processing", "dead_letter")),
            *delivery_filters,
        )
    )
    status_counts = dict(
        session.execute(
            select(ProjectionDelivery.status, func.count())
            .where(*delivery_filters)
            .group_by(ProjectionDelivery.status)
        ).all()
    )
    retry_count = session.scalar(
        select(func.count())
        .select_from(ProjectionAttempt)
        .join(
            ProjectionDelivery,
            ProjectionDelivery.id == ProjectionAttempt.delivery_id,
        )
        .where(
            ProjectionAttempt.outcome == "retry_scheduled", *delivery_filters
        )
    )
    expired_lease_count = session.scalar(
        select(func.count()).select_from(ProjectionDelivery).where(
            ProjectionDelivery.status == "processing",
            ProjectionDelivery.leased_until < now,
            *delivery_filters,
        )
    )
    rebuild_status_counts = {
        status: count
        for status, count in session.execute(
            select(ProjectionRebuildRun.status, func.count())
            .where(*rebuild_filters)
            .group_by(ProjectionRebuildRun.status)
        ).all()
    }
    mismatch_count = session.scalar(
        select(func.count()).select_from(ProjectionReconciliation).where(
            ProjectionReconciliation.expected_digest
            != ProjectionReconciliation.actual_digest,
            *reconciliation_filters,
        )
    )
    return ProjectionHealthSnapshot(
        lag_events=int(lag_events or 0),
        lag_seconds=_age_seconds(now, oldest_lagging),
        oldest_pending_age_seconds=_age_seconds(now, oldest_pending),
        processing_count=int(status_counts.get("processing", 0)),
        expired_lease_count=int(expired_lease_count or 0),
        dead_letter_count=int(status_counts.get("dead_letter", 0)),
        retry_count=int(retry_count or 0),
        rebuild_status_counts=rebuild_status_counts,
        reconciliation_mismatch_count=int(mismatch_count or 0),
        wakeup_failure_count=max(0, int(wakeup_failures)),
    )


def _age_seconds(now: datetime, value: datetime | None) -> float:
    if value is None:
        return 0.0
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return max(0.0, (now - value).total_seconds())


def _delivery_filters(scan_filter: ScanFilter):
    return tuple(
        column == value
        for column, value in (
            (ProjectionDelivery.tenant_id, scan_filter.tenant_id),
            (ProjectionDelivery.project_id, scan_filter.project_id),
            (ProjectionDelivery.projector_id, scan_filter.projector_id),
            (ProjectionDelivery.barrier_kind, scan_filter.barrier_kind),
        )
        if value is not None
    )


def _partition_filters(
    scan_filter: ScanFilter, registry: ProjectorRegistry
):
    filters = [
        column == value
        for column, value in (
            (ProjectionPartition.tenant_id, scan_filter.tenant_id),
            (ProjectionPartition.project_id, scan_filter.project_id),
            (ProjectionPartition.projector_id, scan_filter.projector_id),
        )
        if value is not None
    ]
    if scan_filter.barrier_kind is not None:
        projector_ids = tuple(
            spec.projector_id
            for spec in registry.all()
            if spec.barrier_kind == scan_filter.barrier_kind
        )
        filters.append(ProjectionPartition.projector_id.in_(projector_ids))
    return tuple(filters)


def _rebuild_filters(scan_filter: ScanFilter):
    return tuple(
        column == value
        for column, value in (
            (ProjectionRebuildRun.tenant_id, scan_filter.tenant_id),
            (ProjectionRebuildRun.project_id, scan_filter.project_id),
            (ProjectionRebuildRun.projector_id, scan_filter.projector_id),
        )
        if value is not None
    )


def _reconciliation_filters(scan_filter: ScanFilter):
    return tuple(
        column == value
        for column, value in (
            (ProjectionReconciliation.tenant_id, scan_filter.tenant_id),
            (ProjectionReconciliation.project_id, scan_filter.project_id),
            (ProjectionReconciliation.projector_id, scan_filter.projector_id),
        )
        if value is not None
    )
