"""Read-after-write barrier for the P2 critical projection manifest."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import CanonicalCommit, OutboxEvent


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
        statuses = tuple(
            self.session.scalars(
                select(OutboxEvent.status).where(
                    OutboxEvent.commit_id == commit_id,
                    OutboxEvent.tenant_id == self.tenant_id,
                    OutboxEvent.project_id == self.project_id,
                    OutboxEvent.barrier_kind == "critical",
                )
            ).all()
        )
        if not statuses or "failed" in statuses:
            return "failed"
        if all(status == "published" for status in statuses):
            return "ready"
        return "pending"

