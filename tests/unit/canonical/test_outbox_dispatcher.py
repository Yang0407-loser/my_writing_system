from __future__ import annotations

from unittest.mock import MagicMock

from sqlalchemy import select

from app.canonical.commit_service import CanonicalCommitService, PROJECTION_MANIFEST
from app.canonical.models import CanonicalProject, DocumentRevision, OutboxEvent
from app.canonical.outbox import OutboxDispatcher
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
    assert len(events) == len(PROJECTION_MANIFEST)
    assert all(event.attempts == (1 if event.barrier_kind == "critical" else 0) for event in events)
    assert all(
        event.status == ("published" if event.barrier_kind == "critical" else "pending")
        for event in events
    )
    for name, barrier_kind in PROJECTION_MANIFEST:
        assert projectors[name].call_count == (1 if barrier_kind == "critical" else 0)


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
    assert first == {"published": 2, "failed": 1}
    assert event.status == "failed"
    assert event.attempts == 1
    assert "projection unavailable" in event.last_error
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
    assert second == {"published": 1, "failed": 0}
    assert event.status == "published"
    assert event.attempts == 2
    assert event.last_error is None


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
        event.status == "published"
        for event in canonical_session.scalars(
            select(OutboxEvent).where(OutboxEvent.commit_id == result.commit_id)
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
