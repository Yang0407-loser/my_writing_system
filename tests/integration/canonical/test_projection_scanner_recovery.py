from __future__ import annotations

from sqlalchemy import func, select

from app.canonical.commit_service import CanonicalCommitService
from app.canonical.database import build_session_factory
from app.canonical.models import ProjectionDelivery, ProjectionPartition
from app.canonical.projection_delivery import ScanFilter
from app.canonical.projection_health import projection_health_snapshot
from app.canonical.projection_ports import ProjectionScope
from app.canonical.projection_registry import (
    DEFAULT_PROJECTOR_REGISTRY,
    ProjectorRegistry,
)
from app.canonical.projection_worker import ProjectionWorker
from app.projections.analytics import AnalyticsProjectionAdapter
from tests.unit.canonical.test_commit_service import _prepared


pytest_plugins = ("tests.fixtures_canonical",)


def test_independent_scanner_recovers_without_celery_messages(canonical_session):
    analytics = ProjectorRegistry((DEFAULT_PROJECTOR_REGISTRY.get("analytics"),))
    commits = CanonicalCommitService(
        canonical_session,
        "tenant-1",
        "project-1",
        projector_registry=analytics,
    )
    first = commits.commit(_prepared(canonical_session), "scanner-outage-1")
    commits.commit(
        _prepared(
            canonical_session,
            draft="Second accepted draft",
            base_revision_number=1,
            base_state_version_id=first.state_version_id,
        ),
        "scanner-outage-2",
    )
    factory = build_session_factory(canonical_session.get_bind())
    adapter = AnalyticsProjectionAdapter(
        factory,
        ProjectionScope("tenant-1", "project-1"),
        "task-1",
    )
    worker = ProjectionWorker(
        factory,
        {"analytics": adapter},
        worker_id="independent-scanner",
        registry=analytics,
    )

    summary = worker.scan_once(
        ScanFilter(projector_id="analytics", limit=10)
    )

    assert summary.published == 2
    with factory() as session:
        assert session.scalar(
            select(func.count()).select_from(ProjectionDelivery).where(
                ProjectionDelivery.projector_id == "analytics",
                ProjectionDelivery.status == "published",
            )
        ) == 2
        partition = session.scalar(
            select(ProjectionPartition).where(
                ProjectionPartition.projector_id == "analytics"
            )
        )
        assert partition.last_published_position == 2
        assert projection_health_snapshot(
            session, ScanFilter(projector_id="analytics")
        ).lag_events == 0
