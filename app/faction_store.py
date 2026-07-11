"""势力/阵营存储 —— SQLite 持久化。"""

import json
import os
import uuid
from pathlib import Path

from .config import settings

DB_PATH = os.path.join(os.path.dirname(settings.TASK_DB_PATH), "factions.db")

FACTION_TYPES = ["宗门", "世家", "皇朝", "教派", "散修联盟", "邪道", "商盟", "其他"]
RELATION_TYPES = ["同盟", "中立", "敌对", "附庸", "宗主", "世仇"]


def _get_conn():
    import sqlite3
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS factions (
            id TEXT PRIMARY KEY,
            task_id TEXT DEFAULT '',
            name TEXT NOT NULL,
            type TEXT DEFAULT '宗门',
            leader_name TEXT DEFAULT '',
            description TEXT DEFAULT '',
            goal TEXT DEFAULT '',
            strength INTEGER DEFAULT 5,
            territory TEXT DEFAULT '',
            is_active INTEGER DEFAULT 1,
            genre TEXT DEFAULT '',
            tags TEXT DEFAULT '[]',
            source TEXT DEFAULT 'user',
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS faction_members (
            id TEXT PRIMARY KEY,
            faction_id TEXT NOT NULL,
            character_name TEXT NOT NULL,
            role TEXT DEFAULT '弟子',
            joined_chapter INTEGER DEFAULT 0,
            left_chapter INTEGER DEFAULT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS faction_relations (
            id TEXT PRIMARY KEY,
            task_id TEXT DEFAULT '',
            faction_a TEXT NOT NULL,
            faction_b TEXT NOT NULL,
            relation TEXT DEFAULT '中立',
            description TEXT DEFAULT '',
            established_chapter INTEGER DEFAULT 0
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_fac_task ON factions(task_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_fac_type ON factions(type)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_fmem_faction ON faction_members(faction_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_frel_task ON faction_relations(task_id)")
    conn.commit()
    return conn


# ═══ Faction CRUD ═══

def create_faction(task_id: str, name: str, **kwargs) -> dict:
    conn = _get_conn()
    try:
        fid = str(uuid.uuid4())
        conn.execute("""
            INSERT INTO factions (id, task_id, name, type, leader_name, description,
                goal, strength, territory, is_active, genre, tags, source)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (fid, task_id, name,
              kwargs.get("type", "宗门"), kwargs.get("leader_name", ""),
              kwargs.get("description", ""), kwargs.get("goal", ""),
              kwargs.get("strength", 5), kwargs.get("territory", ""),
              1 if kwargs.get("is_active", True) else 0,
              kwargs.get("genre", ""), json.dumps(kwargs.get("tags", []), ensure_ascii=False),
              kwargs.get("source", "user")))
        conn.commit()
        return get_faction(fid)
    finally:
        conn.close()


def get_faction(fid: str) -> dict | None:
    conn = _get_conn()
    try:
        row = conn.execute("SELECT * FROM factions WHERE id=?", (fid,)).fetchone()
        return _row_to_dict(row) if row else None
    finally:
        conn.close()


def list_factions(task_id: str = "", ftype: str = "") -> list[dict]:
    conn = _get_conn()
    try:
        where = []; params = []
        if task_id: where.append("task_id=?"); params.append(task_id)
        if ftype: where.append("type=?"); params.append(ftype)
        clause = ("WHERE " + " AND ".join(where)) if where else ""
        rows = conn.execute(
            f"SELECT * FROM factions {clause} ORDER BY strength DESC", params
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def update_faction(fid: str, updates: dict) -> dict | None:
    conn = _get_conn()
    try:
        existing = get_faction(fid)
        if not existing: return None
        merged = {**existing, **updates, "id": fid}
        if isinstance(merged.get("tags"), list):
            merged["tags"] = json.dumps(merged["tags"], ensure_ascii=False)
        conn.execute("""
            UPDATE factions SET name=?, type=?, leader_name=?, description=?,
                goal=?, strength=?, territory=?, is_active=?, genre=?, tags=?, source=?
            WHERE id=?
        """, (merged["name"], merged["type"], merged.get("leader_name",""),
              merged.get("description",""), merged.get("goal",""),
              merged.get("strength",5), merged.get("territory",""),
              merged.get("is_active",1), merged.get("genre",""),
              merged.get("tags","[]"), merged.get("source","user"), fid))
        conn.commit()
        return get_faction(fid)
    finally:
        conn.close()


def delete_faction(fid: str) -> bool:
    conn = _get_conn()
    try:
        conn.execute("DELETE FROM faction_members WHERE faction_id=?", (fid,))
        conn.execute("DELETE FROM factions WHERE id=?", (fid,))
        conn.commit()
        return conn.total_changes > 0
    finally:
        conn.close()


# ═══ Members ═══

def add_member(faction_id: str, character_name: str, role: str = "弟子",
               joined_chapter: int = 0) -> dict:
    conn = _get_conn()
    try:
        mid = str(uuid.uuid4())
        conn.execute("""
            INSERT INTO faction_members (id, faction_id, character_name, role, joined_chapter)
            VALUES (?,?,?,?,?)
        """, (mid, faction_id, character_name, role, joined_chapter))
        conn.commit()
        return dict(conn.execute("SELECT * FROM faction_members WHERE id=?", (mid,)).fetchone())
    finally:
        conn.close()


def remove_member(faction_id: str, character_name: str) -> bool:
    conn = _get_conn()
    try:
        conn.execute(
            "DELETE FROM faction_members WHERE faction_id=? AND character_name=?",
            (faction_id, character_name))
        conn.commit()
        return conn.total_changes > 0
    finally:
        conn.close()


def get_members(faction_id: str) -> list[dict]:
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM faction_members WHERE faction_id=?", (faction_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ═══ Relations ═══

def set_relation(task_id: str, faction_a: str, faction_b: str,
                 relation: str = "中立", description: str = "",
                 established_chapter: int = 0) -> dict:
    conn = _get_conn()
    try:
        rid = str(uuid.uuid4())
        conn.execute("""
            INSERT INTO faction_relations (id, task_id, faction_a, faction_b,
                relation, description, established_chapter)
            VALUES (?,?,?,?,?,?,?)
        """, (rid, task_id, faction_a, faction_b, relation, description, established_chapter))
        conn.commit()
        return dict(conn.execute("SELECT * FROM faction_relations WHERE id=?", (rid,)).fetchone())
    finally:
        conn.close()


def get_relations(task_id: str = "", faction_name: str = "") -> list[dict]:
    conn = _get_conn()
    try:
        where = []; params = []
        if task_id: where.append("task_id=?"); params.append(task_id)
        if faction_name: where.append("(faction_a=? OR faction_b=?)"); params.extend([faction_name]*2)
        clause = ("WHERE " + " AND ".join(where)) if where else ""
        rows = conn.execute(
            f"SELECT * FROM faction_relations {clause}", params
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def delete_relation(rid: str) -> bool:
    conn = _get_conn()
    try:
        conn.execute("DELETE FROM faction_relations WHERE id=?", (rid,))
        conn.commit()
        return conn.total_changes > 0
    finally:
        conn.close()


# ═══ Helpers ═══

def _row_to_dict(row) -> dict:
    d = dict(row)
    try:
        d["tags"] = json.loads(d.get("tags", "[]"))
    except (json.JSONDecodeError, TypeError):
        d["tags"] = []
    d["is_active"] = bool(d.get("is_active", 1))
    return d


def build_faction_context(task_id: str, chapter: int = 0) -> str:
    """构建 Writer prompt 注入的势力上下文（CONTEXTUAL 级）。"""
    factions = list_factions(task_id)
    if not factions:
        return ""
    lines = []
    for f in factions:
        members = get_members(f["id"])
        member_names = [m["character_name"] for m in members] if members else []
        lines.append(
            f"- {f['name']}({f['type']}, 强度{f['strength']}/10): "
            f"{f.get('description','')[:80]}"
        )
        if member_names:
            lines.append(f"  成员: {', '.join(member_names[:5])}")
        if f.get("territory"):
            lines.append(f"  势力范围: {f['territory']}")
    # 关系
    rels = get_relations(task_id)
    if rels:
        for r in rels:
            lines.append(f"关系: {r['faction_a']} ←{r['relation']}→ {r['faction_b']}")
    return "## 势力格局\n" + "\n".join(lines) if lines else ""
