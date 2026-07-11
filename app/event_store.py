"""统一事件存储 —— 合并 NarrativeEvent + ExperienceTimeline。

单一 events 表承载: arc_milestone, plot_thread, world_fact,
major_event, item_gain, item_loss, relationship_change, decision, power_up, death。
"""

import json
import os
import uuid
from pathlib import Path

from .config import settings

DB_PATH = os.path.join(os.path.dirname(settings.TASK_DB_PATH), "events.db")


def _get_conn():
    import sqlite3
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id TEXT PRIMARY KEY,
            task_id TEXT DEFAULT '',
            type TEXT DEFAULT 'plot_thread',
            description TEXT DEFAULT '',
            chapter INTEGER DEFAULT 0,
            subsection INTEGER DEFAULT 0,
            character_id TEXT DEFAULT '',
            related_characters TEXT DEFAULT '[]',
            related_items TEXT DEFAULT '[]',
            related_locations TEXT DEFAULT '[]',
            related_factions TEXT DEFAULT '[]',
            importance INTEGER DEFAULT 5,
            status TEXT DEFAULT 'active',
            emotional_impact TEXT DEFAULT '',
            consequences TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_evt_task ON events(task_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_evt_chapter ON events(chapter)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_evt_type ON events(type)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_evt_importance ON events(importance DESC)")
    conn.commit()
    return conn


# ═══ CRUD ═══

def add_event(task_id: str, event_type: str, description: str,
              chapter: int = 0, subsection: int = 0,
              character_id: str = "", importance: int = 5,
              related_characters: list[str] | None = None,
              related_items: list[str] | None = None,
              related_locations: list[str] | None = None,
              related_factions: list[str] | None = None,
              emotional_impact: str = "", consequences: str = "",
              status: str = "active") -> dict:
    conn = _get_conn()
    try:
        eid = str(uuid.uuid4())
        conn.execute("""
            INSERT INTO events (id, task_id, type, description, chapter, subsection,
                character_id, related_characters, related_items, related_locations,
                related_factions, importance, status, emotional_impact, consequences)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (eid, task_id, event_type, description, chapter, subsection,
              character_id, json.dumps(related_characters or [], ensure_ascii=False),
              json.dumps(related_items or [], ensure_ascii=False),
              json.dumps(related_locations or [], ensure_ascii=False),
              json.dumps(related_factions or [], ensure_ascii=False),
              importance, status, emotional_impact, consequences))
        conn.commit()
        return _row_to_dict(conn.execute("SELECT * FROM events WHERE id=?", (eid,)).fetchone())
    finally:
        conn.close()


def get_events(task_id: str = "", event_type: str = "",
               chapter: int = 0, limit: int = 50) -> list[dict]:
    conn = _get_conn()
    try:
        where = []
        params = []
        if task_id:
            where.append("task_id = ?"); params.append(task_id)
        if event_type:
            where.append("type = ?"); params.append(event_type)
        if chapter:
            where.append("chapter = ?"); params.append(chapter)
        clause = ("WHERE " + " AND ".join(where)) if where else ""
        rows = conn.execute(
            f"SELECT * FROM events {clause} ORDER BY chapter, importance DESC LIMIT ?",
            params + [limit]
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def get_events_for_chapter(task_id: str, chapter: int, limit: int = 20) -> list[dict]:
    return get_events(task_id=task_id, chapter=chapter, limit=limit)


def update_event_status(eid: str, status: str) -> bool:
    conn = _get_conn()
    try:
        conn.execute("UPDATE events SET status=? WHERE id=?", (status, eid))
        conn.commit()
        return conn.total_changes > 0
    finally:
        conn.close()


def count_events(task_id: str) -> int:
    conn = _get_conn()
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM events WHERE task_id=?", (task_id,)
        ).fetchone()[0]
    finally:
        conn.close()


# ═══ Helpers ═══

def _row_to_dict(row) -> dict:
    d = dict(row)
    for f in ("related_characters", "related_items", "related_locations", "related_factions"):
        try:
            d[f] = json.loads(d.get(f, "[]"))
        except (json.JSONDecodeError, TypeError):
            d[f] = []
    return d


# ═══ 兼容 experience_timeline 旧接口 ═══

def get_recent_events(task_id: str, limit: int = 30) -> list[dict]:
    """按章节倒序获取最近事件（兼容 experience_timeline 调用）。"""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM events WHERE task_id=? ORDER BY chapter DESC, importance DESC LIMIT ?",
            (task_id, limit)
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()
