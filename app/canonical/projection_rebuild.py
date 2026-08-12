"""Durable, projection-scoped maintenance rebuild state machine."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable, Mapping
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from .hashing import sha256_json
from .models import (
    CanonicalCommit,
    OutboxEvent,
    ProjectionAttempt,
    ProjectionDelivery,
    ProjectionPartition,
    ProjectionRebuildRun,
)
from .projection_locks import ProjectionLockScope, ProjectionMaintenanceLocks
from .projection_manifest import build_manifest, reconcile_projection
from .projection_ports import ProjectionAdapter, ProjectionScope, RebuildStatus
from .projection_registry import DEFAULT_PROJECTOR_REGISTRY, ProjectorRegistry
from .projection_replay import CanonicalProjectionReplay


_TRANSITIONS = {
    "requested": "pausing",
    "pausing": "clearing",
    "clearing": "rebuilding",
    "rebuilding": "reconciling",
    "reconciling": "catching_up",
    "catching_up": "completed",
}


class ProjectionRebuildService:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        adapters: Mapping[str, ProjectionAdapter],
        *,
        registry: ProjectorRegistry = DEFAULT_PROJECTOR_REGISTRY,
        lease_seconds: int = 300,
        failure_hook: Callable[[str], None] | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.adapters = dict(adapters)
        self.registry = registry
        self.lease_seconds = lease_seconds
        self.failure_hook = failure_hook

    def _stage(self, stage: str) -> None:
        if self.failure_hook is not None:
            self.failure_hook(stage)

    def start_maintenance(
        self,
        scope: ProjectionScope,
        projector_id: str,
        *,
        operator_id: str,
        reason: str,
    ) -> str:
        if not operator_id.strip() or not reason.strip():
            raise ValueError("operator_id and reason are required")
        spec = self.registry.get(projector_id)
        if projector_id not in self.adapters:
            raise ValueError(f"adapter is not registered: {projector_id}")
        with self.session_factory() as session:
            active = session.scalar(
                select(ProjectionRebuildRun).where(
                    ProjectionRebuildRun.tenant_id == scope.tenant_id,
                    ProjectionRebuildRun.project_id == scope.project_id,
                    ProjectionRebuildRun.projector_id == projector_id,
                    ProjectionRebuildRun.status.in_(
                        (
                            "requested",
                            "pausing",
                            "clearing",
                            "rebuilding",
                            "reconciling",
                            "catching_up",
                        )
                    ),
                )
            )
            if active is not None:
                raise ValueError("an active rebuild already exists for this partition")
            watermark = session.scalar(
                select(func.max(CanonicalCommit.stream_position)).where(
                    CanonicalCommit.tenant_id == scope.tenant_id,
                    CanonicalCommit.project_id == scope.project_id,
                    CanonicalCommit.status == "committed",
                )
            ) or 0
            partition = session.scalar(
                select(ProjectionPartition).where(
                    ProjectionPartition.tenant_id == scope.tenant_id,
                    ProjectionPartition.project_id == scope.project_id,
                    ProjectionPartition.projector_id == projector_id,
                )
            )
            if partition is None:
                raise ValueError("projection partition is missing")
            run_id = str(uuid4())
            run = ProjectionRebuildRun(
                id=run_id,
                tenant_id=scope.tenant_id,
                project_id=scope.project_id,
                projector_id=projector_id,
                projector_version=spec.version,
                run_kind="maintenance",
                status="requested",
                watermark_position=watermark,
                checkpoint_position=0,
                operator_id=operator_id,
                operator_reason=reason,
            )
            session.add(run)
            partition.runtime_status = "pause_requested"
            partition.maintenance_requested_at = datetime.now(timezone.utc)
            partition.active_rebuild_run_id = run_id
            session.commit()
            return run_id

    def resume(self, run_id: str, *, worker_id: str) -> RebuildStatus:
        if not worker_id.strip():
            raise ValueError("worker_id is required")
        with self.session_factory() as session:
            run = session.get(ProjectionRebuildRun, run_id)
            if run is None:
                raise ValueError("rebuild run not found")
            scope = ProjectionScope(run.tenant_id, run.project_id)
            lock_scope = ProjectionLockScope(
                run.tenant_id, run.project_id, run.projector_id
            )
            locks = ProjectionMaintenanceLocks(session.get_bind())
            with locks.exclusive(lock_scope):
                self._acquire_run_lease(run, worker_id)
                lease_token = run.lease_token
                session.commit()
                while run.status != "completed":
                    if run.status == "reconciliation_failed":
                        break
                    progressed = self._advance_one(
                        session, run, scope, worker_id, lease_token
                    )
                    session.commit()
                    if not progressed:
                        break
                run.lease_token = None
                run.leased_by = None
                run.leased_until = None
                session.commit()
                return self._status(run)

    def status(self, run_id: str) -> RebuildStatus:
        with self.session_factory() as session:
            run = session.get(ProjectionRebuildRun, run_id)
            if run is None:
                raise ValueError("rebuild run not found")
            return self._status(run)

    def _acquire_run_lease(self, run: ProjectionRebuildRun, worker_id: str) -> None:
        now = datetime.now(timezone.utc)
        leased_until = run.leased_until
        if leased_until is not None and leased_until.tzinfo is None:
            leased_until = leased_until.replace(tzinfo=timezone.utc)
        if (
            leased_until is not None
            and leased_until > now
            and run.leased_by not in (None, worker_id)
        ):
            raise RuntimeError("rebuild run lease is held by another worker")
        run.lease_token = uuid4().hex
        run.leased_by = worker_id
        run.leased_until = now + timedelta(seconds=self.lease_seconds)

    def _advance_one(
        self,
        session: Session,
        run: ProjectionRebuildRun,
        scope: ProjectionScope,
        worker_id: str,
        lease_token: str,
    ) -> bool:
        current = run.status
        next_status = _TRANSITIONS.get(current)
        if next_status is None:
            raise ValueError(f"invalid rebuild transition from {current}")
        if run.leased_by != worker_id or run.lease_token != lease_token:
            raise RuntimeError("rebuild run lease is stale")
        if run.projector_version != self.registry.get(run.projector_id).version:
            raise RuntimeError("rebuild projector version no longer matches registry")
        if self.adapters[run.projector_id].spec.version != run.projector_version:
            raise RuntimeError("rebuild adapter version no longer matches pinned run")
        self._stage(f"before_{current}")
        adapter = self.adapters[run.projector_id]
        if current == "requested":
            partition = self._partition(session, run)
            partition.runtime_status = "maintenance"
        elif current == "pausing":
            adapter.clear(scope)
            run.checkpoint_position = 0
        elif current == "clearing":
            replay = CanonicalProjectionReplay(session, registry=self.registry)
            messages = tuple(
                replay.iter_messages(scope, run.projector_id, 0, run.watermark_position)
            )
            for message in messages:
                if message.stream_position <= run.checkpoint_position:
                    continue
                adapter.apply(message)
                self._stage("after_batch_apply_before_checkpoint")
                run.checkpoint_position = message.stream_position
                run.processed_record_count += 1
                session.flush()
                session.commit()
                self._stage("after_batch_checkpoint")
            run.expected_record_count = len(adapter.expected_records(messages))
            expected_records = adapter.expected_records(messages)
            expected = build_manifest(
                scope,
                self.registry.get(run.projector_id),
                run.watermark_position,
                expected_records,
                ledger=[message.payload.get("ledger_events", []) for message in messages],
            )
            run.expected_manifest_json = expected.model_dump(mode="json")
            run.expected_manifest_digest = sha256_json(run.expected_manifest_json)
        elif current == "rebuilding":
            expected = self._manifest(run.expected_manifest_json)
            messages = tuple(
                CanonicalProjectionReplay(session, registry=self.registry).iter_messages(
                    scope, run.projector_id, 0, run.watermark_position
                )
            )
            ledger = [message.payload.get("ledger_events", []) for message in messages]
            actual_records = adapter.actual_records(scope)
            actual = build_manifest(
                scope,
                self.registry.get(run.projector_id),
                run.watermark_position,
                actual_records,
                ledger=ledger,
            )
            result = reconcile_projection(
                expected,
                actual,
                expected_records=adapter.expected_records(messages),
                actual_records=actual_records,
                session=session,
                rebuild_run_id=run.id,
            )
            run.actual_manifest_json = actual.model_dump(mode="json")
            run.actual_manifest_digest = sha256_json(run.actual_manifest_json)
            if result.status == "mismatch":
                run.status = "reconciliation_failed"
                run.error_code = "projection_reconciliation_mismatch"
                run.error_message = "expected and actual projection manifests differ"
                session.flush()
                self._stage("after_reconciliation_failure")
                return True
        elif current == "reconciling":
            self._catch_up_partition(session, run)
            self._stage("after_catch_up")
        elif current == "catching_up":
            head = session.scalar(
                select(func.max(CanonicalCommit.stream_position)).where(
                    CanonicalCommit.tenant_id == run.tenant_id,
                    CanonicalCommit.project_id == run.project_id,
                    CanonicalCommit.status == "committed",
                )
            ) or 0
            partition = self._partition(session, run)
            if partition.last_published_position < head:
                return False
        run.status = next_status
        session.flush()
        self._stage(f"after_{next_status}")
        return True

    @staticmethod
    def _partition(session: Session, run: ProjectionRebuildRun) -> ProjectionPartition:
        partition = session.scalar(
            select(ProjectionPartition).where(
                ProjectionPartition.tenant_id == run.tenant_id,
                ProjectionPartition.project_id == run.project_id,
                ProjectionPartition.projector_id == run.projector_id,
            )
        )
        if partition is None:
            raise ValueError("projection partition is missing")
        if partition.active_rebuild_run_id not in (None, run.id):
            raise ValueError("projection partition is owned by another rebuild")
        return partition

    def _catch_up_partition(self, session: Session, run: ProjectionRebuildRun) -> None:
        partition = self._partition(session, run)
        deliveries = list(
            session.scalars(
                select(ProjectionDelivery).where(
                    ProjectionDelivery.tenant_id == run.tenant_id,
                    ProjectionDelivery.project_id == run.project_id,
                    ProjectionDelivery.projector_id == run.projector_id,
                    ProjectionDelivery.stream_position <= run.watermark_position,
                ).order_by(ProjectionDelivery.stream_position)
            ).all()
        )
        now = datetime.now(timezone.utc)
        for delivery in deliveries:
            receipt = {
                "rebuild_run_id": run.id,
                "projector_id": run.projector_id,
                "stream_position": delivery.stream_position,
                "rebuild": True,
            }
            session.execute(
                update(ProjectionDelivery)
                .where(ProjectionDelivery.id == delivery.id)
                .values(
                    status="published",
                    published_at=now,
                    receipt_json=receipt,
                    receipt_digest=sha256_json(receipt),
                    lease_token=None,
                    leased_by=None,
                    leased_until=None,
                    updated_at=now,
                )
            )
            session.execute(
                update(ProjectionAttempt)
                .where(
                    ProjectionAttempt.delivery_id == delivery.id,
                    ProjectionAttempt.outcome == "claimed",
                )
                .values(
                    outcome="superseded",
                    finished_at=now,
                    operator_id=run.operator_id,
                    operator_reason=run.operator_reason,
                    rebuild_run_id=run.id,
                )
            )
            existing_superseded = session.scalar(
                select(ProjectionAttempt.id).where(
                    ProjectionAttempt.delivery_id == delivery.id,
                    ProjectionAttempt.outcome == "superseded",
                    ProjectionAttempt.rebuild_run_id == run.id,
                )
            )
            if existing_superseded is None:
                attempt_number = delivery.attempt_count + 1
                session.add(
                    ProjectionAttempt(
                        id=str(uuid4()),
                        delivery_id=delivery.id,
                        attempt_number=attempt_number,
                        lease_token=run.lease_token,
                        leased_by=run.leased_by,
                        trigger_source="rebuild",
                        outcome="superseded",
                        started_at=now,
                        finished_at=now,
                        operator_id=run.operator_id,
                        operator_reason=run.operator_reason,
                        rebuild_run_id=run.id,
                    )
                )
                delivery.attempt_count = attempt_number
            session.execute(
                update(OutboxEvent)
                .where(OutboxEvent.id == delivery.outbox_event_id)
                .values(status="published", published_at=now, updated_at=now)
            )
        latest = deliveries[-1] if deliveries else None
        partition.last_published_position = run.watermark_position
        partition.last_published_event_id = latest.outbox_event_id if latest else None
        partition.runtime_status = "active"
        partition.active_rebuild_run_id = None
        partition.resumed_at = now

    @staticmethod
    def _manifest(raw):
        from .projection_ports import ProjectionManifest

        if raw is None:
            raise ValueError("expected manifest is missing")
        return ProjectionManifest.model_validate(raw)

    @staticmethod
    def _status(run: ProjectionRebuildRun) -> RebuildStatus:
        return RebuildStatus(
            run_id=run.id,
            run_kind=run.run_kind,
            status=run.status,
            checkpoint_position=run.checkpoint_position,
            watermark_position=run.watermark_position,
            activation_after_position=run.activation_after_position,
        )
