import sqlite3
import json
import os
from datetime import datetime


class TaskStore:
    """SQLite 任务历史存储 —— 持久化已完成任务的元数据。"""

    _MIGRATIONS_DONE = set()

    def __init__(self, db_path: str | None = None):
        if db_path is None:
            from .config import settings
            db_path = settings.TASK_DB_PATH
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._ensure_tables()
        # 仅在首次实例化时执行 schema migration（按 db_path 去重）
        if db_path not in self._MIGRATIONS_DONE:
            self._MIGRATIONS_DONE.add(db_path)
            for col, col_type in [
                ("world_setting", "TEXT DEFAULT ''"),
                ("story_synopsis", "TEXT DEFAULT ''"),
                ("target_words", "INTEGER DEFAULT 0"),
                ("world_state_json", "TEXT DEFAULT '{}'"),
                ("events_json", "TEXT DEFAULT '[]'"),
                ("analysis_json", "TEXT DEFAULT '{}'"),
            ]:
                try:
                    self._conn.execute(f"ALTER TABLE task_history ADD COLUMN {col} {col_type}")
                except sqlite3.OperationalError:
                    pass

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
