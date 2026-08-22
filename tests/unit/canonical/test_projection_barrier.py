from __future__ import annotations

# ruff: noqa: F401, F811 -- pytest registers the imported sibling fixture by name.

from unittest.mock import MagicMock

import pytest
from sqlalchemy import delete, select

from app.canonical.commit_service import CanonicalCommitService, PROJECTION_MANIFEST
from app.canonical.hashing import sha256_json
from app.canonical.models import (
    OutboxEvent,
    CanonicalCommit,
    ProjectionAttempt,
    ProjectionDelivery,
    ProjectionPartition,
)
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


def test_retryable_critical_projection_is_pending(canonical_session):
    result = CanonicalCommitService(
        canonical_session, "tenant-1", "project-1"
    ).commit(_prepared(canonical_session), "failed-barrier")
    projectors = _projectors(
        handover_context=MagicMock(side_effect=RuntimeError("failed"))
    )
    dispatcher, barrier = _components(canonical_session, projectors)

    dispatcher.dispatch_critical(result.commit_id)

    assert barrier.ensure_ready(result.commit_id) == "pending"


def test_dead_lettered_critical_delivery_fails_without_rolling_back_canon(
    canonical_session,
):
    result = CanonicalCommitService(
        canonical_session, "tenant-1", "project-1"
    ).commit(_prepared(canonical_session), "dead-letter-barrier")
    delivery = canonical_session.scalar(
        select(ProjectionDelivery).where(
            ProjectionDelivery.project_id == "project-1",
            ProjectionDelivery.projector_id == "handover_context",
            ProjectionDelivery.stream_position == 1,
        )
    )
    delivery.status = "dead_letter"
    delivery.last_error_message = "projection exhausted retries"
    canonical_session.commit()
    canonical_session.refresh(delivery)
    assert delivery.status == "dead_letter"
    assert canonical_session.get(CanonicalCommit, result.commit_id).status == "committed"

    assert ProjectionBarrier(
        canonical_session, "tenant-1", "project-1"
    ).ensure_ready(result.commit_id) == "failed"


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


def test_barrier_requires_delivery_cursor_coverage_not_legacy_outbox_status(
    canonical_session,
):
    """A published envelope cannot cover a Delivery whose Cursor is behind."""
    result = CanonicalCommitService(
        canonical_session, "tenant-1", "project-1"
    ).commit(_prepared(canonical_session), "barrier-cursor-truth")
    dispatcher, barrier = _components(canonical_session)
    dispatcher.dispatch_critical(result.commit_id)
    critical_delivery = canonical_session.scalar(
        select(ProjectionDelivery)
        .where(
            ProjectionDelivery.project_id == "project-1",
            ProjectionDelivery.barrier_kind == "critical",
        )
        .order_by(ProjectionDelivery.projector_id)
    )
    partition = canonical_session.scalar(
        select(ProjectionPartition).where(
            ProjectionPartition.tenant_id == "tenant-1",
            ProjectionPartition.project_id == "project-1",
            ProjectionPartition.projector_id == critical_delivery.projector_id,
        )
    )
    partition.last_published_position = 0
    canonical_session.commit()

    assert barrier.ensure_ready(result.commit_id) == "pending"


def test_barrier_ignores_deprecated_outbox_status_without_delivery_cursor_coverage(
    canonical_session,
):
    result = CanonicalCommitService(
        canonical_session, "tenant-1", "project-1"
    ).commit(_prepared(canonical_session), "barrier-outbox-mirror")
    envelopes = canonical_session.scalars(
        select(OutboxEvent).where(
            OutboxEvent.commit_id == result.commit_id,
            OutboxEvent.barrier_kind == "critical",
        )
    ).all()
    for envelope in envelopes:
        envelope.status = "published"
    canonical_session.commit()

    assert ProjectionBarrier(
        canonical_session, "tenant-1", "project-1"
    ).ensure_ready(result.commit_id) == "pending"


@pytest.mark.parametrize("runtime_status", ["maintenance", "catching_up"])
def test_barrier_is_pending_while_critical_partition_is_not_active(
    canonical_session, runtime_status
):
    """Historical Delivery receipts must not bypass an inactive runtime partition."""
    result = CanonicalCommitService(
        canonical_session, "tenant-1", "project-1"
    ).commit(_prepared(canonical_session), "barrier-maintenance")
    dispatcher, barrier = _components(canonical_session)
    dispatcher.dispatch_critical(result.commit_id)
    partition = canonical_session.scalar(
        select(ProjectionPartition).where(
            ProjectionPartition.tenant_id == "tenant-1",
            ProjectionPartition.project_id == "project-1",
            ProjectionPartition.projector_id == "handover_context",
        )
    )
    partition.runtime_status = runtime_status
    canonical_session.commit()

    assert barrier.ensure_ready(result.commit_id) == "pending"


@pytest.mark.parametrize(
    ("partition_update", "idempotency_key"),
    [
        ({"enrollment_status": "disabled"}, "disabled-critical-barrier"),
        ({"activation_after_position": 1}, "not-yet-active-critical-barrier"),
    ],
)
def test_unenrolled_or_not_yet_activated_critical_partition_is_not_expected(
    canonical_session, partition_update, idempotency_key
):
    partition = canonical_session.scalar(
        select(ProjectionPartition).where(
            ProjectionPartition.projector_id == "handover_context",
        )
    )
    for field, value in partition_update.items():
        setattr(partition, field, value)
    canonical_session.commit()

    result = CanonicalCommitService(
        canonical_session, "tenant-1", "project-1"
    ).commit(_prepared(canonical_session), idempotency_key)
    dispatcher, barrier = _components(canonical_session)

    dispatcher.dispatch_critical(result.commit_id)

    assert canonical_session.scalar(
        select(ProjectionDelivery).where(
            ProjectionDelivery.projector_id == "handover_context",
            ProjectionDelivery.stream_position == 1,
        )
    ) is None
    assert barrier.ensure_ready(result.commit_id) == "ready"


def test_exact_version_mismatch_keeps_expected_critical_partition_pending(
    canonical_session,
):
    result = CanonicalCommitService(
        canonical_session, "tenant-1", "project-1"
    ).commit(_prepared(canonical_session), "version-mismatch-barrier")
    dispatcher, barrier = _components(canonical_session)
    dispatcher.dispatch_critical(result.commit_id)
    partition = canonical_session.scalar(
        select(ProjectionPartition).where(
            ProjectionPartition.projector_id == "handover_context",
        )
    )
    partition.projector_version = "v999"
    canonical_session.commit()

    assert barrier.ensure_ready(result.commit_id) == "pending"


def test_extra_critical_delivery_fails_closed(canonical_session):
    result = CanonicalCommitService(
        canonical_session, "tenant-1", "project-1"
    ).commit(_prepared(canonical_session), "extra-critical-barrier")
    dispatcher, barrier = _components(canonical_session)
    dispatcher.dispatch_critical(result.commit_id)
    extra = canonical_session.scalar(
        select(ProjectionDelivery).where(
            ProjectionDelivery.projector_id == "analytics",
            ProjectionDelivery.stream_position == 1,
        )
    )
    extra.barrier_kind = "critical"
    canonical_session.commit()

    assert barrier.ensure_ready(result.commit_id) == "failed"
