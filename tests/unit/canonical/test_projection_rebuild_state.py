from __future__ import annotations

import pytest

from app.canonical.database import build_session_factory
from app.canonical.projection_ports import ProjectionScope
from app.canonical.projection_rebuild import ProjectionRebuildService
from app.canonical.projection_rebuild import _TRANSITIONS
from tests.unit.canonical.test_commit_service import _prepared
from app.canonical.commit_service import CanonicalCommitService
from app.projections.analytics import AnalyticsProjectionAdapter


pytest_plugins = ("tests.fixtures_canonical",)


def test_exact_maintenance_state_machine():
    assert list(_TRANSITIONS.items()) == [
        ("requested", "pausing"),
        ("pausing", "clearing"),
        ("clearing", "rebuilding"),
        ("rebuilding", "reconciling"),
        ("reconciling", "catching_up"),
        ("catching_up", "completed"),
    ]


def test_maintenance_rebuild_transitions_and_resumes(canonical_session):
    scope = ProjectionScope("tenant-1", "project-1")
    CanonicalCommitService(canonical_session, scope.tenant_id, scope.project_id).commit(
        _prepared(canonical_session), "rebuild-state"
    )
    factory = build_session_factory(canonical_session.get_bind())
    adapter = AnalyticsProjectionAdapter(factory, scope, "task-1")
    service = ProjectionRebuildService(
        factory,
        {"analytics": adapter},
    )
    run_id = service.start_maintenance(
        scope, "analytics", operator_id="operator-1", reason="test rebuild"
    )
    assert service.status(run_id).status == "requested"
    assert service.resume(run_id, worker_id="worker-1").status == "completed"
    assert service.status(run_id).checkpoint_position == 1


def test_invalid_resume_worker_is_rejected(canonical_session):
    scope = ProjectionScope("tenant-1", "project-1")
    service = ProjectionRebuildService(lambda: canonical_session, {})
    with pytest.raises(ValueError, match="adapter is not registered"):
        service.start_maintenance(
            scope, "analytics", operator_id="operator-1", reason="missing adapter"
        )
