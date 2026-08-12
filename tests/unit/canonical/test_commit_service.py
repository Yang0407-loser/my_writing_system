from __future__ import annotations

import pytest
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
    Base,
    CanonicalCommit,
    CanonicalProject,
    CanonicalStateVersion,
    CanonicalSubsection,
    DocumentRevision,
    EventLedger,
    IdempotencyRecord,
    OutboxEvent,
    ProjectionDelivery,
    ProjectionPartition,
)
from app.canonical.projection_registry import (
    DEFAULT_PROJECTOR_REGISTRY,
    NON_BLOCKING_RETRY,
    ProjectorRegistry,
    ProjectorSpec,
)
from app.canonical.repositories import CanonicalRepository
from app.canonical.state_transition import LegacyStateTransitionAdapter


@pytest.fixture
def canonical_session(tmp_path):
    url = f"sqlite+pysqlite:///{(tmp_path / 'commit.db').as_posix()}"
    engine = build_engine(url)
    Base.metadata.create_all(engine)
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
    assert _count(canonical_session, ProjectionDelivery) == len(PROJECTION_MANIFEST)
    project = canonical_session.get(CanonicalProject, "project-1")
    subsection = canonical_session.get(CanonicalSubsection, "subsection-1")
    assert project.current_state_version_id == result.state_version_id
    assert subsection.current_revision_id == result.revision_id
    assert set(result.outbox_event_ids) == set(
        canonical_session.scalars(
            select(OutboxEvent.id).where(OutboxEvent.commit_id == result.commit_id)
        ).all()
    )
    envelopes = canonical_session.scalars(
        select(OutboxEvent).where(OutboxEvent.commit_id == result.commit_id)
    ).all()
    deliveries = canonical_session.scalars(
        select(ProjectionDelivery).where(
            ProjectionDelivery.project_id == "project-1"
        )
    ).all()
    commit = canonical_session.get(CanonicalCommit, result.commit_id)
    assert {envelope.id for envelope in envelopes} == {
        delivery.outbox_event_id for delivery in deliveries
    }
    assert {envelope.stream_position for envelope in envelopes} == {1}
    assert {delivery.stream_position for delivery in deliveries} == {1}
    assert commit.stream_position == 1
    assert project.next_stream_position == 1


def test_project_is_locked_before_idempotency_reservation(canonical_session):
    service = CanonicalCommitService(canonical_session, "tenant-1", "project-1")
    locked = False
    original_get_project_for_update = service.repo.get_project_for_update

    def observe_project_lock():
        nonlocal locked
        project = original_get_project_for_update()
        locked = True
        return project

    def assert_reservation_is_under_lock(stage):
        if stage == "after_reservation":
            assert locked is True

    service.repo.get_project_for_update = observe_project_lock
    service.failure_hook = assert_reservation_is_under_lock

    service.commit(_prepared(canonical_session), "locked-reservation")


def test_disabled_registered_projector_gets_only_post_activation_envelope(
    canonical_session,
):
    search = ProjectorSpec(
        "search_index", "v1", "non_blocking", NON_BLOCKING_RETRY
    )
    registry = ProjectorRegistry((*DEFAULT_PROJECTOR_REGISTRY.all(), search))
    canonical_session.add(
        ProjectionPartition(
            id="partition-search",
            tenant_id="tenant-1",
            project_id="project-1",
            projector_id=search.projector_id,
            projector_version=search.version,
            enrollment_status="disabled",
            runtime_status="active",
            last_published_position=0,
            activation_after_position=None,
        )
    )
    canonical_session.commit()
    service = CanonicalCommitService(
        canonical_session,
        "tenant-1",
        "project-1",
        projector_registry=registry,
    )

    first = service.commit(_prepared(canonical_session), "before-search")
    assert canonical_session.scalar(
        select(func.count()).select_from(OutboxEvent).where(
            OutboxEvent.project_id == "project-1",
            OutboxEvent.projection_name == "search_index",
        )
    ) == 0

    partition = canonical_session.get(ProjectionPartition, "partition-search")
    partition.enrollment_status = "active"
    partition.activation_after_position = 1
    canonical_session.commit()
    second = service.commit(
        _prepared(
            canonical_session,
            draft="Accepted after activation",
            base_revision_number=1,
            base_state_version_id=first.state_version_id,
        ),
        "after-search",
    )

    search_envelopes = canonical_session.scalars(
        select(OutboxEvent).where(
            OutboxEvent.project_id == "project-1",
            OutboxEvent.projection_name == "search_index",
        )
    ).all()
    assert len(search_envelopes) == 1
    assert search_envelopes[0].commit_id == second.commit_id
    assert search_envelopes[0].stream_position == 2


@pytest.mark.parametrize(
    "failure_stage",
    ["after_stream_position", "after_outbox", "after_projection_deliveries"],
)
def test_projection_write_failures_roll_back_counter_canon_envelopes_and_deliveries(
    canonical_session, failure_stage
):
    tracked = (
        CanonicalCommit,
        DocumentRevision,
        CanonicalStateVersion,
        EventLedger,
        IdempotencyRecord,
        OutboxEvent,
        ProjectionDelivery,
    )
    before_counts = {model: _count(canonical_session, model) for model in tracked}
    before_project = canonical_session.get(CanonicalProject, "project-1")
    before_counter = before_project.next_stream_position
    before_state_head = before_project.current_state_version_id
    before_revision_head = canonical_session.get(
        CanonicalSubsection, "subsection-1"
    ).current_revision_id

    def fail(stage):
        if stage == failure_stage:
            raise RuntimeError(f"injected failure: {stage}")

    with pytest.raises(RuntimeError, match=failure_stage):
        CanonicalCommitService(
            canonical_session,
            "tenant-1",
            "project-1",
            failure_hook=fail,
        ).commit(_prepared(canonical_session), f"failure-{failure_stage}")

    canonical_session.expire_all()
    assert {model: _count(canonical_session, model) for model in tracked} == before_counts
    project = canonical_session.get(CanonicalProject, "project-1")
    assert project.next_stream_position == before_counter
    assert project.current_state_version_id == before_state_head
    assert canonical_session.get(
        CanonicalSubsection, "subsection-1"
    ).current_revision_id == before_revision_head


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
