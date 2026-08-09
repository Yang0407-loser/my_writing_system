from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.canonical.commit_service import CanonicalCommitService
from app.canonical.contracts import PreparedCanonicalCommit
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
from tests.unit.canonical.test_commit_service import _prepared

pytest_plugins = ("tests.unit.canonical.test_commit_service",)


TRACKED_MODELS = (
    CanonicalCommit,
    DocumentRevision,
    CanonicalStateVersion,
    EventLedger,
    IdempotencyRecord,
    OutboxEvent,
)


def _counts(session):
    return {
        model.__tablename__: session.scalar(select(func.count()).select_from(model))
        for model in TRACKED_MODELS
    }


@pytest.mark.parametrize(
    "failure_stage",
    [
        "after_reservation",
        "after_revision",
        "after_state",
        "after_ledger",
        "after_outbox",
        "before_commit",
    ],
)
def test_failure_at_each_write_stage_rolls_back_every_row_and_both_heads(
    canonical_session, failure_stage
):
    prepared = _prepared(canonical_session)
    before_counts = _counts(canonical_session)
    before_project_head = canonical_session.get(
        CanonicalProject, "project-1"
    ).current_state_version_id
    before_revision_head = canonical_session.get(
        CanonicalSubsection, "subsection-1"
    ).current_revision_id

    def fail(stage):
        if stage == failure_stage:
            raise RuntimeError(f"injected failure: {stage}")

    service = CanonicalCommitService(
        canonical_session,
        "tenant-1",
        "project-1",
        failure_hook=fail,
    )
    with pytest.raises(RuntimeError, match="injected failure"):
        service.commit(prepared, f"failure-{failure_stage}")

    canonical_session.expire_all()
    assert _counts(canonical_session) == before_counts
    assert canonical_session.get(
        CanonicalProject, "project-1"
    ).current_state_version_id == before_project_head
    assert canonical_session.get(
        CanonicalSubsection, "subsection-1"
    ).current_revision_id == before_revision_head
    assert canonical_session.scalar(
        select(IdempotencyRecord).where(
            IdempotencyRecord.idempotency_key == f"failure-{failure_stage}"
        )
    ) is None


def test_tampered_transition_hash_is_rejected_before_any_write(canonical_session):
    prepared = _prepared(canonical_session)
    tampered_transition = prepared.state_transition.model_construct(
        **{
            **prepared.state_transition.model_dump(),
            "next_state_json": {"tampered": True},
        }
    )
    tampered = PreparedCanonicalCommit.model_construct(
        candidate=prepared.candidate,
        state_transition=tampered_transition,
    )
    before = _counts(canonical_session)

    with pytest.raises(ValueError, match="state_hash"):
        CanonicalCommitService(
            canonical_session, "tenant-1", "project-1"
        ).commit(tampered, "tampered")

    assert _counts(canonical_session) == before


def test_incomplete_validation_is_rejected_before_any_write(canonical_session):
    prepared = _prepared(canonical_session)
    invalid_validation = prepared.candidate.validation.model_copy(
        update={"complete": False, "errors": ("incomplete",)}
    )
    invalid_candidate = prepared.candidate.model_construct(
        **{
            **prepared.candidate.model_dump(),
            "validation": invalid_validation,
        }
    )
    invalid = PreparedCanonicalCommit.model_construct(
        candidate=invalid_candidate,
        state_transition=prepared.state_transition,
    )
    before = _counts(canonical_session)

    with pytest.raises(ValueError, match="validation.complete"):
        CanonicalCommitService(
            canonical_session, "tenant-1", "project-1"
        ).commit(invalid, "incomplete")

    assert _counts(canonical_session) == before
