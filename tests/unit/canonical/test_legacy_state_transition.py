from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.canonical.contracts import (
    CandidateValidation,
    CanonicalStateSnapshot,
    PreparedCanonicalCommit,
    SubsectionCandidate,
)
from app.canonical.errors import StateVersionConflict
from app.canonical.hashing import canonical_json_bytes, sha256_json, sha256_text
from app.canonical.legacy_candidate_adapter import adapt_legacy_handover
from app.canonical.state_transition import LegacyStateTransitionAdapter
from app.narrative_event import EventGraph
from app.world_state import WorldStateManager


FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "fixtures"
    / "foundation_golden_slice_v1.json"
)


def _fixture():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _candidate(fixture, *, base_state_version_id="state-foundation-genesis-v1"):
    adapted = adapt_legacy_handover(
        fixture["handover"],
        provenance={
            "task_id": fixture["ids"]["task_id"],
            "section": 1,
            "subsection": 1,
            "source": "golden_fixture",
        },
    )
    return SubsectionCandidate.create(
        tenant_id=fixture["ids"]["tenant_id"],
        project_id=fixture["ids"]["project_id"],
        document_id=fixture["ids"]["document_id"],
        subsection_id=fixture["ids"]["subsection_id"],
        task_id=fixture["ids"]["task_id"],
        section=1,
        subsection=1,
        ordinal=1,
        title=fixture["subsection"]["heading"],
        topic="Foundation Golden",
        base_revision_number=0,
        base_state_version_id=base_state_version_id,
        draft=fixture["subsection"]["body"],
        prompt_hash=sha256_text("golden-prompt"),
        validation=CandidateValidation(complete=True),
        handover_candidate=adapted.handover_candidate,
        world_mutations=adapted.world_mutations,
        events=adapted.events,
        state_frame=None,
        generation_metadata={"attempt_id": "golden-attempt"},
    )


def test_mismatched_project_state_head_raises_explicit_conflict():
    fixture = _fixture()
    base = CanonicalStateSnapshot.model_validate(fixture["initial_canonical_state"])
    candidate = _candidate(fixture, base_state_version_id="different-state")

    with pytest.raises(StateVersionConflict):
        LegacyStateTransitionAdapter().compile(base_state=base, candidate=candidate)


def test_same_base_and_candidate_compile_byte_identically():
    fixture = _fixture()
    base = CanonicalStateSnapshot.model_validate(fixture["initial_canonical_state"])
    candidate = _candidate(fixture)
    compiler = LegacyStateTransitionAdapter()

    first = compiler.compile(base_state=base, candidate=candidate)
    second = compiler.compile(base_state=base, candidate=candidate)

    assert canonical_json_bytes(first) == canonical_json_bytes(second)
    assert first.state_hash == sha256_json(first.next_state_json)
    assert first.candidate_hash == candidate.candidate_hash
    assert first.base_state_version_id == base.version_id


def test_transition_is_complete_preserves_opaque_fields_and_orders_generic_data():
    fixture = _fixture()
    raw_state = deepcopy(fixture["initial_canonical_state"])
    raw_state["state_json"]["opaque_future_domain"] = {"keep": [3, 2, 1]}
    raw_state["state_hash"] = sha256_json(raw_state["state_json"])
    base = CanonicalStateSnapshot.model_validate(raw_state)
    candidate = _candidate(fixture)

    result = LegacyStateTransitionAdapter().compile(
        base_state=base, candidate=candidate
    )

    assert result.next_state_json["opaque_future_domain"] == {"keep": [3, 2, 1]}
    envelope = result.next_state_json["foundation_state_v0"]
    mutation_ids = [item["mutation_id"] for item in envelope["world_mutations"]]
    assert mutation_ids == sorted(mutation_ids)
    event_ids = [item["event_id"] for item in envelope["ledger_events"]]
    assert len(event_ids) == len(set(event_ids))
    assert [item["ordinal"] for item in envelope["ledger_events"]] == list(
        range(1, len(envelope["ledger_events"]) + 1)
    )
    assert envelope["source_candidate_hash"] == candidate.candidate_hash


def test_compiler_is_pure_and_does_not_mutate_inputs(monkeypatch):
    fixture = _fixture()
    base = CanonicalStateSnapshot.model_validate(fixture["initial_canonical_state"])
    candidate = _candidate(fixture)
    base_before = deepcopy(base.model_dump())
    candidate_before = deepcopy(candidate.model_dump())
    add_fact = MagicMock(side_effect=AssertionError("WorldState call"))
    update_arc = MagicMock(side_effect=AssertionError("EventGraph call"))
    monkeypatch.setattr(WorldStateManager, "add_fact", add_fact)
    monkeypatch.setattr(EventGraph, "update_arc_status", update_arc)

    result = LegacyStateTransitionAdapter().compile(
        base_state=base, candidate=candidate
    )

    assert base.model_dump() == base_before
    assert candidate.model_dump() == candidate_before
    add_fact.assert_not_called()
    update_arc.assert_not_called()
    prepared = PreparedCanonicalCommit(candidate=candidate, state_transition=result)
    assert prepared.state_transition.state_hash == sha256_json(
        prepared.state_transition.next_state_json
    )
