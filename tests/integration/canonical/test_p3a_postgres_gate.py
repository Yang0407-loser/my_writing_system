from __future__ import annotations

import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fakeredis import FakeRedis
from sqlalchemy import func, select

from app.blackboard import Blackboard
from app.canonical.commit_service import CanonicalCommitService
from app.canonical.database import build_engine, build_session_factory
from app.canonical.models import (
    CanonicalCommit,
    OutboxEvent,
    ProjectionDelivery,
    ProjectionPartition,
    ProjectionRebuildRun,
)
from app.canonical.projection_barrier import ProjectionBarrier
from app.canonical.projection_delivery import ProjectionDeliveryStore, ScanFilter
from app.canonical.projection_health import projection_health_snapshot
from app.canonical.projection_ports import ProjectionScope
from app.canonical.projection_rebuild import ProjectionRebuildService
from app.canonical.projection_registry import DEFAULT_PROJECTOR_REGISTRY
from app.canonical.projection_worker import ProjectionWorker
from app.projections.analytics import AnalyticsProjectionAdapter
from app.projections.factory import build_projection_adapters
from tests.integration.canonical.helpers import build_prepared, seed_project
from tests.unit.projections.test_critical_projection_adapters import FakeVectorStore


pytestmark = pytest.mark.postgres


def _scope(database_url: str, label: str) -> ProjectionScope:
    run_id = os.getenv("P3A_GATE_RUN_ID", uuid4().hex[:12])
    scenario = os.getenv("P3A_GATE_SCENARIO", "direct")
    suffix = f"{run_id[:8]}-{scenario[:4]}-{label[:4]}"[:20]
    scope = ProjectionScope(f"t-{suffix}", f"p-{suffix}")
    seed_project(database_url, scope.tenant_id, scope.project_id, subsection_count=2)
    return scope


def _factory(database_url: str):
    engine = build_engine(database_url)
    return engine, build_session_factory(engine)


def _commit(database_url: str, scope: ProjectionScope, key: str, *, ordinal=1):
    engine, factory = _factory(database_url)
    try:
        with factory() as session:
            return CanonicalCommitService(
                session, scope.tenant_id, scope.project_id
            ).commit(
                build_prepared(
                    database_url,
                    scope.tenant_id,
                    scope.project_id,
                    ordinal=ordinal,
                    draft=f"P3A gate draft {key}",
                    attempt_id=key,
                ),
                key,
            )
    finally:
        engine.dispose()


def _adapters(factory, scope: ProjectionScope, tmp_path):
    board = Blackboard.__new__(Blackboard)
    board._redis = FakeRedis()
    return build_projection_adapters(
        factory,
        scope=scope,
        task_id=f"task-{scope.project_id}",
        blackboard=board,
        vector_store=FakeVectorStore(),
        markdown_root=str(tmp_path / "markdown"),
    )


def test_postgres_seven_projection_delete_rebuild_and_final_lag(
    postgres_database_url, tmp_path
):
    scope = _scope(postgres_database_url, "seven")
    _commit(postgres_database_url, scope, "seven-rebuild")
    engine, factory = _factory(postgres_database_url)
    try:
        adapters = _adapters(factory, scope, tmp_path)
        service = ProjectionRebuildService(factory, adapters)
        for projector_id in adapters:
            run_id = service.start_maintenance(
                scope,
                projector_id,
                operator_id="p3a-gate",
                reason="delete, rebuild and reconcile",
            )
            status = service.resume(run_id, worker_id=f"gate-{projector_id}")
            assert status.status == "completed"

        with factory() as session:
            runs = session.scalars(
                select(ProjectionRebuildRun).where(
                    ProjectionRebuildRun.tenant_id == scope.tenant_id,
                    ProjectionRebuildRun.project_id == scope.project_id,
                )
            ).all()
            assert {run.projector_id for run in runs} == {
                spec.projector_id for spec in DEFAULT_PROJECTOR_REGISTRY.all()
            }
            assert all(
                run.expected_manifest_digest == run.actual_manifest_digest
                and run.status == "completed"
                for run in runs
            )
            assert projection_health_snapshot(
                session,
                ScanFilter(
                    tenant_id=scope.tenant_id,
                    project_id=scope.project_id,
                ),
            ).lag_events == 0
    finally:
        engine.dispose()


