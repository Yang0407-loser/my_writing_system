from __future__ import annotations

import inspect

from sqlalchemy import func, select

from app.canonical.commit_service import CanonicalCommitService
from app.canonical.database import build_session_factory
from app.canonical.models import CanonicalCommit, ProjectionDelivery
from app.canonical.projection_registry import (
    DEFAULT_PROJECTOR_REGISTRY,
    ProjectorRegistry,
)
from app.canonical.projection_worker import ProjectionWorker
from app.projections.analytics import AnalyticsProjectionAdapter
from app.projection_tasks import (
    _production_worker,
    try_wake_projection_scanner,
    wake_projection_scanner,
    wakeup_failure_count,
)
from app.canonical.projection_ports import ProjectionScope
from app.writing.canonical_subsection_runtime import CanonicalSubsectionRuntime
from tests.unit.canonical.test_commit_service import _prepared
from tests.unit.test_canonical_subsection_runtime import _command, _runtime


pytest_plugins = ("tests.unit.canonical.test_commit_service",)


def test_broker_publish_failure_does_not_change_canonical_result(
    canonical_session, monkeypatch
):
    before_failures = wakeup_failure_count()

    def broker_down(**_kwargs):
        raise ConnectionError("redis unavailable")

    monkeypatch.setattr(wake_projection_scanner, "delay", broker_down)
    runtime = _runtime(canonical_session)
    runtime.projection_wakeup_sender = try_wake_projection_scanner

    result = runtime.execute(_command())

    assert result.commit.commit_id
    assert canonical_session.scalar(
        select(func.count()).select_from(CanonicalCommit)
    ) == 1
    assert canonical_session.scalar(
        select(func.count()).select_from(ProjectionDelivery)
    ) == 7
    assert wakeup_failure_count() == before_failures + 1


def test_runtime_sends_only_optional_scope_hints_after_commit(canonical_session):
    calls = []

    def sender(**kwargs):
        assert canonical_session.scalar(
            select(func.count()).select_from(CanonicalCommit)
        ) == 1
        calls.append(kwargs)
        return True

    runtime = _runtime(canonical_session)
    runtime.projection_wakeup_sender = sender
    runtime.execute(_command())

    assert calls == [{"tenant_id": "tenant-1", "project_id": "project-1"}]
    parameters = inspect.signature(wake_projection_scanner.run).parameters
    assert "delivery_id" not in parameters
    assert "outbox_event_id" not in parameters
    assert "commit_id" not in parameters


def test_duplicate_wakeup_task_invocations_converge_once(
    canonical_session, monkeypatch
):
    registry = ProjectorRegistry((DEFAULT_PROJECTOR_REGISTRY.get("analytics"),))
    CanonicalCommitService(
        canonical_session,
        "tenant-1",
        "project-1",
        projector_registry=registry,
    ).commit(_prepared(canonical_session), "duplicate-wakeup")
    factory = build_session_factory(canonical_session.get_bind())
    adapter = AnalyticsProjectionAdapter(
        factory, ProjectionScope("tenant-1", "project-1"), "task-1"
    )

    def build_worker(_worker_id):
        return ProjectionWorker(
            factory,
            {"analytics": adapter},
            worker_id="duplicate-wakeup-worker",
            registry=registry,
        )

    monkeypatch.setattr(
        "app.projection_tasks.build_production_projection_worker", build_worker
    )
    _production_worker.cache_clear()
    for _ in range(50):
        wake_projection_scanner.run(
            tenant_id="tenant-1",
            project_id="project-1",
            projector_id="analytics",
        )

    assert canonical_session.scalar(
        select(func.count()).select_from(CanonicalCommit)
    ) == 1
    assert canonical_session.scalar(
        select(func.count()).select_from(ProjectionDelivery).where(
            ProjectionDelivery.status == "published"
        )
    ) == 1
    assert isinstance(_runtime(canonical_session), CanonicalSubsectionRuntime)
    _production_worker.cache_clear()
