from __future__ import annotations

# ruff: noqa: F401, F811 -- pytest registers the imported sibling fixture by name.

import pytest
from sqlalchemy import delete, select

from app.canonical.commit_service import CanonicalCommitService
from app.canonical.contracts import PreparedCanonicalCommit, SubsectionCandidate
from app.canonical.models import (
    CanonicalCommit,
    CanonicalStateVersion,
    DocumentRevision,
    EventLedger,
    OutboxEvent,
    ProjectionDelivery,
)
from app.canonical.projection_ports import ProjectionScope
from app.canonical.projection_registry import (
    DEFAULT_PROJECTOR_REGISTRY,
    NON_BLOCKING_RETRY,
    ProjectorRegistry,
    ProjectorSpec,
    projection_event_id,
)
from app.canonical.projection_replay import CanonicalProjectionReplay
from app.canonical.repositories import CanonicalRepository
from app.canonical.state_transition import LegacyStateTransitionAdapter
from tests.unit.canonical.test_commit_service import (
    _base_state,
    _prepared,
    canonical_session,
)


SCOPE = ProjectionScope(tenant_id="tenant-1", project_id="project-1")


def _commit(session, *, key="projection-replay", **prepared):
    return CanonicalCommitService(session, "tenant-1", "project-1").commit(
        _prepared(session, **prepared), key
    )


def _prepared_for_document(
    session,
    *,
    document_id,
    subsection_id,
    draft,
    base_state_version_id,
):
    original = _prepared(
        session,
        subsection_id=subsection_id,
        ordinal=1,
        draft=draft,
        base_state_version_id=base_state_version_id,
    ).candidate
    payload = original.model_dump(
        exclude={"candidate_hash", "draft_hash", "created_at"}
    )
    payload["document_id"] = document_id
    candidate = SubsectionCandidate.create(**payload)
    transition = LegacyStateTransitionAdapter().compile(
        base_state=_base_state(session, base_state_version_id),
        candidate=candidate,
    )
    return PreparedCanonicalCommit(candidate=candidate, state_transition=transition)


def test_delivery_and_canon_replay_have_same_semantic_identity(canonical_session):
    result = _commit(canonical_session)
    delivery = canonical_session.scalar(
        select(ProjectionDelivery).where(
            ProjectionDelivery.projector_id == "chroma_story_chunks"
        )
    )
    replay = CanonicalProjectionReplay(canonical_session)

    incremental = replay.message_for_delivery(delivery.id)
    rebuilt = tuple(
        replay.iter_messages(SCOPE, "chroma_story_chunks", 0, 1)
    )[0]

    assert incremental.projection_event_id == rebuilt.projection_event_id == (
        projection_event_id("chroma_story_chunks", result.commit_id)
    )
    assert incremental.commit_id == rebuilt.commit_id == result.commit_id
    assert incremental.payload == rebuilt.payload
    assert incremental.outbox_event_id == delivery.outbox_event_id
    assert incremental.delivery_id == delivery.id
    assert rebuilt.outbox_event_id is None
    assert rebuilt.delivery_id is None


def test_bootstrap_replay_does_not_require_historical_outbox(canonical_session):
    result = _commit(canonical_session)
    registry = ProjectorRegistry(
        (
            *DEFAULT_PROJECTOR_REGISTRY.all(),
            ProjectorSpec("search_index", "v1", "non_blocking", NON_BLOCKING_RETRY),
        )
    )
    replay = CanonicalProjectionReplay(canonical_session, registry=registry)

    assert canonical_session.scalar(
        select(OutboxEvent).where(
            OutboxEvent.projection_name == "search_index",
            OutboxEvent.commit_id == result.commit_id,
        )
    ) is None

    position = canonical_session.get(CanonicalCommit, result.commit_id).stream_position
    messages = tuple(replay.iter_messages(SCOPE, "search_index", 0, position))

    assert len(messages) == 1
    assert messages[0].commit_id == result.commit_id
    assert messages[0].outbox_event_id is None
    assert messages[0].delivery_id is None