def test_postgres_duplicate_wakeup_and_outage_scanner_recovery(
    postgres_database_url,
):
    scope = _scope(postgres_database_url, "wakeup")
    first = _commit(postgres_database_url, scope, "wakeup-1")
    _commit(postgres_database_url, scope, "wakeup-2", ordinal=2)
    engine, factory = _factory(postgres_database_url)
    try:
        adapter = AnalyticsProjectionAdapter(
            factory, scope, f"task-{scope.project_id}"
        )
        worker = ProjectionWorker(
            factory,
            {"analytics": adapter},
            worker_id="p3a-independent-scanner",
        )
        scan_filter = ScanFilter(
            tenant_id=scope.tenant_id,
            project_id=scope.project_id,
            projector_id="analytics",
            limit=100,
        )
        for _ in range(50):
            worker.scan_once(scan_filter)
        with factory() as session:
            published = session.scalar(
                select(func.count()).select_from(ProjectionDelivery).where(
                    ProjectionDelivery.tenant_id == scope.tenant_id,
                    ProjectionDelivery.project_id == scope.project_id,
                    ProjectionDelivery.projector_id == "analytics",
                    ProjectionDelivery.status == "published",
                )
            )
            assert published == 2
            assert session.scalar(
                select(func.count()).select_from(CanonicalCommit).where(
                    CanonicalCommit.tenant_id == scope.tenant_id,
                    CanonicalCommit.project_id == scope.project_id,
                )
            ) == 2
            assert first.commit_id
            assert projection_health_snapshot(
                session,
                ScanFilter(
                    tenant_id=scope.tenant_id,
                    project_id=scope.project_id,
                    projector_id="analytics",
                ),
            ).lag_events == 0
    finally:
        engine.dispose()


def test_postgres_dead_letters_barrier_health_and_audited_requeue(
    postgres_database_url,
):
    scope = _scope(postgres_database_url, "deadletter")
    result = _commit(postgres_database_url, scope, "deadletter")
    engine, factory = _factory(postgres_database_url)
    try:
        with factory() as session:
            critical = session.scalar(
                select(ProjectionDelivery).where(
                    ProjectionDelivery.tenant_id == scope.tenant_id,
                    ProjectionDelivery.projector_id == "legacy_world_event",
                )
            )
            nonblocking = session.scalar(
                select(ProjectionDelivery).where(
                    ProjectionDelivery.tenant_id == scope.tenant_id,
                    ProjectionDelivery.projector_id == "analytics",
                )
            )
            critical.status = "dead_letter"
            nonblocking.status = "dead_letter"
            session.commit()
            assert ProjectionBarrier(
                session, scope.tenant_id, scope.project_id
            ).ensure_ready(result.commit_id) == "failed"
            health = projection_health_snapshot(
                session,
                ScanFilter(tenant_id=scope.tenant_id, project_id=scope.project_id),
            )
            assert health.dead_letter_count == 2
            assert ProjectionDeliveryStore(session).requeue_dead_letter(
                nonblocking.id,
                "p3a-gate",
                "audited recovery",
                now=datetime.now(timezone.utc),
            )
            assert nonblocking.status == "pending"
    finally:
        engine.dispose()


