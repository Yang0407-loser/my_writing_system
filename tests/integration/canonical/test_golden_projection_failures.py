from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from sqlalchemy import func, select

from app.canonical.commit_service import CanonicalCommitService, PROJECTION_MANIFEST
from app.canonical.models import CanonicalCommit, DocumentRevision, EventLedger, OutboxEvent
from app.canonical.outbox import OutboxDispatcher
from app.canonical.projection_barrier import ProjectionBarrier
from app.canonical.projection_ports import ProjectionMessage
from app.writing.canonical_subsection_runtime import (
    CanonicalSubsectionCommand,
    CanonicalSubsectionRuntime,
)
from app.writing.legacy_subsection_projection import LegacySubsectionProjection
from tests.unit.canonical.test_commit_service import _prepared

pytest_plugins = ("tests.unit.canonical.test_commit_service",)


def _projectors(**overrides):
    values = {name: MagicMock() for name, _ in PROJECTION_MANIFEST}
    values.update(overrides)
    return values


def _command():
    return CanonicalSubsectionCommand(
        task_id="task-1",
        document_id="document-1",
        subsection_id="subsection-1",
        generation_attempt_id="golden-failure-attempt",
        expected_revision_id="GENESIS",
        expected_state_version_id="state-genesis",
    )


def _generator(session, spy=None):
    def generate(*, snapshot, base_revision_number, command):
        if spy is not None:
            spy()
        return _prepared(
            session,
            subsection_id=command.subsection_id,
            base_revision_number=base_revision_number,
            base_state_version_id=snapshot.version_id,
        ).candidate

    return generate


def _runtime(session, projectors, generator):
    return CanonicalSubsectionRuntime(
        session=session,
        tenant_id="tenant-1",
        project_id="project-1",
        candidate_generator=generator,
        projectors=projectors,
        checkpoint_writer=lambda _payload: None,
    )


@pytest.mark.parametrize(
    "stage",
    ["after_revision", "after_state", "after_ledger", "after_outbox", "before_commit"],
)
def test_sql_crash_points_leave_zero_partial_canon(canonical_session, stage):
    def fail(name):
        if name == stage:
            raise RuntimeError(stage)

    with pytest.raises(RuntimeError, match=stage):
        CanonicalCommitService(
            canonical_session,
            "tenant-1",
            "project-1",
            failure_hook=fail,
        ).commit(_prepared(canonical_session), f"failure-{stage}")

    assert canonical_session.scalar(select(func.count()).select_from(CanonicalCommit)) == 0
    assert canonical_session.scalar(select(func.count()).select_from(DocumentRevision)) == 0
    assert canonical_session.scalar(select(func.count()).select_from(EventLedger)) == 0
    assert canonical_session.scalar(select(func.count()).select_from(OutboxEvent)) == 0


def test_critical_outage_keeps_canon_and_retry_preflight_skips_llm(canonical_session):
    failing = _projectors(
        chroma_story_chunks=MagicMock(side_effect=RuntimeError("chroma offline"))
    )
    llm = MagicMock()
    first = _runtime(canonical_session, failing, _generator(canonical_session, llm)).execute(
        _command()
    )

    assert first.phase == "awaiting_critical_projection"
    assert canonical_session.scalar(select(func.count()).select_from(CanonicalCommit)) == 1
    assert canonical_session.scalar(select(func.count()).select_from(DocumentRevision)) == 1

    retry_llm = MagicMock(side_effect=AssertionError("retry LLM is forbidden"))
    retried = _runtime(
        canonical_session, _projectors(), retry_llm
    ).execute(_command())
    assert retried.phase == "ready"
    assert retried.commit.commit_id == first.commit.commit_id
    retry_llm.assert_not_called()


def test_nonblocking_outage_does_not_close_critical_barrier(canonical_session):
    projectors = _projectors(
        redis_stream=MagicMock(side_effect=RuntimeError("redis offline")),
        markdown_export=MagicMock(side_effect=RuntimeError("disk offline")),
    )
    result = _runtime(
        canonical_session, projectors, _generator(canonical_session)
    ).execute(_command())

    assert result.phase == "ready"
    assert result.critical_projection_status == "ready"
    assert result.non_blocking_summary == {"published": 2, "failed": 2}


def test_dispatcher_restart_continues_failed_rows_without_republishing_successes(
    canonical_session,
):
    result = CanonicalCommitService(
        canonical_session, "tenant-1", "project-1"
    ).commit(_prepared(canonical_session), "dispatcher-crash")
    first = _projectors(
        handover_context=MagicMock(side_effect=RuntimeError("process terminated"))
    )
    OutboxDispatcher(
        lambda: canonical_session, "tenant-1", "project-1", first
    ).dispatch_critical(result.commit_id)

    restarted = _projectors()
    summary = OutboxDispatcher(
        lambda: canonical_session, "tenant-1", "project-1", restarted
    ).dispatch_pending(100)

    assert summary == {"published": 5, "failed": 0}
    assert restarted["legacy_world_event"].call_count == 0
    assert restarted["chroma_story_chunks"].call_count == 0
    assert ProjectionBarrier(
        canonical_session, "tenant-1", "project-1"
    ).ensure_ready(result.commit_id) == "ready"


def test_same_message_100_times_is_one_commit_revision_and_manifest(canonical_session):
    prepared = _prepared(canonical_session)
    service = CanonicalCommitService(canonical_session, "tenant-1", "project-1")
    results = [service.commit(prepared, "one-message") for _ in range(100)]

    assert len({result.commit_id for result in results}) == 1
    assert canonical_session.scalar(select(func.count()).select_from(CanonicalCommit)) == 1
    assert canonical_session.scalar(select(func.count()).select_from(DocumentRevision)) == 1
    assert canonical_session.scalar(select(func.count()).select_from(OutboxEvent)) == 7


class _ReplaceVector:
    def __init__(self):
        self.rows = {}

    def add_text(self, text, metadata, *, document_id=None):
        self.rows[document_id] = (text, metadata)
        return document_id

    def enforce_task_limit(self, _task_id):
        return 0


def test_deleted_derived_chunks_rebuild_identically_from_canon(canonical_session):
    result = CanonicalCommitService(
        canonical_session, "tenant-1", "project-1"
    ).commit(_prepared(canonical_session), "rebuild-projection")
    row = canonical_session.scalar(
        select(OutboxEvent).where(
            OutboxEvent.commit_id == result.commit_id,
            OutboxEvent.projection_name == "chroma_story_chunks",
        )
    )
    message = ProjectionMessage(
        event_id=row.id,
        tenant_id=row.tenant_id,
        project_id=row.project_id,
        commit_id=row.commit_id,
        projection_name=row.projection_name,
        barrier_kind=row.barrier_kind,
        event_type=row.event_type,
        payload=row.payload_json,
    )
    vector = _ReplaceVector()
    projection = LegacySubsectionProjection(
        canonical_session,
        "tenant-1",
        "project-1",
        vector_store=vector,
    )
    projection.project(message)
    original = dict(vector.rows)
    vector.rows.clear()
    projection.project(message)

    assert vector.rows == original
