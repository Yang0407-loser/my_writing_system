from app import coordinator


class RecordingBlackboard:
    def __init__(self):
        self.calls = []

    def set(self, task_id, field, value):
        self.calls.append(("set", task_id, field, value))

    def xadd_event(self, task_id, event):
        self.calls.append(("event", task_id, event))
        return "1-0"


def test_phase_complete_appends_done_before_completed(monkeypatch):
    board = RecordingBlackboard()
    monkeypatch.setattr(coordinator, "_export_draft", lambda *args: "test.md")
    monkeypatch.setattr(coordinator, "_save_task_history", lambda *args, **kwargs: None)

    coordinator._phase_complete(
        board,
        "task-1",
        {"section_texts": {1: "确定性正文"}, "review_result": {"global_score": 8}},
    )

    done_index = next(
        index for index, call in enumerate(board.calls)
        if call[0] == "event" and call[2]["event"] == "done"
    )
    completed_index = next(
        index for index, call in enumerate(board.calls)
        if call[:3] == ("set", "task-1", "status") and call[3] == "completed"
    )
    assert done_index < completed_index
