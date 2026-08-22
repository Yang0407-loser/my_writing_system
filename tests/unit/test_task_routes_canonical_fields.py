from __future__ import annotations

from unittest.mock import MagicMock

import app.routers.tasks as task_routes


def test_status_adds_canonical_refs_without_removing_legacy_fields(monkeypatch):
    board = MagicMock()
    board.get_all.return_value = {
        "status": "awaiting_critical_projection",
        "draft": "legacy preview",
        "document_id": "document-1",
        "current_revision_id": "revision-1",
        "last_commit_id": "commit-1",
        "current_state_version_id": "state-1",
        "critical_projection_status": "failed",
        "non_blocking_projection_status": "pending",
    }
    monkeypatch.setattr(task_routes, "bb", board)

    result = task_routes.get_task_status("task-1")

    assert result.draft == "legacy preview"
    assert result.document_ref == {
        "document_id": "document-1",
        "revision_id": "revision-1",
        "commit_id": "commit-1",
    }
    assert result.commit_status == "committed"
    assert result.state_version_id == "state-1"
    assert result.critical_projection_status == "failed"


def test_completed_result_is_additively_enriched(monkeypatch):
    async_result = MagicMock()
    async_result.ready.return_value = True
    async_result.successful.return_value = True
    async_result.result = {
        "task_id": "task-1",
        "status": "completed",
        "draft": "legacy field remains",
        "document_id": "document-1",
        "current_revision_id": "revision-1",
        "last_commit_id": "commit-1",
        "current_state_version_id": "state-1",
        "critical_projection_status": "ready",
        "non_blocking_projection_status": "lagging",
    }
    monkeypatch.setattr(task_routes.writing_task, "AsyncResult", lambda _id: async_result)

    result = task_routes.get_task_result("task-1")

    assert result["draft"] == "legacy field remains"
    assert result["document_ref"]["document_id"] == "document-1"
    assert result["commit_status"] == "committed"
    assert result["critical_projection_status"] == "ready"
