import hashlib
import json

import pytest
from pydantic import ValidationError

from app.writing.subsection_handover_history import (
    HandoverExtractionObservation,
    HandoverFieldArtifact,
    SubsectionHandoverRecord,
    canonical_json,
    observation_from_note,
    sha256_json,
    skipped_observation,
)
from app.writing.subsection_handover_persistence import (
    SubsectionHandoverHistoryRecorder,
    history_source_status,
    normalize_history,
)


class FakeBlackboard:
    def __init__(self):
        self.data = {}
        self.checkpoint = {}
        self.fail_set = False

    def get(self, task_id, key):
        return self.data.get(key)

    def set(self, task_id, key, value):
        if self.fail_set:
            raise RuntimeError("private value must not escape")
        self.data[key] = value

    def load_checkpoint(self, task_id):
        return dict(self.checkpoint)


def _observation(note):
    return observation_from_note(note)


def _capture(recorder, subsection=1, suffix="one", note=None, observation=None):
    output_hash = hashlib.sha256(f"output-{suffix}".encode()).hexdigest()
    return recorder.capture_committed(
        section=1,
        subsection=subsection,
        output_sha256=output_hash,
        prompt_messages_hash=hashlib.sha256(
            f"prompt-{suffix}".encode()
        ).hexdigest(),
        commit_idempotency_key=f"task:1:{subsection}",
        handover_note=note,
        observation=observation or _observation(note),
    )


def test_canonical_json_hash_is_stable():
    left = {"new_facts": ["A"], "character_state": "B"}
    right = {"character_state": "B", "new_facts": ["A"]}
    assert canonical_json(left) == canonical_json(right)
    assert sha256_json(left) == sha256_json(right)


def test_execution_statuses_are_distinct():
    changed = observation_from_note({
        "foreshadowing": "value",
        "character_state": "",
        "open_threads": "",
        "new_facts": [],
    })
    no_change = observation_from_note({
        "foreshadowing": "",
        "character_state": "",
        "open_threads": "",
        "new_facts": [],
    })
    invalid = observation_from_note({})
    skipped = skipped_observation("missing_committed_text")
    assert changed.execution_status == "completed_with_changes"
    assert no_change.execution_status == "completed_no_change"
    assert invalid.execution_status == "error"
    assert invalid.error_type == "InvalidHandoverPayload"
    assert skipped.execution_status == "skipped"
    assert skipped.executed is False


def test_contract_is_frozen_and_rejects_unknown_enum():
    observation = skipped_observation("not_run")
    with pytest.raises(ValidationError):
        observation.execution_status = "other"
    with pytest.raises(ValidationError):
        HandoverExtractionObservation(
            executed=True,
            execution_status="unknown",
        )


def test_record_and_source_ids_are_stable_and_replay_is_idempotent():
    board = FakeBlackboard()
    recorder = SubsectionHandoverHistoryRecorder(board, "task")
    note = {
        "foreshadowing": "private-value",
        "character_state": "",
        "open_threads": [],
        "new_facts": [],
    }
    first = _capture(recorder, note=note)
    second = _capture(recorder, note=note)
    history = normalize_history(board.data["subsection_handover_history_v1"])
    assert first == second
    assert len(history.records) == 1
    record = history.records[first]
    assert record.record_id == first
    assert record.handover_source_id.startswith("writer-handover:")
    assert record.field_count == 4
    assert all(field.source_hash == record.output_sha256 for field in record.fields)


def test_different_output_hashes_do_not_overwrite():
    board = FakeBlackboard()
    recorder = SubsectionHandoverHistoryRecorder(board, "task")
    note = {"foreshadowing": "", "character_state": "", "open_threads": ""}
    assert _capture(recorder, suffix="one", note=note)
    assert _capture(recorder, suffix="two", note=note)
    history = normalize_history(board.data["subsection_handover_history_v1"])
    assert len(history.records) == 2


def test_persistence_failure_is_fail_open_and_sanitized():
    board = FakeBlackboard()
    board.fail_set = True
    recorder = SubsectionHandoverHistoryRecorder(board, "task")
    note = {"foreshadowing": "must-not-be-logged"}
    assert _capture(recorder, note=note) is None
    assert not board.data


def test_historical_task_is_explicitly_unavailable():
    assert history_source_status(None) == (
        "historical_subsection_handover_unavailable"
    )
