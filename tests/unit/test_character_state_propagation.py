import copy
import json
from unittest.mock import MagicMock

from app import coordinator
from app.agents.character_manager import CharacterManager
from app.writing.character_state_propagation import (
    build_character_state_propagation_event,
    character_arcs_hash,
    resolve_writer_character_arcs,
)


def _manager(response):
    manager = CharacterManager.__new__(CharacterManager)
    manager.llm = MagicMock()
    manager.llm.chat_completion.return_value = response
    manager.last_raw_response = ""
    return manager


def _characters():
    return [{"id": "c1", "name": "A"}, {"id": "c2", "name": "B"}]


def _arcs():
    return [
        {"character_id": "c1", "current_state": "old-1"},
        {"character_id": "c2", "current_state": "old-2"},
    ]


def test_character_state_update_is_atomic_and_does_not_mutate_input():
    arcs = _arcs()
    original = copy.deepcopy(arcs)
    response = json.dumps([
        {"character_id": "c1", "current_state": "new-1"},
        {"character_id": "c2", "current_state": "new-2"},
    ])

    result = _manager(response).update_states(_characters(), arcs, "section", 1)

    assert arcs == original
    assert [item["current_state"] for item in result] == ["new-1", "new-2"]


def test_partial_character_state_response_keeps_entire_prior_state():
    arcs = _arcs()
    response = json.dumps([{"character_id": "c1", "current_state": "new-1"}])
    result = _manager(response).update_states(_characters(), arcs, "section", 1)
    assert result == arcs
    assert result is not arcs


def test_invalid_character_state_response_keeps_prior_state():
    arcs = _arcs()
    result = _manager("not-json").update_states(_characters(), arcs, "section", 1)
    assert result == arcs


def test_character_state_hash_is_canonical_and_contains_no_payload():
    left = [{"character_id": "c1", "current_state": "private"}]
    right = [{"current_state": "private", "character_id": "c1"}]
    assert character_arcs_hash(left) == character_arcs_hash(right)
    assert "private" not in character_arcs_hash(left)


def test_old_writer_result_falls_back_without_mutating_legacy_state():
    fallback = _arcs()
    resolved, source = resolve_writer_character_arcs({"draft": "text"}, fallback)
    resolved[0]["current_state"] = "changed"
    assert source == "missing_writer_state"
    assert fallback[0]["current_state"] == "old-1"


def test_coordinator_adopts_writer_state_and_tracks_matching_hashes():
    old_arcs = _arcs()
    updated = copy.deepcopy(old_arcs)
    updated[0]["current_state"] = "new-1"
    updated_hash = character_arcs_hash(updated)
    event = build_character_state_propagation_event(
        task_id="task-1",
        section=1,
        subsection=None,
        source="writer_updated",
        input_state_hash=character_arcs_hash(old_arcs),
        updated_state_hash=updated_hash,
        checkpoint_state_hash=updated_hash,
        update_applied=True,
        checkpoint_version="phase4r-r1",
    )
    state = {"character_arcs": copy.deepcopy(old_arcs)}
    blackboard = MagicMock()

    resolved, propagation = coordinator._apply_writer_character_state(
        blackboard,
        "task-1",
        state,
        {"character_arcs": updated, "character_state_propagation": event},
        old_arcs,
    )

    assert resolved == state["character_arcs"] == updated
    assert propagation["updated_state_hash"] == updated_hash
    assert propagation["coordinator_state_hash"] == updated_hash
    assert propagation["checkpoint_state_hash"] == updated_hash
    blackboard.set.assert_called_once_with("task-1", "character_arcs", updated)


def test_coordinator_rejects_invalid_writer_state_and_keeps_fallback():
    fallback = _arcs()
    state = {"character_arcs": copy.deepcopy(fallback)}
    resolved, propagation = coordinator._apply_writer_character_state(
        MagicMock(), "task-1", state, {"character_arcs": {"bad": "shape"}}, fallback
    )
    assert resolved == fallback
    assert propagation["source"] == "invalid_writer_state"
    assert propagation["fallback_reason"] == "invalid_writer_state"
