from __future__ import annotations

# ruff: noqa: F401, F811 -- pytest registers the imported sibling fixture by name.

from unittest.mock import MagicMock

from sqlalchemy import select

from app.canonical.commit_service import CanonicalCommitService, PROJECTION_MANIFEST
from app.canonical.models import (
    CanonicalProject,
    DocumentRevision,
    OutboxEvent,
    ProjectionDelivery,
)
from app.canonical.outbox import OutboxDispatcher
from app.canonical.projection_ports import ProjectionScope
from app.canonical.projection_replay import CanonicalProjectionReplay
from tests.unit.canonical.test_commit_service import _prepared, canonical_session


def _commit(session, key="outbox-key"):
    return CanonicalCommitService(
        session, "tenant-1", "project-1"
    ).commit(_prepared(session), key)


def _projectors(**overrides):
    projectors = {name: MagicMock() for name, _ in PROJECTION_MANIFEST}
    projectors.update(overrides)
    return projectors


def test_commit_manifest_is_pending_and_dispatches_critical_only(canonical_session):
    result = _commit(canonical_session)
    projectors = _projectors()
    dispatcher = OutboxDispatcher(
        lambda: canonical_session,
        tenant_id="tenant-1",
        project_id="project-1",
        projectors=projectors,
    )

    summary = dispatcher.dispatch_critical(result.commit_id)

    assert summary == {"published": 3, "failed": 0}
    events = canonical_session.scalars(
        select(OutboxEvent).where(OutboxEvent.commit_id == result.commit_id)
    ).all()
    deliveries = canonical_session.scalars(
        select(ProjectionDelivery)
        .join(OutboxEvent, OutboxEvent.id == ProjectionDelivery.outbox_event_id)
        .where(OutboxEvent.commit_id == result.commit_id)
    ).all()
    assert len(events) == len(PROJECTION_MANIFEST)
    assert all(
        event.status == "pending"
        and event.attempts == 0
        and event.published_at is None
        and event.last_error is None
        for event in events
    )
    assert all(
        delivery.attempt_count == (1 if delivery.barrier_kind == "critical" else 0)
        for delivery in deliveries
    )
    assert all(
        delivery.status
        == ("published" if delivery.barrier_kind == "critical" else "pending")
        for delivery in deliveries
    )
    for name, barrier_kind in PROJECTION_MANIFEST:
        assert projectors[name].call_count == (1 if barrier_kind == "critical" else 0)
    message = projectors["legacy_world_event"].call_args.args[0]
    assert message.outbox_event_id is not None
    assert message.delivery_id is not None
    assert message.projector_id == "legacy_world_event"
    assert message.stream_position == 1
    assert message.revision_id == result.revision_id
    assert message.state_version_id == result.state_version_id


def test_dispatcher_message_matches_pure_canon_replay_except_delivery_ids(
    canonical_session,
):
    _commit(canonical_session)
    projectors = _projectors()
    rebuilt = tuple(
        CanonicalProjectionReplay(canonical_session).iter_messages(
            ProjectionScope("tenant-1", "project-1"),
            "chroma_story_chunks",
            0,
            1,
        )
    )[0]

    OutboxDispatcher(
        lambda: canonical_session,
        "tenant-1",
        "project-1",
        projectors,
    ).dispatch_critical(rebuilt.commit_id)

    incremental = projectors["chroma_story_chunks"].call_args.args[0]
    assert incremental.model_dump(
        exclude={"outbox_event_id", "delivery_id"}
    ) == rebuilt.model_dump(exclude={"outbox_event_id", "delivery_id"})
    assert incremental.outbox_event_id is not None
    assert incremental.delivery_id is not None


def test_projection_failure_preserves_canon_and_retries(canonical_session):
    result = _commit(canonical_session)
    failing = MagicMock(side_effect=RuntimeError("projection unavailable"))
    projectors = _projectors(chroma_story_chunks=failing)
    dispatcher = OutboxDispatcher(
        lambda: canonical_session, "tenant-1", "project-1", projectors
    )
    project = canonical_session.get(CanonicalProject, "project-1")
    revision = canonical_session.get(DocumentRevision, result.revision_id)
    canon_before = (
        project.current_state_version_id,
        revision.content_hash,
    )

    first = dispatcher.dispatch_critical(result.commit_id)

    event = canonical_session.scalar(
        select(OutboxEvent).where(
            OutboxEvent.commit_id == result.commit_id,
            OutboxEvent.projection_name == "chroma_story_chunks",
        )
    )
    delivery = canonical_session.scalar(
        select(ProjectionDelivery).where(
            ProjectionDelivery.outbox_event_id == event.id
        )
    )
    assert first == {"published": 2, "failed": 1}
    assert event.status == "pending"
    assert event.attempts == 0
    assert event.last_error is None
    assert delivery.status == "pending"
    assert delivery.attempt_count == 1
    assert "projection unavailable" in delivery.last_error_message
    assert (
        canonical_session.get(CanonicalProject, "project-1").current_state_version_id,
        canonical_session.get(DocumentRevision, result.revision_id).content_hash,
    ) == canon_before

    projectors["chroma_story_chunks"] = MagicMock()
    dispatcher = OutboxDispatcher(
        lambda: canonical_session, "tenant-1", "project-1", projectors
    )
    second = dispatcher.dispatch_critical(result.commit_id)
    canonical_session.refresh(event)
    canonical_session.refresh(delivery)
    assert second == {"published": 1, "failed": 0}
    assert event.status == "pending"
    assert event.attempts == 0
    assert event.last_error is None
    assert delivery.status == "published"
    assert delivery.attempt_count == 2
    assert delivery.last_error_message is None


def test_restart_scans_durable_pending_and_failed_rows(canonical_session):
    result = _commit(canonical_session)
    projectors = _projectors()
    projectors["legacy_world_event"] = MagicMock(side_effect=RuntimeError("first"))
    OutboxDispatcher(
        lambda: canonical_session, "tenant-1", "project-1", projectors
    ).dispatch_critical(result.commit_id)

    restarted_projectors = _projectors()
    summary = OutboxDispatcher(
        lambda: canonical_session,
        tenant_id="tenant-1",
        project_id="project-1",
        projectors=restarted_projectors,
    ).dispatch_pending(limit=100)

    assert summary == {"published": 5, "failed": 0}
    assert all(
        event.status == "pending"
        for event in canonical_session.scalars(
            select(OutboxEvent).where(OutboxEvent.commit_id == result.commit_id)
        ).all()
    )
    assert all(
        delivery.status == "published"
        for delivery in canonical_session.scalars(
            select(ProjectionDelivery)
            .join(OutboxEvent, OutboxEvent.id == ProjectionDelivery.outbox_event_id)
            .where(OutboxEvent.commit_id == result.commit_id)
        ).all()
    )


def test_duplicate_commit_does_not_create_more_outbox(canonical_session):
    prepared = _prepared(canonical_session)
    service = CanonicalCommitService(canonical_session, "tenant-1", "project-1")
    first = service.commit(prepared, "duplicate-outbox")
    before = len(
        canonical_session.scalars(
            select(OutboxEvent).where(OutboxEvent.commit_id == first.commit_id)
        ).all()
    )

    duplicate = service.commit(prepared, "duplicate-outbox")
    after = len(
        canonical_session.scalars(
            select(OutboxEvent).where(OutboxEvent.commit_id == first.commit_id)
        ).all()
    )

    assert duplicate.skipped_as_duplicate is True
    assert before == after == len(PROJECTION_MANIFEST)
