from __future__ import annotations

from unittest.mock import MagicMock

from app.canonical.commit_service import CanonicalCommitService, PROJECTION_MANIFEST
from app.canonical.outbox import OutboxDispatcher
from app.canonical.projection_barrier import ProjectionBarrier
from tests.unit.canonical.test_commit_service import _prepared, canonical_session


def _components(session, projectors=None):
    projectors = projectors or {name: MagicMock() for name, _ in PROJECTION_MANIFEST}
    dispatcher = OutboxDispatcher(
        lambda: session, "tenant-1", "project-1", projectors
    )
    barrier = ProjectionBarrier(session, "tenant-1", "project-1")
    return dispatcher, barrier


def test_barrier_is_pending_until_all_critical_events_publish(canonical_session):
    result = CanonicalCommitService(
        canonical_session, "tenant-1", "project-1"
    ).commit(_prepared(canonical_session), "barrier-key")
    dispatcher, barrier = _components(canonical_session)

    assert barrier.ensure_ready(result.commit_id) == "pending"
    dispatcher.dispatch_critical(result.commit_id)
    assert barrier.ensure_ready(result.commit_id) == "ready"


def test_failed_critical_projection_is_not_ready(canonical_session):
    result = CanonicalCommitService(
        canonical_session, "tenant-1", "project-1"
    ).commit(_prepared(canonical_session), "failed-barrier")
    projectors = {name: MagicMock() for name, _ in PROJECTION_MANIFEST}
    projectors["handover_context"] = MagicMock(side_effect=RuntimeError("failed"))
    dispatcher, barrier = _components(canonical_session, projectors)

    dispatcher.dispatch_critical(result.commit_id)

    assert barrier.ensure_ready(result.commit_id) == "failed"


def test_non_blocking_failures_do_not_change_ready_barrier(canonical_session):
    result = CanonicalCommitService(
        canonical_session, "tenant-1", "project-1"
    ).commit(_prepared(canonical_session), "nonblocking-barrier")
    projectors = {
        name: (
            MagicMock(side_effect=RuntimeError("nonblocking failed"))
            if barrier_kind == "non_blocking"
            else MagicMock()
        )
        for name, barrier_kind in PROJECTION_MANIFEST
    }
    dispatcher, barrier = _components(canonical_session, projectors)

    dispatcher.dispatch_critical(result.commit_id)
    assert dispatcher.dispatch_non_blocking(result.commit_id) == {
        "published": 0,
        "failed": 4,
    }
    assert barrier.ensure_ready(result.commit_id) == "ready"
