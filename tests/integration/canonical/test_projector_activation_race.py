from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.canonical.commit_service import CanonicalCommitService
from app.canonical.database import build_session_factory
from app.canonical.models import (
    OutboxEvent,
    ProjectionDelivery,
    ProjectionPartition,
    ProjectionRebuildRun,
)
from app.canonical.projection_delivery import ProjectionDeliveryStore, ScanFilter
from app.canonical.projection_ports import ProjectionScope
from app.canonical.projection_rebuild import ProjectionRebuildService
from app.canonical.projection_registry import (
    DEFAULT_PROJECTOR_REGISTRY,
    NON_BLOCKING_RETRY,
    ProjectorRegistry,
    ProjectorSpec,
)
from app.canonical.projection_worker import ProjectionWorker
from tests.integration.canonical.bootstrap_helpers import MemoryProjectionAdapter
from tests.unit.canonical.test_commit_service import _prepared


pytest_plugins = ("tests.fixtures_canonical",)


def test_bootstrap_activation_gap_and_post_threshold_delivery(canonical_session):
    scope = ProjectionScope("tenant-1", "project-1")
    search = ProjectorSpec("search_index", "v1", "non_blocking", NON_BLOCKING_RETRY)
    registry = ProjectorRegistry((*DEFAULT_PROJECTOR_REGISTRY.all(), search))
    canonical_session.add(
        ProjectionPartition(
            id="partition-search",
            tenant_id=scope.tenant_id,
            project_id=scope.project_id,
            projector_id=search.projector_id,
            projector_version=search.version,
            enrollment_status="disabled",
            runtime_status="active",
            last_published_position=0,
            activation_after_position=None,
        )
    )
    canonical_session.commit()
    commits = CanonicalCommitService(
        canonical_session,
        scope.tenant_id,
        scope.project_id,
        projector_registry=registry,
    )
    first = commits.commit(_prepared(canonical_session), "bootstrap-w")
    factory = build_session_factory(canonical_session.get_bind())
    adapter = MemoryProjectionAdapter(search, scope, "task-1")
    stop = {"before_activation": True, "after_activation": True}

    def hook(stage):
        if stage == "after_reconciling" and stop["before_activation"]:
            stop["before_activation"] = False
            raise RuntimeError("pause before activation")
        if stage == "after_activation_commit" and stop["after_activation"]:
            stop["after_activation"] = False
            raise RuntimeError("pause after activation")

    service = ProjectionRebuildService(
        factory,
        {search.projector_id: adapter},
        registry=registry,
        failure_hook=hook,
        lease_seconds=0,
    )
    run_id = service.start_bootstrap(
        scope, search.projector_id, operator_id="operator-1", reason="activation race"
    )
    with pytest.raises(RuntimeError, match="before activation"):
        service.resume(run_id, worker_id="bootstrap-worker")

    gap = commits.commit(
        _prepared(
            canonical_session,
            subsection_id="subsection-2",
            ordinal=2,
            draft="Gap commit",
            base_state_version_id=first.state_version_id,
        ),
        "bootstrap-gap",
    )
    with pytest.raises(RuntimeError, match="after activation"):
        service.resume(run_id, worker_id="bootstrap-worker")

    with factory() as session:
        run = session.get(ProjectionRebuildRun, run_id)
        partition = session.get(ProjectionPartition, "partition-search")
        assert run.activation_after_position == 2
        assert partition.runtime_status == "catching_up"
        assert session.scalar(
            select(func.count()).select_from(OutboxEvent).where(
                OutboxEvent.projection_name == search.projector_id,
                OutboxEvent.commit_id == gap.commit_id,
            )
        ) == 0

    later = commits.commit(
        _prepared(
            canonical_session,
            draft="Later commit",
            base_revision_number=1,
            base_state_version_id=gap.state_version_id,
        ),
        "bootstrap-later",
    )
    with factory() as session:
        assert session.scalar(
            select(func.count()).select_from(OutboxEvent).where(
                OutboxEvent.projection_name == search.projector_id,
                OutboxEvent.commit_id == later.commit_id,
            )
        ) == 1
        assert session.scalar(
            select(func.count()).select_from(ProjectionDelivery).where(
                ProjectionDelivery.projector_id == search.projector_id,
                ProjectionDelivery.stream_position == 3,
            )
        ) == 1
        assert ProjectionDeliveryStore(session, registry).claim_next(
            "blocked-worker",
            ScanFilter(projector_id=search.projector_id, limit=1),
        ) is None

    resumed = ProjectionRebuildService(
        factory, {search.projector_id: adapter}, registry=registry
    )
    assert resumed.resume(run_id, worker_id="bootstrap-worker").status == "catching_up"
    assert len(adapter.actual_records(scope)) == 2
    summary = ProjectionWorker(
        factory,
        {search.projector_id: adapter},
        worker_id="scanner-worker",
        registry=registry,
    ).scan_once(ScanFilter(projector_id=search.projector_id, limit=10))
    assert summary.published == 1
    assert resumed.resume(run_id, worker_id="bootstrap-worker").status == "completed"
    with factory() as session:
        partition = session.get(ProjectionPartition, "partition-search")
        assert partition.last_published_position == 3


