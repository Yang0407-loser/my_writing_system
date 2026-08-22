from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.canonical.contracts import (
    CandidateValidation,
    CanonicalCommitResult,
    CanonicalEventCandidate,
    CanonicalStateSnapshot,
    PreparedCanonicalCommit,
    StateTransitionResult,
    SubsectionCandidate,
    WorldMutationCandidate,
)
from app.canonical.errors import (
    IdempotencyConflict,
    ProjectionBarrierPending,
    RevisionConflict,
    StateVersionConflict,
)
from app.canonical.hashing import sha256_json, sha256_text


def _candidate(**overrides) -> SubsectionCandidate:
    values = {
        "tenant_id": "tenant-1",
        "project_id": "project-1",
        "document_id": "document-1",
        "subsection_id": "subsection-1",
        "task_id": "task-1",
        "section": 1,
        "subsection": 1,
        "ordinal": 1,
        "title": "First",
        "topic": "Foundation",
        "base_revision_number": 0,
        "base_state_version_id": "state-v0",
        "draft": "A deterministic draft.",
        "prompt_hash": sha256_text("prompt"),
        "validation": CandidateValidation(complete=True),
        "handover_candidate": {"summary": "done"},
        "world_mutations": (
            WorldMutationCandidate(
                mutation_id="mutation-1",
                predicate="location.name",
                subject="bakery",
                value="Wild Bread",
                provenance={"source": "handover"},
                evidence=("line-1",),
            ),
        ),
        "events": (
            CanonicalEventCandidate(
                event_id="event-1",
                event_type="arc_progress",
                payload={"status": "done"},
                provenance={"source": "handover"},
            ),
        ),
        "state_frame": {"location": "bakery"},
        "generation_metadata": {"attempt_id": "attempt-1", "model": "test"},
    }
    values.update(overrides)
    return SubsectionCandidate.create(**values)


def _transition(candidate: SubsectionCandidate) -> StateTransitionResult:
    return StateTransitionResult.create(
        transition_version="legacy-transition-v0",
        candidate_hash=candidate.candidate_hash,
        base_state_version_id=candidate.base_state_version_id,
        next_state_json={"foundation_state_v0": {"world_mutations": []}},
        ledger_events=candidate.events,
    )


def test_frozen_models_forbid_extra_fields_and_mutation():
    candidate = _candidate()

    with pytest.raises(ValidationError):
        SubsectionCandidate.model_validate({**candidate.model_dump(), "surprise": True})
    with pytest.raises(ValidationError):
        candidate.title = "changed"


def test_draft_and_candidate_hashes_are_recomputed_and_field_order_independent():
    candidate = _candidate(
        generation_metadata={"z": 3, "nested": {"b": 2, "a": 1}}
    )
    reordered = _candidate(
        generation_metadata={"nested": {"a": 1, "b": 2}, "z": 3}
    )

    assert candidate.draft_hash == sha256_text(candidate.draft)
    assert candidate.candidate_hash == reordered.candidate_hash
    assert candidate.candidate_hash == sha256_json(candidate.hash_payload())


def test_changed_draft_changes_both_hashes():
    first = _candidate(draft="first")
    second = _candidate(draft="second")

    assert first.draft_hash != second.draft_hash
    assert first.candidate_hash != second.candidate_hash


def test_self_reported_hashes_are_not_trusted():
    candidate = _candidate()
    payload = candidate.model_dump()

    with pytest.raises(ValidationError, match="draft_hash"):
        SubsectionCandidate.model_validate({**payload, "draft_hash": "0" * 64})
    with pytest.raises(ValidationError, match="candidate_hash"):
        SubsectionCandidate.model_validate({**payload, "candidate_hash": "f" * 64})


def test_base_state_version_is_required_and_part_of_candidate_hash():
    first = _candidate(base_state_version_id="state-v0")
    second = _candidate(base_state_version_id="state-v1")

    assert first.candidate_hash != second.candidate_hash
    with pytest.raises((ValidationError, TypeError)):
        values = first.model_dump()
        values.pop("base_state_version_id")
        SubsectionCandidate.model_validate(values)


def test_incomplete_validation_rejects_candidate():
    with pytest.raises(ValidationError, match="validation.complete"):
        _candidate(validation=CandidateValidation(complete=False, errors=("bad",)))


def test_state_snapshot_and_transition_hashes_are_recomputed():
    snapshot = CanonicalStateSnapshot.create(
        version_id="state-v0",
        project_id="project-1",
        schema_version="canonical-state-v0",
        state_json={"b": 2, "a": 1},
    )
    candidate = _candidate()
    transition = _transition(candidate)

    assert snapshot.state_hash == sha256_json({"a": 1, "b": 2})
    assert transition.state_hash == sha256_json(transition.next_state_json)
    with pytest.raises(ValidationError, match="state_hash"):
        StateTransitionResult.model_validate(
            {**transition.model_dump(), "state_hash": "0" * 64}
        )


def test_prepared_commit_requires_candidate_and_transition_alignment():
    candidate = _candidate()
    transition = _transition(candidate)

    prepared = PreparedCanonicalCommit(
        candidate=candidate, state_transition=transition
    )
    assert prepared.candidate.candidate_hash == prepared.state_transition.candidate_hash

    other = _candidate(draft="other")
    with pytest.raises(ValidationError, match="candidate_hash"):
        PreparedCanonicalCommit(candidate=other, state_transition=transition)


def test_contracts_round_trip_without_mutating_inputs():
    metadata = {"nested": {"values": [1, 2]}}
    before = deepcopy(metadata)
    candidate = _candidate(generation_metadata=metadata)
    transition = _transition(candidate)
    prepared = PreparedCanonicalCommit(candidate=candidate, state_transition=transition)

    restored = PreparedCanonicalCommit.model_validate_json(prepared.model_dump_json())

    assert restored == prepared
    assert metadata == before


def test_commit_result_exposes_duplicate_flag_and_is_frozen():
    result = CanonicalCommitResult(
        commit_id="commit-1",
        revision_id="revision-1",
        revision_number=1,
        state_version_id="state-v1",
        content_hash=sha256_text("draft"),
        outbox_event_ids=("outbox-1",),
        idempotency_key="subsection-commit:v0:key",
        candidate_hash="a" * 64,
        skipped_as_duplicate=True,
    )

    assert result.schema_version == "canonical-commit-result-v0"
    assert result.skipped_as_duplicate is True
    with pytest.raises(ValidationError):
        result.skipped_as_duplicate = False


@pytest.mark.parametrize(
    "error_type",
    [RevisionConflict, StateVersionConflict, IdempotencyConflict, ProjectionBarrierPending],
)
def test_canonical_conflicts_have_explicit_error_types(error_type):
    assert issubclass(error_type, Exception)
