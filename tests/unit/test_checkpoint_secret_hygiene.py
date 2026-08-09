from __future__ import annotations

from copy import deepcopy

import fakeredis
import pytest

from app.blackboard import Blackboard
import app.coordinator as coordinator


def _blackboard() -> Blackboard:
    board = Blackboard()
    board._redis = fakeredis.FakeRedis()
    return board


def _assert_no_credential_fields(value) -> None:
    credential_names = {"api_key", "llm_api_key", "authorization", "x_api_key"}
    if isinstance(value, dict):
        assert credential_names.isdisjoint({str(key).lower() for key in value})
        for nested in value.values():
            _assert_no_credential_fields(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            _assert_no_credential_fields(nested)


def test_save_checkpoint_removes_nested_credentials_without_mutating_input():
    board = _blackboard()
    state = {
        "api_key": "root-plaintext-secret",
        "phase": "writing",
        "nested": {
            "llm_api_key": "nested-plaintext-secret",
            "authorization": "Bearer plaintext-secret",
            "authorization_status": "approved",
            "token_usage": 42,
            "items": [
                {
                    "x_api_key": "header-plaintext-secret",
                    "business_secret_name": "keep-this-business-value",
                }
            ],
        },
    }
    original = deepcopy(state)

    board.save_checkpoint("task-secret", state)

    raw = board._redis.hgetall(board.checkpoint_key("task-secret"))
    serialized = b" ".join(raw.keys()) + b" " + b" ".join(raw.values())
    assert b"plaintext-secret" not in serialized
    loaded = board.load_checkpoint("task-secret")
    _assert_no_credential_fields(loaded)
    assert loaded["nested"]["authorization_status"] == "approved"
    assert loaded["nested"]["token_usage"] == 42
    assert loaded["nested"]["items"][0]["business_secret_name"] == "keep-this-business-value"
    assert state == original


def test_load_checkpoint_discards_credentials_from_legacy_data():
    board = _blackboard()
    key = board.checkpoint_key("legacy-task")
    board._redis.hset(
        key,
        mapping={
            "api_key": '"legacy-root-secret"',
            "phase": '"writing"',
            "config": (
                '{"llm_api_key":"legacy-nested-secret",'
                '"authorization":"Bearer legacy-secret",'
                '"authorization_status":"approved"}'
            ),
        },
    )

    loaded = board.load_checkpoint("legacy-task")

    _assert_no_credential_fields(loaded)
    assert loaded == {
        "phase": "writing",
        "config": {"authorization_status": "approved"},
    }


class _StopAfterFirstCheckpoint(Exception):
    pass


class _RecordingBlackboard:
    def __init__(self, checkpoint=None):
        self.checkpoint = checkpoint
        self.saved = []
        self.values = {}

    def get(self, task_id, key):
        return self.values.get((task_id, key))

    def set(self, task_id, key, value):
        self.values[(task_id, key)] = value

    def load_checkpoint(self, task_id):
        return deepcopy(self.checkpoint)

    def save_checkpoint(self, task_id, state):
        self.saved.append((task_id, deepcopy(state)))

    def xadd_event(self, task_id, event):
        return None


def _run_until_first_phase(monkeypatch, board, **kwargs):
    monkeypatch.setattr(coordinator, "Blackboard", lambda: board)
    monkeypatch.setattr(coordinator, "preflight_embedding_backend", lambda: (True, "ok"))
    monkeypatch.setattr(
        coordinator,
        "_phase_characters",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(_StopAfterFirstCheckpoint()),
    )
    coordinator.writing_task.apply(
        kwargs=kwargs,
        task_id="checkpoint-contract-task",
        throw=False,
    )


def test_new_task_coordinator_state_never_contains_api_key(monkeypatch):
    board = _RecordingBlackboard()

    _run_until_first_phase(
        monkeypatch,
        board,
        topic="secret hygiene",
        api_key="current-request-secret",
    )

    assert board.saved
    for _, state in board.saved:
        _assert_no_credential_fields(state)
        assert "current-request-secret" not in repr(state)


def test_resume_uses_only_explicit_request_key_and_does_not_rewrite_legacy_key(monkeypatch):
    board = _RecordingBlackboard(
        checkpoint={
            "task_id": "old-task",
            "phase": "characters",
            "api_key": "legacy-checkpoint-secret",
            "nested": {"llm_api_key": "legacy-nested-secret", "story": "keep"},
        }
    )
    configured_keys = []
    monkeypatch.setattr(coordinator, "set_api_key", configured_keys.append)

    _run_until_first_phase(
        monkeypatch,
        board,
        resume=True,
        resume_from_task_id="old-task",
        api_key="current-request-secret",
    )

    assert configured_keys
    assert set(configured_keys) == {"current-request-secret"}
    assert board.saved
    for _, state in board.saved:
        _assert_no_credential_fields(state)
        assert "legacy-checkpoint-secret" not in repr(state)
        assert "legacy-nested-secret" not in repr(state)
    assert board.saved[-1][1]["nested"]["story"] == "keep"