def test_bootstrap_gap_mismatch_recovers_without_history_backfill(canonical_session):
    scope = ProjectionScope("tenant-1", "project-1")
    search = ProjectorSpec("search_index", "v1", "non_blocking", NON_BLOCKING_RETRY)
    registry = ProjectorRegistry((*DEFAULT_PROJECTOR_REGISTRY.all(), search))
    canonical_session.add(
        ProjectionPartition(
            id="partition-search",
            tenant_id=scope.tenant_id,
            project_id=scope.project_id,
            projector_id=search.projector_id,
            projector_version=search.version,
            enrollment_status="disabled",
            runtime_status="active",
            last_published_position=0,
            activation_after_position=None,
        )
    )
    canonical_session.commit()
    commits = CanonicalCommitService(
        canonical_session,
        scope.tenant_id,
        scope.project_id,
        projector_registry=registry,
    )
    first = commits.commit(_prepared(canonical_session), "bootstrap-mismatch-w")
    factory = build_session_factory(canonical_session.get_bind())
    adapter = MemoryProjectionAdapter(search, scope, "task-1")

    def corrupt_after_activation(stage):
        if stage == "after_activation_commit":
            adapter.corrupt_actual = True

    service = ProjectionRebuildService(
        factory,
        {search.projector_id: adapter},
        registry=registry,
        failure_hook=corrupt_after_activation,
    )
    run_id = service.start_bootstrap(
        scope, search.projector_id, operator_id="operator-1", reason="mismatch"
    )
    result = service.resume(run_id, worker_id="bootstrap-worker")
    assert result.status == "reconciliation_failed"

    later = commits.commit(
        _prepared(
            canonical_session,
            draft="After failed activation",
            base_revision_number=1,
            base_state_version_id=first.state_version_id,
        ),
        "bootstrap-mismatch-later",
    )
    with factory() as session:
        partition = session.get(ProjectionPartition, "partition-search")
        assert partition.enrollment_status == "active"
        assert partition.runtime_status == "catching_up"
        assert partition.activation_after_position == 1
        assert partition.last_published_position == 0
        assert session.scalar(
            select(func.count()).select_from(OutboxEvent).where(
                OutboxEvent.projection_name == search.projector_id
            )
        ) == 1
        assert session.scalar(
            select(func.count()).select_from(ProjectionDelivery).where(
                ProjectionDelivery.projector_id == search.projector_id,
                ProjectionDelivery.stream_position == 2,
            )
        ) == 1
        assert ProjectionDeliveryStore(session, registry).claim_next(
            "blocked-worker",
            ScanFilter(projector_id=search.projector_id, limit=1),
        ) is None

    adapter.corrupt_actual = False
    resumed = ProjectionRebuildService(
        factory, {search.projector_id: adapter}, registry=registry
    )
    assert resumed.resume(run_id, worker_id="bootstrap-worker").status == "catching_up"
    summary = ProjectionWorker(
        factory,
        {search.projector_id: adapter},
        worker_id="scanner-worker",
        registry=registry,
    ).scan_once(ScanFilter(projector_id=search.projector_id, limit=10))
    assert summary.published == 1
    assert resumed.resume(run_id, worker_id="bootstrap-worker").status == "completed"
    with factory() as session:
        partition = session.get(ProjectionPartition, "partition-search")
        assert partition.last_published_position == 2
        assert session.scalar(
            select(func.count()).select_from(OutboxEvent).where(
                OutboxEvent.projection_name == search.projector_id,
                OutboxEvent.commit_id == later.commit_id,
            )
        ) == 1