def test_postgres_rebuild_crash_resume_commits_during_rebuild_and_mismatch(
    postgres_database_url,
):
    scope = _scope(postgres_database_url, "rebuild")
    first = _commit(postgres_database_url, scope, "rebuild-1")
    engine, factory = _factory(postgres_database_url)
    try:
        adapter = AnalyticsProjectionAdapter(
            factory, scope, f"task-{scope.project_id}"
        )
        crashed = False

        def crash_after_clear(stage):
            nonlocal crashed
            if stage == "after_clearing" and not crashed:
                crashed = True
                raise RuntimeError("injected gate crash")

        service = ProjectionRebuildService(
            factory,
            {"analytics": adapter},
            failure_hook=crash_after_clear,
            lease_seconds=0,
        )
        run_id = service.start_maintenance(
            scope, "analytics", operator_id="p3a-gate", reason="crash resume"
        )
        with pytest.raises(RuntimeError, match="injected gate crash"):
            service.resume(run_id, worker_id="crashing-worker")
        _commit(postgres_database_url, scope, "rebuild-2", ordinal=2)
        resumed = ProjectionRebuildService(factory, {"analytics": adapter})
        assert resumed.resume(run_id, worker_id="resume-worker").status == "catching_up"
        assert ProjectionWorker(
            factory,
            {"analytics": adapter},
            worker_id="catch-up-worker",
        ).scan_once(
            ScanFilter(
                tenant_id=scope.tenant_id,
                project_id=scope.project_id,
                projector_id="analytics",
                limit=10,
            )
        ).published == 1
        assert resumed.resume(run_id, worker_id="resume-worker").status == "completed"

        class CorruptingAdapter:
            spec = adapter.spec

            def apply(self, message):
                return adapter.apply(message)

            def clear(self, item_scope):
                return adapter.clear(item_scope)

            def expected_records(self, messages):
                return adapter.expected_records(messages)

            def actual_records(self, item_scope):
                return tuple(
                    record.model_copy(
                        update={"payload": {**record.payload, "corrupt": True}}
                    )
                    for record in adapter.actual_records(item_scope)
                )

        mismatch = ProjectionRebuildService(
            factory, {"analytics": CorruptingAdapter()}
        )
        mismatch_id = mismatch.start_maintenance(
            scope, "analytics", operator_id="p3a-gate", reason="corrupt manifest"
        )
        assert mismatch.resume(
            mismatch_id, worker_id="mismatch-worker"
        ).status == "reconciliation_failed"
        with factory() as session:
            assert session.get(CanonicalCommit, first.commit_id) is not None
    finally:
        engine.dispose()


def test_postgres_new_projector_bootstrap_has_no_history_backfill(
    postgres_database_url,
):
    scope = _scope(postgres_database_url, "bootstrap")
    _commit(postgres_database_url, scope, "bootstrap-history")
    engine, factory = _factory(postgres_database_url)
    try:
        with factory() as session:
            partition = session.scalar(
                select(ProjectionPartition).where(
                    ProjectionPartition.tenant_id == scope.tenant_id,
                    ProjectionPartition.project_id == scope.project_id,
                    ProjectionPartition.projector_id == "analytics",
                )
            )
            partition.enrollment_status = "disabled"
            partition.activation_after_position = None
            session.execute(
                ProjectionDelivery.__table__.delete().where(
                    ProjectionDelivery.tenant_id == scope.tenant_id,
                    ProjectionDelivery.project_id == scope.project_id,
                    ProjectionDelivery.projector_id == "analytics",
                )
            )
            session.execute(
                OutboxEvent.__table__.delete().where(
                    OutboxEvent.tenant_id == scope.tenant_id,
                    OutboxEvent.project_id == scope.project_id,
                    OutboxEvent.projection_name == "analytics",
                )
            )
            session.commit()
        adapter = AnalyticsProjectionAdapter(
            factory, scope, f"task-{scope.project_id}"
        )
        service = ProjectionRebuildService(factory, {"analytics": adapter})
        run_id = service.start_bootstrap(
            scope, "analytics", operator_id="p3a-gate", reason="new projector"
        )
        assert service.resume(run_id, worker_id="bootstrap-worker").status == "completed"
        with factory() as session:
            assert session.scalar(
                select(func.count()).select_from(OutboxEvent).where(
                    OutboxEvent.tenant_id == scope.tenant_id,
                    OutboxEvent.project_id == scope.project_id,
                    OutboxEvent.projection_name == "analytics",
                )
            ) == 0
            run = session.get(ProjectionRebuildRun, run_id)
            assert run.activation_after_position == 1
            assert run.expected_manifest_digest == run.actual_manifest_digest
    finally:
        engine.dispose()


