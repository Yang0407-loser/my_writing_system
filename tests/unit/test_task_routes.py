from unittest.mock import MagicMock

import app.routers.tasks as task_routes
import app.vector_store as vector_store_module


def test_delete_task_uses_blackboard_stream_lifecycle(monkeypatch):
    board = MagicMock()
    task_store = MagicMock()
    vector_store = MagicMock()
    vector_store.cleanup_task.return_value = 0
    monkeypatch.setattr(task_routes, "bb", board)
    monkeypatch.setattr(task_routes, "TaskStore", lambda: task_store)
    monkeypatch.setattr(vector_store_module, "VectorStore", lambda: vector_store)

    result = task_routes.delete_task("task-123")

    assert result == {"status": "deleted", "task_id": "task-123"}
    board.delete_checkpoint.assert_called_once_with("task-123")
    board.stream_delete.assert_called_once_with("task-123")
    task_store.delete.assert_called_once_with("task-123")
    vector_store.cleanup_task.assert_called_once_with("task-123")
    board._redis.delete.assert_not_called()
