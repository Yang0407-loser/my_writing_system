from __future__ import annotations

from sqlalchemy import select
from fakeredis import FakeRedis

from app.blackboard import Blackboard
from app.canonical.commit_service import CanonicalCommitService
from app.canonical.database import build_session_factory
from app.canonical.models import CanonicalCommit, ProjectionPartition
from app.canonical.projection_delivery import ScanFilter
from app.canonical.projection_ports import ProjectionScope
from app.canonical.projection_rebuild import ProjectionRebuildService
from app.canonical.projection_worker import ProjectionWorker
from app.projections.analytics import AnalyticsProjectionAdapter
from app.projections.factory import build_projection_adapters
from tests.unit.projections.test_critical_projection_adapters import (
    FakeVectorStore,
)
from tests.unit.canonical.test_commit_service import _prepared


pytest_plugins = ("tests.fixtures_canonical",)


def test_rebuild_replays_canon_and_advances_partition(canonical_session):
    scope = ProjectionScope("tenant-1", "project-1")
    service_commit = CanonicalCommitService(
        canonical_session, scope.tenant_id, scope.project_id
    )
    service_commit.commit(_prepared(canonical_session), "rebuild-e2e")
    factory = build_session_factory(canonical_session.get_bind())
    adapter = AnalyticsProjectionAdapter(factory, scope, "task-1")
    service = ProjectionRebuildService(factory, {"analytics": adapter})
    run_id = service.start_maintenance(
        scope, "analytics", operator_id="operator-1", reason="e2e"
    )
    result = service.resume(run_id, worker_id="worker-1")
    assert result.status == "completed"
    assert result.checkpoint_position == 1


def test_rebuild_holds_at_catching_up_until_normal_scanner_reaches_new_head(
    canonical_session,
):
    scope = ProjectionScope("tenant-1", "project-1")
    commits = CanonicalCommitService(
        canonical_session, scope.tenant_id, scope.project_id
    )
    first = commits.commit(_prepared(canonical_session), "rebuild-watermark")
    factory = build_session_factory(canonical_session.get_bind())
    adapter = AnalyticsProjectionAdapter(factory, scope, "task-1")
    service = ProjectionRebuildService(factory, {"analytics": adapter})
    run_id = service.start_maintenance(
        scope, "analytics", operator_id="operator-1", reason="catch up"
    )
    first_state = canonical_session.get(CanonicalCommit, first.commit_id)
    second = commits.commit(
        _prepared(
            canonical_session,
            subsection_id="subsection-2",
            ordinal=2,
            draft="Second accepted draft",
            base_state_version_id=first.state_version_id,
        ),
        "rebuild-after-watermark",
    )

    paused = service.resume(run_id, worker_id="rebuild-worker")
    assert paused.status == "catching_up"
    assert paused.watermark_position == first_state.stream_position

    worker = ProjectionWorker(
        factory, {"analytics": adapter}, worker_id="scanner-worker"
    )
    summary = worker.scan_once(
        ScanFilter(
            tenant_id=scope.tenant_id,
            project_id=scope.project_id,
            projector_id="analytics",
            limit=10,
        )
    )
    assert summary.published == 1
    completed = service.resume(run_id, worker_id="rebuild-worker")
    assert completed.status == "completed"
    with factory() as session:
        partition = session.scalar(
            select(ProjectionPartition).where(
                ProjectionPartition.projector_id == "analytics"
            )
        )
        assert partition.last_published_position == session.get(
            CanonicalCommit, second.commit_id
        ).stream_position


def test_all_seven_projection_adapters_rebuild_from_same_canon(
    canonical_session, tmp_path
):
    scope = ProjectionScope("tenant-1", "project-1")
    CanonicalCommitService(canonical_session, scope.tenant_id, scope.project_id).commit(
        _prepared(canonical_session), "rebuild-seven-adapters"
    )
    factory = build_session_factory(canonical_session.get_bind())
    board = Blackboard.__new__(Blackboard)
    board._redis = FakeRedis()
    adapters = build_projection_adapters(
        factory,
        scope=scope,
        task_id="task-1",
        blackboard=board,
        vector_store=FakeVectorStore(),
        markdown_root=str(tmp_path / "markdown"),
    )
    service = ProjectionRebuildService(factory, adapters)
    for projector_id in adapters:
        run_id = service.start_maintenance(
            scope,
            projector_id,
            operator_id="operator-1",
            reason="seven adapter rebuild",
        )
        result = service.resume(run_id, worker_id=f"worker-{projector_id}")
        assert result.status == "completed"
        assert result.checkpoint_position == 1
