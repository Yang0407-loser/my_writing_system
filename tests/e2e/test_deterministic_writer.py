import time

import fakeredis
import pytest

from app.blackboard import Blackboard
from app.config import settings
from app.models import TaskState, TaskStatus
from app.task_store import TaskStore
from tests.e2e.support.deterministic_writer import DeterministicWriter


@pytest.fixture(autouse=True)
def isolated_task_db(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "TASK_DB_PATH", str(tmp_path / "tasks.db"))


def make_board():
    board = Blackboard()
    board._redis = fakeredis.FakeRedis(decode_responses=False)
    return board


def test_submission_does_not_advance_and_public_state_uses_production_models():
    board = make_board()
    writer = DeterministicWriter(board)
    writer.apply_async(
        kwargs={"topic": "雨夜", "reference_text": "参考", "interactive": False},
        task_id="task-1",
    )
    assert board._redis.xlen(board.stream_key("task-1")) == 0
    assert writer.advance("task-1") == "section_start"
    payload = {"task_id": "task-1", **board.get_all("task-1")}
    assert TaskStatus.model_validate(payload).status == "running"


def test_automatic_done_is_a_separate_advance_before_completed():
    board = make_board()
    writer = DeterministicWriter(board)
    writer.apply_async(kwargs={"topic": "雨夜", "reference_text": "参考"}, task_id="task-1")
    labels = [writer.advance("task-1") for _ in range(5)]
    assert labels == ["section_start", "token_1", "token_2", "section_end", "done"]
    events = [event for _, event in board.xread_events("task-1", "0-0")]
    assert [event["event"] for event in events] == [
        "section_start", "token", "token", "section_end", "done"
    ]
    assert board.get("task-1", "status") == "running"
    assert writer.advance("task-1") == "completed"
    assert board.get("task-1", "status") == "completed"


def test_interactive_checkpoint_is_a_production_task_state():
    board = make_board()
    writer = DeterministicWriter(board)
    writer.apply_async(
        kwargs={"topic": "雨夜", "reference_text": "参考", "interactive": True},
        task_id="task-1",
    )
    assert writer.advance("task-1") == "awaiting_outline_approval"
    checkpoint = board.load_checkpoint("task-1")
    assert TaskState.model_validate(checkpoint).status == "awaiting_outline_approval"


def test_completed_run_persists_workspace_draft_and_production_result():
    board = make_board()
    writer = DeterministicWriter(board)
    writer.apply_async(
        kwargs={
            "topic": "雨夜",
            "reference_text": "参考",
            "workspace_task_id": "workspace-1",
        },
        task_id="task-1",
    )

    for _ in range(6):
        writer.advance("task-1")

    with TaskStore() as store:
        workspace = store.get_workspace("workspace-1")
    assert workspace["active_task_id"] == "task-1"
    assert workspace["draft_backup"] == "雨落在旧站台。她终于等到回信。"
    assert workspace["status"] == "completed"
    result = writer.AsyncResult("task-1")
    assert result.ready() and result.successful()
    assert result.result["workspace_task_id"] == "workspace-1"
    assert result.result["review"] == {"global_score": 8}


def test_background_runner_waits_at_interactive_checkpoint_and_shutdown_cleans_up():
    board = make_board()
    writer = DeterministicWriter(board, auto_run=True, step_delay_ms=5)
    writer.apply_async(
        kwargs={"topic": "雨夜", "reference_text": "参考", "interactive": True},
        task_id="original-task",
    )
    try:
        deadline = time.monotonic() + 1
        while board.get("original-task", "status") != "awaiting_outline_approval":
            assert time.monotonic() < deadline
            time.sleep(0.01)
        time.sleep(0.03)
        assert board._redis.xlen(board.stream_key("original-task")) == 0

        replacement = writer.delay(
            topic="雨夜",
            reference_text="参考",
            resume=True,
            workspace_task_id="original-task",
        )
        deadline = time.monotonic() + 1
        while not writer.AsyncResult(replacement.id).ready():
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert board.get(replacement.id, "status") == "completed"
    finally:
        writer.shutdown()
    assert writer._thread is None or not writer._thread.is_alive()
