from __future__ import annotations

import sqlite3

from app.task_store import TaskStore
from app.task_store_migrations import CANONICAL_REFS_MIGRATION


def test_legacy_task_store_is_versioned_and_gains_canonical_refs(tmp_path):
    path = tmp_path / "legacy-tasks.db"
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE task_history (task_id TEXT PRIMARY KEY, draft_preview TEXT DEFAULT '')"
    )
    connection.execute(
        "INSERT INTO task_history(task_id, draft_preview) VALUES ('old', 'legacy')"
    )
    connection.commit()
    connection.close()

    store = TaskStore(str(path))
    columns = {
        row[1] for row in store._conn.execute("PRAGMA table_info(task_history)")
    }
    versions = {
        row[0]
        for row in store._conn.execute(
            "SELECT version FROM task_store_schema_migrations"
        )
    }

    assert {"document_id", "current_revision_id", "last_commit_id"} <= columns
    assert CANONICAL_REFS_MIGRATION in versions
    assert store.get("old")["draft_preview"] == "legacy"
    store.close()


def test_task_store_keeps_preview_but_persists_canonical_status_refs(tmp_path):
    store = TaskStore(str(tmp_path / "tasks.db"))
    store.save(
        "task-1",
        {
            "draft": "正文" * 2000,
            "document_id": "document-1",
            "current_revision_id": "revision-1",
            "last_commit_id": "commit-1",
            "state_version_id": "state-1",
            "commit_status": "committed",
            "critical_projection_status": "ready",
            "non_blocking_projection_status": "lagging",
        },
    )

    saved = store.get("task-1")
    assert len(saved["draft_preview"]) == 2000
    assert saved["document_id"] == "document-1"
    assert saved["current_revision_id"] == "revision-1"
    assert saved["last_commit_id"] == "commit-1"
    assert saved["state_version_id"] == "state-1"
    assert saved["critical_projection_status"] == "ready"
    store.close()


def test_recreated_database_at_same_path_reapplies_versioned_migrations(tmp_path):
    path = tmp_path / "recreated-tasks.db"
    first = TaskStore(str(path))
    first.close()
    path.unlink()

    second = TaskStore(str(path))
    versions = {
        row[0]
        for row in second._conn.execute(
            "SELECT version FROM task_store_schema_migrations"
        )
    }

    assert CANONICAL_REFS_MIGRATION in versions
    second.close()


def test_context_managed_stores_do_not_accumulate_live_connections(tmp_path):
    for index in range(20):
        with TaskStore(str(tmp_path / f"tasks-{index}.db")) as store:
            store.save(f"task-{index}", {})

    assert len(TaskStore._INSTANCES) == 0
