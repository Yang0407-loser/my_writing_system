from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select

from app.canonical.commit_service import CanonicalCommitService, PROJECTION_MANIFEST
from app.canonical.contracts import (
    CandidateValidation,
    CanonicalEventCandidate,
    CanonicalStateSnapshot,
    PreparedCanonicalCommit,
    SubsectionCandidate,
)
from app.canonical.database import build_engine, build_session_factory
from app.canonical.errors import (
    IdempotencyConflict,
    RevisionConflict,
    StateVersionConflict,
)
from app.canonical.hashing import sha256_text
from app.canonical.models import (
    CanonicalCommit,
    CanonicalProject,
    CanonicalStateVersion,
    CanonicalSubsection,
    DocumentRevision,
    EventLedger,
    IdempotencyRecord,
    OutboxEvent,
)
from app.canonical.repositories import CanonicalRepository
from app.canonical.state_transition import LegacyStateTransitionAdapter


def _migrate(url: str) -> None:
    root = Path(__file__).resolve().parents[3]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")


@pytest.fixture
def canonical_session(tmp_path):
    url = f"sqlite+pysqlite:///{(tmp_path / 'commit.db').as_posix()}"
    _migrate(url)
    engine = build_engine(url)
    factory = build_session_factory(engine)
    with factory() as session:
        repo = CanonicalRepository(session, "tenant-1", "project-1")
        repo.create_project(
            owner_id="owner-1",
            name="Project",
            genesis_state_json={"foundation_state_v0": {"world_mutations": [], "ledger_events": []}},
            genesis_state_version_id="state-genesis",
        )
        repo.create_document("document-1", "Document")
        repo.create_subsection("subsection-1", "document-1", 1, 1, 1)
        repo.create_subsection("subsection-2", "document-1", 2, 1, 2)
        session.commit()
        yield session
    engine.dispose()


def _base_state(session, version_id: str = "state-genesis"):
    row = session.get(CanonicalStateVersion, version_id)
    return CanonicalStateSnapshot.create(
        version_id=row.id,
        project_id=row.project_id,
        schema_version=row.schema_version,
        state_json=row.state_json,
    )


def _prepared(
    session,
    *,
    subsection_id="subsection-1",
    ordinal=1,
    draft="Accepted draft",
    base_revision_number=0,
    base_state_version_id="state-genesis",
):
    candidate = SubsectionCandidate.create(
        tenant_id="tenant-1",
        project_id="project-1",
        document_id="document-1",
        subsection_id=subsection_id,
        task_id="task-1",
        section=1,
        subsection=ordinal,
        ordinal=ordinal,
        title=f"Subsection {ordinal}",
        topic="Atomic commit",
        base_revision_number=base_revision_number,
        base_state_version_id=base_state_version_id,
        draft=draft,
        prompt_hash=sha256_text("prompt"),
        validation=CandidateValidation(complete=True),
        handover_candidate={"summary": "done"},
        world_mutations=(),
        events=(
            CanonicalEventCandidate(
                event_id=f"event-{subsection_id}-{base_revision_number}",
                event_type="subsection.accepted",
                payload={"subsection_id": subsection_id},
                provenance={"source": "test"},
            ),
        ),
        state_frame=None,
        generation_metadata={"attempt_id": "attempt-1"},
    )
    transition = LegacyStateTransitionAdapter().compile(
        base_state=_base_state(session, base_state_version_id),
        candidate=candidate,
    )
    return PreparedCanonicalCommit(candidate=candidate, state_transition=transition)


def _count(session, model):
    return session.scalar(select(func.count()).select_from(model))


