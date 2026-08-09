"""Versioned, additive migrations for the legacy SQLite TaskStore."""

from __future__ import annotations

import sqlite3


LEGACY_COLUMNS_MIGRATION = "0001-task-history-legacy-columns"
CANONICAL_REFS_MIGRATION = "0002-task-history-canonical-refs"

LEGACY_COLUMNS = {
    "topic": "TEXT DEFAULT ''",
    "word_count": "INTEGER DEFAULT 0",
    "section_count": "INTEGER DEFAULT 0",
    "status": "TEXT DEFAULT 'completed'",
    "mode": "TEXT DEFAULT 'celery'",
    "style_json": "TEXT DEFAULT '{}'",
    "outline_json": "TEXT DEFAULT '[]'",
    "handover_json": "TEXT DEFAULT '[]'",
    "characters_json": "TEXT DEFAULT '[]'",
    "review_json": "TEXT DEFAULT '{}'",
    "world_setting": "TEXT DEFAULT ''",
    "story_synopsis": "TEXT DEFAULT ''",
    "target_words": "INTEGER DEFAULT 0",
    "world_state_json": "TEXT DEFAULT '{}'",
    "events_json": "TEXT DEFAULT '[]'",
    "analysis_json": "TEXT DEFAULT '{}'",
    "draft_preview": "TEXT DEFAULT ''",
    "output_file": "TEXT DEFAULT ''",
    "created_at": "TEXT DEFAULT ''",
    "updated_at": "TEXT DEFAULT ''",
}

CANONICAL_COLUMNS = {
    "document_id": "TEXT DEFAULT ''",
    "current_revision_id": "TEXT DEFAULT ''",
    "last_commit_id": "TEXT DEFAULT ''",
    "state_version_id": "TEXT DEFAULT ''",
    "commit_status": "TEXT DEFAULT ''",
    "critical_projection_status": "TEXT DEFAULT ''",
    "non_blocking_projection_status": "TEXT DEFAULT ''",
}


def _add_missing_columns(
    connection: sqlite3.Connection, columns: dict[str, str]
) -> None:
    existing = {
        row[1] for row in connection.execute("PRAGMA table_info(task_history)")
    }
    for name, declaration in columns.items():
        if name not in existing:
            connection.execute(
                f'ALTER TABLE task_history ADD COLUMN "{name}" {declaration}'
            )


def apply_task_store_migrations(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS task_store_schema_migrations (
            version TEXT PRIMARY KEY,
            applied_at TEXT DEFAULT (datetime('now'))
        )
        """
    )
    applied = {
        row[0]
        for row in connection.execute(
            "SELECT version FROM task_store_schema_migrations"
        )
    }
    for version, columns in (
        (LEGACY_COLUMNS_MIGRATION, LEGACY_COLUMNS),
        (CANONICAL_REFS_MIGRATION, CANONICAL_COLUMNS),
    ):
        if version in applied:
            continue
        _add_missing_columns(connection, columns)
        connection.execute(
            "INSERT INTO task_store_schema_migrations(version) VALUES (?)",
            (version,),
        )
    connection.commit()
