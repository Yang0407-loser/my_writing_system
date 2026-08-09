from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from sqlalchemy import select

from app.canonical.commit_service import CanonicalCommitService
from app.canonical.models import CanonicalProject, OutboxEvent
from app.canonical.projection_ports import ProjectionMessage
from app.writing.legacy_subsection_projection import (
    LegacyProjectionError,
    LegacySubsectionProjection,
)
from tests.unit.canonical.test_commit_service import _prepared, canonical_session


class RecordingVectorStore:
    def __init__(self):
        self.calls = []

    def add_text(self, text, metadata, *, document_id=None):
        self.calls.append((text, metadata, document_id))
        return document_id

    def enforce_task_limit(self, task_id):
        return 0


def _message(session, commit_id, projection_name):
    event = session.scalar(
        select(OutboxEvent).where(
            OutboxEvent.commit_id == commit_id,
            OutboxEvent.projection_name == projection_name,
        )
    )
    return ProjectionMessage(
        event_id=event.id,
        tenant_id=event.tenant_id,
        project_id=event.project_id,
        commit_id=event.commit_id,
        projection_name=event.projection_name,
        barrier_kind=event.barrier_kind,
        event_type=event.event_type,
        payload=event.payload_json,
    )


def _committed(session):
    return CanonicalCommitService(session, "tenant-1", "project-1").commit(
        _prepared(session), "projection-key"
    )


def test_candidate_without_committed_outbox_cannot_be_projected(canonical_session):
    projection = LegacySubsectionProjection(
        canonical_session, "tenant-1", "project-1"
    )

    with pytest.raises(TypeError, match="ProjectionMessage"):
        projection.project(_prepared(canonical_session).candidate)


def test_each_critical_projector_consumes_only_its_own_outbox(canonical_session):
    result = _committed(canonical_session)
    world_sink = MagicMock()
    handover_sink = MagicMock()
    vector = RecordingVectorStore()
    projection = LegacySubsectionProjection(
        canonical_session,
        "tenant-1",
        "project-1",
        world_event_sink=world_sink,
        handover_sink=handover_sink,
        vector_store=vector,
    )

    projection.project(_message(canonical_session, result.commit_id, "legacy_world_event"))
    assert world_sink.call_count == 1
    assert handover_sink.call_count == 0
    assert vector.calls == []

    projection.project(_message(canonical_session, result.commit_id, "handover_context"))
    assert world_sink.call_count == 1
    assert handover_sink.call_count == 1
    assert vector.calls == []

    projection.project(_message(canonical_session, result.commit_id, "chroma_story_chunks"))
    assert vector.calls
    assert world_sink.call_count == handover_sink.call_count == 1

    for sink in (world_sink, handover_sink):
        envelope = sink.call_args.args[0]
        assert envelope.commit_id == result.commit_id
        assert envelope.revision_id == result.revision_id
        assert envelope.content_hash == result.content_hash
    for _, metadata, _ in vector.calls:
        assert metadata["commit_id"] == result.commit_id
        assert metadata["revision_id"] == result.revision_id
        assert metadata["content_hash"] == result.content_hash


def test_chroma_projection_uses_stable_chunk_ids_on_replay(canonical_session):
    result = _committed(canonical_session)
    vector = RecordingVectorStore()
    projection = LegacySubsectionProjection(
        canonical_session,
        "tenant-1",
        "project-1",
        vector_store=vector,
    )
    message = _message(canonical_session, result.commit_id, "chroma_story_chunks")

    projection.project(message)
    first_ids = [call[2] for call in vector.calls]
    vector.calls.clear()
    projection.project(message)
    second_ids = [call[2] for call in vector.calls]

    assert first_ids == second_ids
    assert len(first_ids) == len(set(first_ids))
    assert all(value.startswith("canonical-chunk-") for value in first_ids)


def test_sink_failure_is_classified_without_changing_canon(canonical_session):
    result = _committed(canonical_session)
    project = canonical_session.get(CanonicalProject, "project-1")
    head_before = project.current_state_version_id
    projection = LegacySubsectionProjection(
        canonical_session,
        "tenant-1",
        "project-1",
        world_event_sink=MagicMock(side_effect=RuntimeError("legacy down")),
    )

    with pytest.raises(LegacyProjectionError, match="legacy_world_event"):
        projection.project(
            _message(canonical_session, result.commit_id, "legacy_world_event")
        )

    assert canonical_session.get(
        CanonicalProject, "project-1"
    ).current_state_version_id == head_before


def test_nonblocking_projection_failure_is_local(canonical_session):
    result = _committed(canonical_session)
    analytics = MagicMock(side_effect=RuntimeError("analytics down"))
    projection = LegacySubsectionProjection(
        canonical_session,
        "tenant-1",
        "project-1",
        non_blocking_sinks={"analytics": analytics},
    )

    with pytest.raises(LegacyProjectionError, match="analytics"):
        projection.project(_message(canonical_session, result.commit_id, "analytics"))

    assert analytics.call_count == 1