def test_replay_payload_orders_ledger_by_ordinal(canonical_session):
    result = _commit(canonical_session)
    canonical_session.add(
        EventLedger(
            id="event-second",
            tenant_id="tenant-1",
            project_id="project-1",
            commit_id=result.commit_id,
            event_type="second.event",
            payload_json={"sequence": 2},
            evidence_refs_json=[{"source": "second"}],
            ordinal=2,
        )
    )
    canonical_session.commit()

    message = tuple(
        CanonicalProjectionReplay(canonical_session).iter_messages(
            SCOPE, "analytics", 0, 1
        )
    )[0]

    assert [event["ordinal"] for event in message.payload["ledger_events"]] == [1, 2]
    assert message.payload["ledger_events"][1] == {
        "event_id": "event-second",
        "event_type": "second.event",
        "payload": {"sequence": 2},
        "evidence_refs": [{"source": "second"}],
        "ordinal": 2,
    }


@pytest.mark.parametrize("tamper", ["revision", "state"])
def test_replay_rejects_canon_whose_hash_cannot_be_recomputed(
    canonical_session, tamper
):
    result = _commit(canonical_session)
    if tamper == "revision":
        revision = canonical_session.get(DocumentRevision, result.revision_id)
        revision.content_hash = "0" * 64
    else:
        state = canonical_session.get(CanonicalStateVersion, result.state_version_id)
        state.state_hash = "0" * 64
    canonical_session.commit()

    with pytest.raises(ValueError, match=f"{tamper}.*recomputed"):
        tuple(
            CanonicalProjectionReplay(canonical_session).iter_messages(
                SCOPE, "analytics", 0, 1
            )
        )


def test_materialize_document_at_uses_revision_heads_as_of_requested_position(
    canonical_session,
):
    first = _commit(canonical_session, key="first", draft="First v1")
    second = _commit(
        canonical_session,
        key="second",
        subsection_id="subsection-2",
        ordinal=2,
        draft="Second v1",
        base_state_version_id=first.state_version_id,
    )
    _commit(
        canonical_session,
        key="third",
        draft="First v2",
        base_revision_number=1,
        base_state_version_id=second.state_version_id,
    )
    replay = CanonicalProjectionReplay(canonical_session)

    assert replay.materialize_document_at(SCOPE, 1) == "First v1"
    assert replay.materialize_document_at(SCOPE, 2) == "First v1\n\nSecond v1"
    assert replay.materialize_document_at(SCOPE, 3) == "First v2\n\nSecond v1"


def test_materialize_document_at_isolates_documents_and_rejects_ambiguity(
    canonical_session,
):
    repo = CanonicalRepository(canonical_session, "tenant-1", "project-1")
    repo.create_document("document-2", "Second document")
    repo.create_subsection("document-2-subsection-1", "document-2", 1, 1, 1)
    canonical_session.commit()
    first = _commit(canonical_session, key="document-one", draft="Document one")
    second = CanonicalCommitService(
        canonical_session, "tenant-1", "project-1"
    ).commit(
        _prepared_for_document(
            canonical_session,
            document_id="document-2",
            subsection_id="document-2-subsection-1",
            draft="Document two",
            base_state_version_id=first.state_version_id,
        ),
        "document-two",
    )
    position = canonical_session.get(CanonicalCommit, second.commit_id).stream_position
    replay = CanonicalProjectionReplay(canonical_session)

    with pytest.raises(ValueError, match="multiple documents.*document_id"):
        replay.materialize_document_at(SCOPE, position)
    assert replay.materialize_document_at(
        SCOPE, position, document_id="document-1"
    ) == "Document one"
    assert replay.materialize_document_at(
        SCOPE, position, document_id="document-2"
    ) == "Document two"
    with pytest.raises(ValueError, match="outside scope or missing"):
        replay.materialize_document_at(SCOPE, position, document_id="missing")


def test_message_for_delivery_rejects_cross_scope_canon_join(canonical_session):
    _commit(canonical_session)
    delivery = canonical_session.scalar(select(ProjectionDelivery))
    delivery.tenant_id = "tenant-other"
    canonical_session.commit()

    with pytest.raises(ValueError, match="delivery.*Canon"):
        CanonicalProjectionReplay(canonical_session).message_for_delivery(delivery.id)
