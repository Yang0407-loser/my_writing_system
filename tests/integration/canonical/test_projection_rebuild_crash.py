from __future__ import annotations

import pytest
from sqlalchemy import select

from app.canonical.commit_service import CanonicalCommitService
from app.canonical.database import build_session_factory
from app.canonical.models import ProjectionPartition, ProjectionRebuildRun
from app.canonical.projection_ports import ProjectionScope
from app.canonical.projection_rebuild import ProjectionRebuildService
from app.projections.analytics import AnalyticsProjectionAdapter
from tests.unit.canonical.test_commit_service import _prepared


pytest_plugins = ("tests.fixtures_canonical",)


def test_rebuild_resume_after_phase_crash(canonical_session):
    scope = ProjectionScope("tenant-1", "project-1")
    CanonicalCommitService(canonical_session, scope.tenant_id, scope.project_id).commit(
        _prepared(canonical_session), "rebuild-crash"
    )
    factory = build_session_factory(canonical_session.get_bind())
    adapter = AnalyticsProjectionAdapter(factory, scope, "task-1")
    crashed = {"done": False}

    def hook(stage):
        if stage == "after_clearing" and not crashed["done"]:
            crashed["done"] = True
            raise RuntimeError("injected crash")

    service = ProjectionRebuildService(
        factory, {"analytics": adapter}, failure_hook=hook, lease_seconds=0
    )
    run_id = service.start_maintenance(
        scope, "analytics", operator_id="operator-1", reason="crash"
    )
    with pytest.raises(RuntimeError, match="injected crash"):
        service.resume(run_id, worker_id="worker-1")
    resumed = ProjectionRebuildService(factory, {"analytics": adapter})
    assert resumed.resume(run_id, worker_id="worker-2").status == "completed"


def test_reconciliation_mismatch_keeps_maintenance_and_cursor_unchanged(
    canonical_session,
):
    scope = ProjectionScope("tenant-1", "project-1")
    CanonicalCommitService(canonical_session, scope.tenant_id, scope.project_id).commit(
        _prepared(canonical_session), "rebuild-mismatch"
    )
    factory = build_session_factory(canonical_session.get_bind())
    real = AnalyticsProjectionAdapter(factory, scope, "task-1")

    class CorruptingAdapter:
        spec = real.spec

        def apply(self, message):
            return real.apply(message)

        def clear(self, item_scope):
            return real.clear(item_scope)

        def expected_records(self, messages):
            return real.expected_records(messages)

        def actual_records(self, item_scope):
            records = real.actual_records(item_scope)
            return tuple(
                record.model_copy(update={"payload": {**record.payload, "corrupt": True}})
                for record in records
            )

    service = ProjectionRebuildService(factory, {"analytics": CorruptingAdapter()})
    run_id = service.start_maintenance(
        scope, "analytics", operator_id="operator-1", reason="mismatch"
    )
    result = service.resume(run_id, worker_id="worker-1")
    assert result.status == "reconciliation_failed"
    with factory() as session:
        partition = session.scalar(
            select(ProjectionPartition).where(
                ProjectionPartition.projector_id == "analytics"
            )
        )
        run = session.get(ProjectionRebuildRun, run_id)
        assert partition.runtime_status == "maintenance"
        assert partition.last_published_position == 0
        assert run.error_code == "projection_reconciliation_mismatch"
