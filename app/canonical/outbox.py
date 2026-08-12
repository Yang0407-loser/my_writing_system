"""Compatibility facade over the shared fenced projection Delivery worker."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from sqlalchemy.orm import Session

from .projection_delivery import ScanFilter
from .projection_ports import ProjectionMessage, ProjectionPort, ProjectionReceipt
from .projection_registry import DEFAULT_PROJECTOR_REGISTRY, ProjectorSpec
from .projection_worker import ProjectionWorker, ScanSummary


DispatchSummary = dict[str, int]


@dataclass(frozen=True)
class _CompatibilityExecutor:
    spec: ProjectorSpec
    projector: ProjectionPort

    def apply(self, message: ProjectionMessage) -> ProjectionReceipt:
        receipt = self.projector(message)
        if not isinstance(receipt, ProjectionReceipt):
            raise TypeError("compatibility projector must return ProjectionReceipt")
        return receipt


class OutboxDispatcher:
    """Temporary P2 signatures backed solely by authoritative Deliveries."""

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
            ScanFilter(
                tenant_id=self.tenant_id,
                project_id=self.project_id,
                commit_id=commit_id,
                barrier_kind="critical",
                limit=100,
            )
        )

    def dispatch_non_blocking(self, commit_id: str) -> DispatchSummary:
        return self._dispatch(
            ScanFilter(
                tenant_id=self.tenant_id,
                project_id=self.project_id,
                commit_id=commit_id,
                barrier_kind="non_blocking",
                limit=100,
            )
        )

    def dispatch_pending(self, limit: int = 100) -> DispatchSummary:
        if limit < 1:
            raise ValueError("limit must be positive")
        return self._dispatch(
            ScanFilter(
                tenant_id=self.tenant_id,
                project_id=self.project_id,
                limit=limit,
            )
        )

    def _dispatch(self, scan_filter: ScanFilter) -> DispatchSummary:
        executors = {
            projector_id: _CompatibilityExecutor(
                DEFAULT_PROJECTOR_REGISTRY.get(projector_id), projector
            )
            for projector_id, projector in self.projectors.items()
            if projector_id
            in {spec.projector_id for spec in DEFAULT_PROJECTOR_REGISTRY.all()}
        }
        summary = ProjectionWorker(
            self._session_factory,
            executors,
            worker_id=(
                f"outbox-compat:{self.tenant_id}:{self.project_id}"
            ),
        ).scan_once(scan_filter)
        return self._compatibility_summary(summary)

    @staticmethod
    def _compatibility_summary(summary: ScanSummary) -> DispatchSummary:
        return {
            "published": summary.published,
            "failed": summary.retried + summary.dead_lettered,
        }
