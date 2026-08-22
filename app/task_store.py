import sqlite3
import json
import uuid
from weakref import WeakSet


class TaskStore:
    """SQLite 任务历史存储 —— 持久化已完成任务的元数据。"""

    _INSTANCES = WeakSet()

    def __init__(self, db_path: str | None = None):
        if db_path is None:
            from .config import settings
            db_path = settings.TASK_DB_PATH
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._closed = False
        try:
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._ensure_tables()
            # Migration records live in the database, so every connection may
            # safely apply them. A process-local path cache would incorrectly
            # skip migrations when a test or operator recreates a database at
            # the same path, and would also poison retries after a failure.
            from .task_store_migrations import apply_task_store_migrations

            apply_task_store_migrations(self._conn)
        except Exception:
            self._conn.close()
            self._closed = True
            raise
        self._INSTANCES.add(self)

    def close(self) -> None:
        if not self._closed:
            self._conn.close()
            self._closed = True
            self._INSTANCES.discard(self)

    def __enter__(self) -> "TaskStore":
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            # Interpreter shutdown may already have torn down sqlite internals.
            pass

    @classmethod
    def close_all(cls) -> None:
        for store in list(cls._INSTANCES):
            store.close()

    def _ensure_tables(self):
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS task_history (
                task_id TEXT PRIMARY KEY,
                topic TEXT DEFAULT '',
                word_count INTEGER DEFAULT 0,
                section_count INTEGER DEFAULT 0,
                status TEXT DEFAULT 'completed',
                mode TEXT DEFAULT 'celery',
                style_json TEXT DEFAULT '{}',
                outline_json TEXT DEFAULT '[]',
                handover_json TEXT DEFAULT '[]',
                characters_json TEXT DEFAULT '[]',
                review_json TEXT DEFAULT '{}',
                world_setting TEXT DEFAULT '',
                story_synopsis TEXT DEFAULT '',
                target_words INTEGER DEFAULT 0,

                world_state_json TEXT DEFAULT '{}',
                events_json TEXT DEFAULT '[]',
                analysis_json TEXT DEFAULT '{}',
                draft_preview TEXT DEFAULT '',
                output_file TEXT DEFAULT '',
                document_id TEXT DEFAULT '',
                current_revision_id TEXT DEFAULT '',
                last_commit_id TEXT DEFAULT '',
                state_version_id TEXT DEFAULT '',
                commit_status TEXT DEFAULT '',
                critical_projection_status TEXT DEFAULT '',
                non_blocking_projection_status TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS project_workspaces (
                workspace_task_id TEXT PRIMARY KEY,
                active_task_id TEXT DEFAULT '',
                topic TEXT DEFAULT '',
                world_setting TEXT DEFAULT '',
                story_synopsis TEXT DEFAULT '',
                reference_text TEXT DEFAULT '',
                style_json TEXT DEFAULT '{}',
                target_words INTEGER DEFAULT 3000,
                outline_json TEXT DEFAULT '[]',
                draft_backup TEXT DEFAULT '',
                exports_json TEXT DEFAULT '[]',
                status TEXT DEFAULT 'draft',
                archived INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            )
        """)
        self._conn.commit()

    def save(self, task_id: str, data: dict) -> None:
        """保存或更新任务记录。"""
        cols = [
            "task_id", "topic", "word_count", "section_count", "status", "mode",
            "style_json", "outline_json", "handover_json", "characters_json",
            "review_json", "world_setting", "story_synopsis", "target_words",
            "world_state_json", "events_json", "analysis_json", "draft_preview", "output_file",
            "document_id", "current_revision_id", "last_commit_id", "state_version_id",
            "commit_status", "critical_projection_status", "non_blocking_projection_status",
        ]
        draft_text = data.get("draft", "") or ""
        values = {
            "task_id": task_id,
            "topic": data.get("topic", ""),
            "word_count": data.get("word_count", 0),
            "section_count": data.get("section_count", 0),
            "status": data.get("status", "completed"),
            "mode": data.get("mode", "celery"),
            "style_json": json.dumps(data.get("style", {}), ensure_ascii=False),
            "outline_json": json.dumps(data.get("outline", []), ensure_ascii=False),
            "handover_json": json.dumps(data.get("handover_notes", []), ensure_ascii=False),
            "characters_json": json.dumps(data.get("characters", []), ensure_ascii=False),
            "review_json": json.dumps(data.get("review", {}), ensure_ascii=False),
            "world_setting": data.get("world_setting", ""),
            "story_synopsis": data.get("story_synopsis", ""),
            "target_words": data.get("target_words", 0),

            "world_state_json": json.dumps(data.get("world_state", {}), ensure_ascii=False),
            "events_json": json.dumps(data.get("events", []), ensure_ascii=False),
            "analysis_json": json.dumps(data.get("analysis", {}), ensure_ascii=False),
            "draft_preview": draft_text[:2000],
            "output_file": data.get("output_file", ""),
            "document_id": data.get("document_id", ""),
            "current_revision_id": data.get("current_revision_id", ""),
            "last_commit_id": data.get("last_commit_id", ""),
            "state_version_id": data.get("state_version_id", ""),
            "commit_status": data.get("commit_status", ""),
            "critical_projection_status": data.get("critical_projection_status", ""),
            "non_blocking_projection_status": data.get(
                "non_blocking_projection_status", ""
            ),
        }

        existing = self._conn.execute(
            "SELECT task_id FROM task_history WHERE task_id = ?", (task_id,)
        ).fetchone()

        if existing:
            set_clause = ", ".join(f"{c} = ?" for c in cols)
            self._conn.execute(
                f"UPDATE task_history SET {set_clause}, updated_at = datetime('now') WHERE task_id = ?",
                [values[c] for c in cols] + [task_id],
            )
        else:
            placeholders = ", ".join("?" for _ in cols)
            self._conn.execute(
                f"INSERT INTO task_history ({', '.join(cols)}) VALUES ({placeholders})",
                [values[c] for c in cols],
            )
        self._conn.commit()

    def get(self, task_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM task_history WHERE task_id = ?", (task_id,)
        ).fetchone()
        if not row:
            return None
        return self._row_to_dict(row)

    def list_all(self, limit: int = 50) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM task_history ORDER BY updated_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def save_workspace(self, workspace_task_id: str, data: dict) -> None:
        existing = self.get_workspace(workspace_task_id)
        merged = {**(existing or {}), **data}
        values = (
            workspace_task_id,
            merged.get("active_task_id", workspace_task_id),
            merged.get("topic", ""),
            merged.get("world_setting", ""),
            merged.get("story_synopsis", ""),
            merged.get("reference_text", ""),
            json.dumps(merged.get("style_profile", {}), ensure_ascii=False),
            merged.get("target_words_per_section", 3000),
            json.dumps(merged.get("outline", []), ensure_ascii=False),
            merged.get("draft_backup", ""),
            json.dumps(merged.get("exports", []), ensure_ascii=False),
            merged.get("status", "draft"),
            int(bool(merged.get("archived", False))),
        )
        self._conn.execute(
            """
            INSERT INTO project_workspaces (
                workspace_task_id, active_task_id, topic, world_setting,
                story_synopsis, reference_text, style_json, target_words,
                outline_json, draft_backup, exports_json, status, archived
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(workspace_task_id) DO UPDATE SET
                active_task_id=excluded.active_task_id,
                topic=excluded.topic,
                world_setting=excluded.world_setting,
                story_synopsis=excluded.story_synopsis,
                reference_text=excluded.reference_text,
                style_json=excluded.style_json,
                target_words=excluded.target_words,
                outline_json=excluded.outline_json,
                draft_backup=excluded.draft_backup,
                exports_json=excluded.exports_json,
                status=excluded.status,
                archived=excluded.archived,
                updated_at=datetime('now')
            """,
            values,
        )
        self._conn.commit()

    def get_workspace(self, workspace_task_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM project_workspaces WHERE workspace_task_id = ?",
            (workspace_task_id,),
        ).fetchone()
        if not row:
            return None
        data = dict(row)
        try:
            data["style_profile"] = json.loads(data.pop("style_json") or "{}")
        except (json.JSONDecodeError, TypeError):
            data["style_profile"] = {}
        data["target_words_per_section"] = data.pop("target_words", 3000)
        try:
            data["outline"] = json.loads(data.pop("outline_json") or "[]")
        except (json.JSONDecodeError, TypeError):
            data["outline"] = []
        try:
            data["exports"] = json.loads(data.pop("exports_json") or "[]")
        except (json.JSONDecodeError, TypeError):
            data["exports"] = []
        data["archived"] = bool(data.get("archived"))
        return data

    def list_workspaces(self, limit: int = 100, include_archived: bool = False) -> list[dict]:
        where = "" if include_archived else "WHERE archived = 0"
        rows = self._conn.execute(
            f"SELECT workspace_task_id FROM project_workspaces {where} "
            "ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [self.get_workspace(row[0]) for row in rows]

    def find_workspace_for_task(self, task_id: str) -> dict | None:
        row = self._conn.execute(
            """
            SELECT workspace_task_id FROM project_workspaces
            WHERE workspace_task_id = ? OR active_task_id = ?
            ORDER BY updated_at DESC LIMIT 1
            """,
            (task_id, task_id),
        ).fetchone()
        return self.get_workspace(row[0]) if row else None

    def add_draft_version(
        self,
        workspace_task_id: str,
        *,
        active_task_id: str,
        section: int,
        subsection: int,
        content: str,
        source: str,
        instruction: str = "",
        parent_version_id: str | None = None,
        version_id: str | None = None,
    ) -> dict:
        version_id = version_id or str(uuid.uuid4())
        self._conn.execute(
            """
            INSERT INTO draft_versions (
                version_id, workspace_task_id, active_task_id, section,
                subsection, content, source, instruction, parent_version_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                version_id,
                workspace_task_id,
                active_task_id,
                int(section),
                int(subsection),
                content,
                source,
                instruction,
                parent_version_id,
            ),
        )
        self._conn.commit()
        return self.get_draft_version(version_id)

    def get_draft_version(self, version_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM draft_versions WHERE version_id = ?",
            (version_id,),
        ).fetchone()
        return dict(row) if row else None

    def list_draft_versions(
        self,
        workspace_task_id: str,
        *,
        section: int | None = None,
        subsection: int | None = None,
        limit: int = 50,
    ) -> list[dict]:
        clauses = ["workspace_task_id = ?"]
        values: list[object] = [workspace_task_id]
        if section is not None:
            clauses.append("section = ?")
            values.append(int(section))
        if subsection is not None:
            clauses.append("subsection = ?")
            values.append(int(subsection))
        values.append(int(limit))
        rows = self._conn.execute(
            "SELECT * FROM draft_versions WHERE "
            + " AND ".join(clauses)
            + " ORDER BY created_at DESC, rowid DESC LIMIT ?",
            values,
        ).fetchall()
        return [dict(row) for row in rows]

    def delete(self, task_id: str) -> bool:
        cur = self._conn.execute("DELETE FROM task_history WHERE task_id = ?", (task_id,))
        self._conn.commit()
        return cur.rowcount > 0

    def _row_to_dict(self, row: sqlite3.Row) -> dict:
        d = dict(row)
        for field in ["style_json", "outline_json", "handover_json", "characters_json", "review_json", "world_state_json", "events_json", "analysis_json"]:
            if field in d and isinstance(d[field], str):
                try:
                    d[field] = json.loads(d[field])
                except (json.JSONDecodeError, TypeError):
                    pass
        return d