def test_postgres_activation_gap_race_freezes_threshold_and_reconciles_gap(
    postgres_database_url,
):
    scope = _scope(postgres_database_url, "activation")
    engine, factory = _factory(postgres_database_url)
    try:
        with factory() as session:
            partition = session.scalar(
                select(ProjectionPartition).where(
                    ProjectionPartition.tenant_id == scope.tenant_id,
                    ProjectionPartition.project_id == scope.project_id,
                    ProjectionPartition.projector_id == "analytics",
                )
            )
            partition.enrollment_status = "disabled"
            session.commit()
        first = _commit(postgres_database_url, scope, "activation-first")
        adapter = AnalyticsProjectionAdapter(
            factory, scope, f"task-{scope.project_id}"
        )
        paused = {"before": True, "after": True}

        def pause_before_activation(stage):
            if stage == "after_reconciling" and paused["before"]:
                paused["before"] = False
                raise RuntimeError("pause before activation")
            if stage == "after_activation_commit" and paused["after"]:
                paused["after"] = False
                raise RuntimeError("pause after activation")

        service = ProjectionRebuildService(
            factory,
            {"analytics": adapter},
            failure_hook=pause_before_activation,
            lease_seconds=0,
        )
        run_id = service.start_bootstrap(
            scope, "analytics", operator_id="p3a-gate", reason="activation race"
        )
        with pytest.raises(RuntimeError, match="pause before activation"):
            service.resume(run_id, worker_id="bootstrap-worker")
        _commit(postgres_database_url, scope, "activation-gap", ordinal=2)
        with pytest.raises(RuntimeError, match="pause after activation"):
            service.resume(run_id, worker_id="bootstrap-worker")
        _commit(postgres_database_url, scope, "activation-later", ordinal=2)
        with factory() as session:
            run = session.get(ProjectionRebuildRun, run_id)
            assert run.activation_after_position == 2
            assert session.scalar(
                select(func.count()).select_from(OutboxEvent).where(
                    OutboxEvent.tenant_id == scope.tenant_id,
                    OutboxEvent.project_id == scope.project_id,
                    OutboxEvent.projection_name == "analytics",
                    OutboxEvent.stream_position == 2,
                )
            ) == 0
            assert session.scalar(
                select(func.count()).select_from(OutboxEvent).where(
                    OutboxEvent.tenant_id == scope.tenant_id,
                    OutboxEvent.project_id == scope.project_id,
                    OutboxEvent.projection_name == "analytics",
                    OutboxEvent.stream_position == 3,
                )
            ) == 1
        blocked = ProjectionWorker(
            factory,
            {"analytics": adapter},
            worker_id="activation-scanner",
        ).scan_once(
            ScanFilter(
                tenant_id=scope.tenant_id,
                project_id=scope.project_id,
                projector_id="analytics",
                limit=10,
            )
        )
        assert blocked.published == 0
        assert service.resume(run_id, worker_id="bootstrap-worker").status == "catching_up"
        published = ProjectionWorker(
            factory,
            {"analytics": adapter},
            worker_id="activation-scanner",
        ).scan_once(
            ScanFilter(
                tenant_id=scope.tenant_id,
                project_id=scope.project_id,
                projector_id="analytics",
                limit=10,
            )
        )
        assert published.published == 1
        assert service.resume(run_id, worker_id="bootstrap-worker").status == "completed"
        with factory() as session:
            partition = session.scalar(
                select(ProjectionPartition).where(
                    ProjectionPartition.tenant_id == scope.tenant_id,
                    ProjectionPartition.project_id == scope.project_id,
                    ProjectionPartition.projector_id == "analytics",
                )
            )
            assert partition.last_published_position == 3
            assert first.commit_id
    finally:
        engine.dispose()
