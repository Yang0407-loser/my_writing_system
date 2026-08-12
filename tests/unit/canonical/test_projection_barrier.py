from __future__ import annotations

# ruff: noqa: F401, F811 -- pytest registers the imported sibling fixture by name.

from unittest.mock import MagicMock

from sqlalchemy import delete, select

from app.canonical.commit_service import CanonicalCommitService, PROJECTION_MANIFEST
from app.canonical.hashing import sha256_json
from app.canonical.models import OutboxEvent, ProjectionAttempt, ProjectionDelivery
from app.canonical.outbox import OutboxDispatcher
from app.canonical.projection_barrier import ProjectionBarrier
from app.canonical.projection_ports import ProjectionReceipt
from tests.unit.canonical.test_commit_service import _prepared, canonical_session


def _components(session, projectors=None):
    projectors = projectors or _projectors()
    dispatcher = OutboxDispatcher(
        lambda: session, "tenant-1", "project-1", projectors
    )
    barrier = ProjectionBarrier(session, "tenant-1", "project-1")
    return dispatcher, barrier


def _projectors(**overrides):
    def projector(name):
        def receipt(message):
            return ProjectionReceipt(
                projection_event_id=message.projection_event_id,
                projector_id=message.projector_id,
                projector_version=message.projector_version,
                stream_position=message.stream_position,
                record_count=1,
                content_digest=sha256_json({"event": message.projection_event_id}),
            )

        return MagicMock(side_effect=receipt)

    projectors = {name: projector(name) for name, _ in PROJECTION_MANIFEST}
    projectors.update(overrides)
    return projectors


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
    projectors = _projectors(
        handover_context=MagicMock(side_effect=RuntimeError("failed"))
    )
    dispatcher, barrier = _components(canonical_session, projectors)

    dispatcher.dispatch_critical(result.commit_id)

    assert barrier.ensure_ready(result.commit_id) == "failed"


def test_missing_critical_delivery_fails_closed(canonical_session):
    result = CanonicalCommitService(
        canonical_session, "tenant-1", "project-1"
    ).commit(_prepared(canonical_session), "missing-delivery-barrier")
    dispatcher, barrier = _components(canonical_session)
    dispatcher.dispatch_critical(result.commit_id)
    missing = canonical_session.scalar(
        select(ProjectionDelivery)
        .join(OutboxEvent, OutboxEvent.id == ProjectionDelivery.outbox_event_id)
        .where(
            OutboxEvent.commit_id == result.commit_id,
            ProjectionDelivery.barrier_kind == "critical",
        )
    )
    canonical_session.execute(
        delete(ProjectionAttempt).where(
            ProjectionAttempt.delivery_id == missing.id
        )
    )
    canonical_session.delete(missing)
    canonical_session.commit()

    assert barrier.ensure_ready(result.commit_id) == "failed"


def test_non_blocking_failures_do_not_change_ready_barrier(canonical_session):
    result = CanonicalCommitService(
        canonical_session, "tenant-1", "project-1"
    ).commit(_prepared(canonical_session), "nonblocking-barrier")
    projectors = _projectors(
        **{
            name: MagicMock(side_effect=RuntimeError("nonblocking failed"))
            for name, barrier_kind in PROJECTION_MANIFEST
            if barrier_kind == "non_blocking"
        }
    )
    dispatcher, barrier = _components(canonical_session, projectors)

    dispatcher.dispatch_critical(result.commit_id)
    assert dispatcher.dispatch_non_blocking(result.commit_id) == {
        "published": 0,
        "failed": 4,
    }
    assert barrier.ensure_ready(result.commit_id) == "ready"
