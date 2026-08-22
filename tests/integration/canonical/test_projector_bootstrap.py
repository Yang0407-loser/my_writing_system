from __future__ import annotations

from sqlalchemy import func, select

from app.canonical.commit_service import CanonicalCommitService
from app.canonical.database import build_session_factory
from app.canonical.models import OutboxEvent, ProjectionDelivery, ProjectionPartition
from app.canonical.projection_ports import ProjectionScope
from app.canonical.projection_rebuild import ProjectionRebuildService
from app.canonical.projection_registry import (
    DEFAULT_PROJECTOR_REGISTRY,
    NON_BLOCKING_RETRY,
    ProjectorRegistry,
    ProjectorSpec,
)
from tests.integration.canonical.bootstrap_helpers import MemoryProjectionAdapter
from tests.unit.canonical.test_commit_service import _prepared


pytest_plugins = ("tests.fixtures_canonical",)


def test_bootstrap_rebuilds_history_without_backfilling_envelopes_or_deliveries(
    canonical_session,
):
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
    CanonicalCommitService(
        canonical_session,
        scope.tenant_id,
        scope.project_id,
        projector_registry=registry,
    ).commit(_prepared(canonical_session), "bootstrap-history")
    before = (
        canonical_session.scalar(select(func.count()).select_from(OutboxEvent)),
        canonical_session.scalar(select(func.count()).select_from(ProjectionDelivery)),
    )
    factory = build_session_factory(canonical_session.get_bind())
    adapter = MemoryProjectionAdapter(search, scope, "task-1")
    service = ProjectionRebuildService(
        factory, {search.projector_id: adapter}, registry=registry
    )
    run_id = service.start_bootstrap(
        scope, search.projector_id, operator_id="operator-1", reason="new search"
    )

    result = service.resume(run_id, worker_id="bootstrap-worker")

    assert result.status == "completed"
    assert result.watermark_position == 1
    assert len(adapter.actual_records(scope)) == 1
    with factory() as session:
        after = (
            session.scalar(select(func.count()).select_from(OutboxEvent)),
            session.scalar(select(func.count()).select_from(ProjectionDelivery)),
        )
        partition = session.get(ProjectionPartition, "partition-search")
        assert after == before
        assert partition.enrollment_status == "active"
        assert partition.runtime_status == "active"
        assert partition.activation_after_position == 1
        assert partition.last_published_position == 1
