"""Read-after-write barrier for the P2 critical projection manifest."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import (
    CanonicalCommit,
    ProjectionDelivery,
    ProjectionPartition,
)
from .projection_registry import DEFAULT_PROJECTOR_REGISTRY


class ProjectionBarrier:
    def __init__(
        self, session: Session, tenant_id: str, project_id: str
    ) -> None:
        if not tenant_id or not project_id:
            raise ValueError("tenant_id and project_id are required")
        self.session = session
        self.tenant_id = tenant_id
        self.project_id = project_id

    def ensure_ready(self, commit_id: str) -> str:
        commit = self.session.scalar(
            select(CanonicalCommit).where(
                CanonicalCommit.id == commit_id,
                CanonicalCommit.tenant_id == self.tenant_id,
                CanonicalCommit.project_id == self.project_id,
                CanonicalCommit.status == "committed",
            )
        )
        if commit is None:
            return "failed"
        critical_specs = tuple(
            spec
            for spec in DEFAULT_PROJECTOR_REGISTRY.all()
            if spec.barrier_kind == "critical"
        )
        deliveries = {
            delivery.projector_id: delivery
            for delivery in self.session.scalars(
                select(ProjectionDelivery).where(
                    ProjectionDelivery.tenant_id == self.tenant_id,
                    ProjectionDelivery.project_id == self.project_id,
                    ProjectionDelivery.stream_position == commit.stream_position,
                    ProjectionDelivery.barrier_kind == "critical",
                )
            )
        }
        partitions = {
            partition.projector_id: partition
            for partition in self.session.scalars(
                select(ProjectionPartition).where(
                    ProjectionPartition.tenant_id == self.tenant_id,
                    ProjectionPartition.project_id == self.project_id,
                )
            )
        }
        if set(deliveries) != {spec.projector_id for spec in critical_specs}:
            return "failed"
        if any(delivery.status == "dead_letter" for delivery in deliveries.values()):
            return "failed"
        for spec in critical_specs:
            delivery = deliveries[spec.projector_id]
            partition = partitions.get(spec.projector_id)
            if (
                delivery.projector_version != spec.version
                or delivery.barrier_kind != spec.barrier_kind
                or partition is None
                or partition.projector_version != spec.version
                or partition.enrollment_status != "active"
                or partition.runtime_status != "active"
            ):
                return "pending"
            if (
                delivery.status != "published"
                or delivery.last_error_message is not None
                or partition.last_published_position < commit.stream_position
            ):
                return "pending"
        return "ready"

