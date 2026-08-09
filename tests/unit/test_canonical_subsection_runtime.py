from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.canonical.commit_service import PROJECTION_MANIFEST
from app.canonical.contracts import CanonicalStateSnapshot
from app.canonical.errors import RevisionConflict
from app.canonical.repositories import CanonicalRepository
from app.writing.canonical_subsection_runtime import (
    CanonicalSubsectionCommand,
    CanonicalSubsectionRuntime,
    canonical_idempotency_key,
)
from tests.unit.canonical.test_commit_service import _prepared, canonical_session


def _command(**overrides):
    values = {
        "task_id": "task-1",
        "document_id": "document-1",
        "subsection_id": "subsection-1",
        "generation_attempt_id": "attempt-1",
        "expected_revision_id": "GENESIS",
        "expected_state_version_id": "state-genesis",
    }
    values.update(overrides)
    return CanonicalSubsectionCommand(**values)


def _projectors(**overrides):
    events = []

    def projector(name):
        mock = MagicMock(side_effect=lambda _message: events.append(name))
        return mock

    result = {name: projector(name) for name, _ in PROJECTION_MANIFEST}
    result.update(overrides)
    return result, events


def _generator(session, mock=None):
    mock = mock or MagicMock()

    def generate(*, snapshot, base_revision_number, command):
        mock(snapshot=snapshot, base_revision_number=base_revision_number, command=command)
        return _prepared(
            session,
            subsection_id=command.subsection_id,
            ordinal=1 if command.subsection_id == "subsection-1" else 2,
            base_revision_number=base_revision_number,
            base_state_version_id=snapshot.version_id,
        ).candidate

    return generate, mock


def _runtime(session, *, projectors=None, generator=None, checkpoints=None):
    if projectors is None:
        projectors, _ = _projectors()
    if generator is None:
        generator, _ = _generator(session)
    checkpoints = checkpoints if checkpoints is not None else []
    return CanonicalSubsectionRuntime(
        session=session,
        tenant_id="tenant-1",
        project_id="project-1",
        candidate_generator=generator,
        projectors=projectors,
        checkpoint_writer=lambda value: checkpoints.append(dict(value)),
    )


def test_runtime_fixed_order_commits_before_critical_and_nonblocking(canonical_session):
    projectors, events = _projectors()
    generator, generated = _generator(canonical_session)
    checkpoints = []
    runtime = _runtime(
        canonical_session,
        projectors=projectors,
        generator=generator,
        checkpoints=checkpoints,
    )

    result = runtime.execute(_command())

    assert result.phase == "ready"
    assert result.critical_projection_status == "ready"
    assert events == [name for name, _ in PROJECTION_MANIFEST]
    assert generated.call_count == 1
    assert checkpoints[0]["last_commit_id"] == result.commit.commit_id
    assert checkpoints[0]["critical_projection_status"] == "pending"
    assert checkpoints[-1]["critical_projection_status"] == "ready"
    assert checkpoints[-1]["current_revision_id"] == result.commit.revision_id
    assert checkpoints[-1]["current_state_version_id"] == result.commit.state_version_id


def test_retry_preflight_skips_generation_and_returns_original_commit(canonical_session):
    failed = MagicMock(side_effect=RuntimeError("critical down"))
    first_projectors, _ = _projectors(handover_context=failed)
    generator, generated = _generator(canonical_session)
    command = _command()
    first = _runtime(
        canonical_session, projectors=first_projectors, generator=generator
    ).execute(command)
    assert first.phase == "awaiting_critical_projection"

    retry_generator = MagicMock(side_effect=AssertionError("LLM must not run"))
    retry_projectors, _ = _projectors()
    retried = _runtime(
        canonical_session,
        projectors=retry_projectors,
        generator=retry_generator,
    ).execute(command)

    assert retried.commit.commit_id == first.commit.commit_id
    assert retried.commit.skipped_as_duplicate is True
    assert retried.phase == "ready"
    retry_generator.assert_not_called()
    assert canonical_idempotency_key(command) == first.commit.idempotency_key


def test_commit_conflict_produces_zero_projection(canonical_session):
    projectors, _ = _projectors()
    generator, _ = _generator(canonical_session)
    runtime = _runtime(
        canonical_session, projectors=projectors, generator=generator
    )

    with pytest.raises(RevisionConflict):
        runtime.execute(_command(expected_revision_id="missing-revision"))

    assert all(projector.call_count == 0 for projector in projectors.values())


def test_critical_failure_pauses_sequence_before_next_llm(canonical_session):
    failing = MagicMock(side_effect=RuntimeError("chroma down"))
    projectors, _ = _projectors(chroma_story_chunks=failing)
    first_generator, first_llm = _generator(canonical_session)
    runtime = _runtime(
        canonical_session, projectors=projectors, generator=first_generator
    )
    second_llm = MagicMock(side_effect=AssertionError("next LLM must be paused"))

    results = runtime.execute_sequence(
        [
            (_command(), first_generator),
            (
                _command(
                    subsection_id="subsection-2",
                    generation_attempt_id="attempt-2",
                    expected_state_version_id="not-yet-consumable",
                ),
                second_llm,
            ),
        ]
    )

    assert len(results) == 1
    assert results[0].phase == "awaiting_critical_projection"
    assert first_llm.call_count == 1
    second_llm.assert_not_called()


def test_nonblocking_failure_does_not_pause_next_subsection(canonical_session):
    failing = MagicMock(side_effect=RuntimeError("analytics down"))
    projectors, _ = _projectors(analytics=failing)
    generator, _ = _generator(canonical_session)
    runtime = _runtime(
        canonical_session, projectors=projectors, generator=generator
    )

    result = runtime.execute(_command())

    assert result.phase == "ready"
    assert result.non_blocking_projection_status == "lagging"
    assert result.non_blocking_summary == {"published": 3, "failed": 1}


def test_runtime_fails_closed_when_scope_or_heads_are_missing(canonical_session):
    generator, _ = _generator(canonical_session)
    runtime = _runtime(canonical_session, generator=generator)

    with pytest.raises(ValueError, match="expected_state_version_id"):
        runtime.execute(_command(expected_state_version_id=""))
    with pytest.raises(ValueError, match="document binding"):
        runtime.execute(_command(document_id="missing"))


def test_loaded_snapshot_is_the_candidate_base(canonical_session):
    captured = {}

    def generate(*, snapshot, base_revision_number, command):
        assert isinstance(snapshot, CanonicalStateSnapshot)
        captured["version"] = snapshot.version_id
        return _prepared(
            canonical_session,
            base_revision_number=base_revision_number,
            base_state_version_id=snapshot.version_id,
        ).candidate

    result = _runtime(canonical_session, generator=generate).execute(_command())

    assert captured["version"] == "state-genesis"
    assert result.phase == "ready"