def test_first_commit_writes_all_canon_moves_both_heads_and_manifest(canonical_session):
    prepared = _prepared(canonical_session)
    service = CanonicalCommitService(
        canonical_session, tenant_id="tenant-1", project_id="project-1"
    )

    result = service.commit(prepared, idempotency_key="key-1")

    assert result.skipped_as_duplicate is False
    assert result.content_hash == prepared.candidate.draft_hash
    assert _count(canonical_session, CanonicalCommit) == 1
    assert _count(canonical_session, DocumentRevision) == 1
    assert _count(canonical_session, CanonicalStateVersion) == 2
    assert _count(canonical_session, EventLedger) == 1
    assert _count(canonical_session, IdempotencyRecord) == 1
    assert _count(canonical_session, OutboxEvent) == len(PROJECTION_MANIFEST)
    project = canonical_session.get(CanonicalProject, "project-1")
    subsection = canonical_session.get(CanonicalSubsection, "subsection-1")
    assert project.current_state_version_id == result.state_version_id
    assert subsection.current_revision_id == result.revision_id
    assert set(result.outbox_event_ids) == set(
        canonical_session.scalars(
            select(OutboxEvent.id).where(OutboxEvent.commit_id == result.commit_id)
        ).all()
    )


def test_same_key_and_hash_replays_original_result_without_new_rows(canonical_session):
    prepared = _prepared(canonical_session)
    service = CanonicalCommitService(
        canonical_session, tenant_id="tenant-1", project_id="project-1"
    )
    first = service.commit(prepared, "same-key")
    counts = {
        model: _count(canonical_session, model)
        for model in (CanonicalCommit, DocumentRevision, CanonicalStateVersion, EventLedger, IdempotencyRecord, OutboxEvent)
    }

    replay = service.commit(prepared, "same-key")

    assert replay.commit_id == first.commit_id
    assert replay.revision_id == first.revision_id
    assert replay.skipped_as_duplicate is True
    assert {model: _count(canonical_session, model) for model in counts} == counts


def test_same_key_with_different_hash_is_explicit_conflict(canonical_session):
    service = CanonicalCommitService(
        canonical_session, tenant_id="tenant-1", project_id="project-1"
    )
    service.commit(_prepared(canonical_session, draft="first"), "conflict-key")
    different = _prepared(
        canonical_session,
        draft="different",
        base_revision_number=1,
        base_state_version_id=canonical_session.get(CanonicalProject, "project-1").current_state_version_id,
    )

    with pytest.raises(IdempotencyConflict):
        service.commit(different, "conflict-key")


def test_stale_revision_is_rejected(canonical_session):
    service = CanonicalCommitService(canonical_session, "tenant-1", "project-1")
    first = service.commit(_prepared(canonical_session), "first-key")
    stale = _prepared(
        canonical_session,
        draft="stale",
        base_revision_number=0,
        base_state_version_id=first.state_version_id,
    )

    with pytest.raises(RevisionConflict):
        service.commit(stale, "stale-key")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("document_id", "other-document"),
        ("ordinal", 2),
        ("section", 2),
        ("subsection", 2),
    ],
)
def test_candidate_binding_must_match_locked_subsection(
    canonical_session, field, value
):
    prepared = _prepared(canonical_session)
    mismatched = prepared.candidate.model_copy(update={field: value})
    # model_copy intentionally bypasses the factory hash recalculation; rebuild
    # through create so this test reaches the locked-row binding guard.
    payload = mismatched.model_dump(
        exclude={"candidate_hash", "draft_hash", "created_at"}
    )
    candidate = SubsectionCandidate.create(**payload)
    transition = LegacyStateTransitionAdapter().compile(
        base_state=_base_state(canonical_session), candidate=candidate
    )

    with pytest.raises(RevisionConflict, match="binding"):
        CanonicalCommitService(
            canonical_session, "tenant-1", "project-1"
        ).commit(
            PreparedCanonicalCommit(
                candidate=candidate, state_transition=transition
            ),
            f"binding-{field}",
        )


def test_state_head_conflict_rejects_other_unchanged_subsection(canonical_session):
    second_prepared_from_old_state = _prepared(
        canonical_session, subsection_id="subsection-2", ordinal=2
    )
    service = CanonicalCommitService(canonical_session, "tenant-1", "project-1")
    service.commit(_prepared(canonical_session), "first-key")

    with pytest.raises(StateVersionConflict):
        service.commit(second_prepared_from_old_state, "second-key")
