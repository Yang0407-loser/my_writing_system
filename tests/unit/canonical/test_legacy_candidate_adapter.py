from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import MagicMock

from app.canonical.legacy_candidate_adapter import adapt_legacy_handover
from app.narrative_event import EventGraph
from app.world_state import WorldStateManager


FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "fixtures"
    / "foundation_golden_slice_v1.json"
)


def _provenance():
    return {
        "task_id": "task-foundation-golden",
        "section": 1,
        "subsection": 1,
        "source": "legacy_handover",
    }


def test_golden_handover_becomes_generic_mutation_and_event_candidates(monkeypatch):
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    add_fact = MagicMock(side_effect=AssertionError("WorldState must not be called"))
    update_arc = MagicMock(side_effect=AssertionError("EventGraph must not be called"))
    monkeypatch.setattr(WorldStateManager, "add_fact", add_fact)
    monkeypatch.setattr(EventGraph, "update_arc_status", update_arc)

    result = adapt_legacy_handover(fixture["handover"], provenance=_provenance())

    assert result.world_mutations
    assert result.world_mutations[0].predicate == "location.bakery.name"
    assert result.events
    assert result.events[0].event_type == "legacy.arc_progress"
    assert result.events[0].payload["status"] == "done"
    assert result.warnings == ()
    add_fact.assert_not_called()
    update_arc.assert_not_called()


def test_legacy_string_facts_and_mapping_arc_progress_are_supported():
    result = adapt_legacy_handover(
        {
            "new_facts": [" 林晚回到家 "],
            "arc_progress": {"linwan": "deviated", "other": "active"},
        },
        provenance=_provenance(),
    )

    assert [item.value for item in result.world_mutations] == ["林晚回到家"]
    assert result.world_mutations[0].predicate == "legacy.new_fact"
    assert [item.payload["status"] for item in result.events] == ["deviated"]
    assert any("other" in warning for warning in result.warnings)


def test_empty_and_invalid_values_are_rejected_as_warnings_without_mutation():
    handover = {
        "new_facts": ["", None, {"subject": "missing predicate"}],
        "arc_progress": [None, {"arc_id": "arc", "status": "unknown"}],
        "opaque_business_field": {"keep": True},
    }
    before = deepcopy(handover)

    result = adapt_legacy_handover(handover, provenance=_provenance())

    assert result.world_mutations == ()
    assert result.events == ()
    assert len(result.warnings) >= 4
    assert result.handover_candidate["opaque_business_field"] == {"keep": True}
    assert handover == before


def test_candidate_ids_are_stable_and_input_is_not_modified():
    handover = {
        "new_facts": ["Fact A", "Fact B"],
        "arc_progress": {"character-1": "done"},
    }
    before = deepcopy(handover)

    first = adapt_legacy_handover(handover, provenance=_provenance())
    second = adapt_legacy_handover(handover, provenance=_provenance())

    assert first == second
    assert handover == before
    assert len({item.mutation_id for item in first.world_mutations}) == 2
