"""物品背包系统 —— 物品追踪：获取→转移→消耗全链路。"""

import json
import uuid
import os
from pathlib import Path
from .config import settings

DB_PATH = os.path.join(os.path.dirname(settings.TASK_DB_PATH), "items.db")


def _get_conn():
    import sqlite3
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS items (
            id TEXT PRIMARY KEY,
            task_id TEXT DEFAULT '',
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            type TEXT DEFAULT 'material',
            rarity TEXT DEFAULT 'common',
            abilities TEXT DEFAULT '[]',
            origin_chapter INTEGER DEFAULT 0,
            current_owner TEXT DEFAULT '',
            status TEXT DEFAULT 'active',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS item_transactions (
            id TEXT PRIMARY KEY,
            item_id TEXT NOT NULL,
            from_owner TEXT DEFAULT '',
            to_owner TEXT DEFAULT '',
            chapter INTEGER DEFAULT 0,
            description TEXT DEFAULT '',
            timestamp TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_items_task ON items(task_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_items_owner ON items(current_owner)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_trans_item ON item_transactions(item_id)")
    conn.commit()
    return conn


def _row_to_dict(row) -> dict:
    d = dict(row)
    if "abilities" in d and isinstance(d["abilities"], str):
        try:
            d["abilities"] = json.loads(d["abilities"])
        except (json.JSONDecodeError, TypeError):
            d["abilities"] = []
    return d


def create_item(data: dict) -> dict:
    conn = _get_conn()
    try:
        iid = data.get("id") or str(uuid.uuid4())
        abilities = json.dumps(data.get("abilities", []), ensure_ascii=False)
        conn.execute("""
            INSERT OR REPLACE INTO items
            (id, task_id, name, description, type, rarity, abilities, origin_chapter, current_owner, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (iid, data.get("task_id", ""), data.get("name", ""), data.get("description", ""),
              data.get("type", "material"), data.get("rarity", "common"), abilities,
              data.get("origin_chapter", 0), data.get("current_owner", ""), data.get("status", "active")))
        conn.commit()
        return get_item(iid)
    finally:
        conn.close()


def get_item(iid: str) -> dict | None:
    conn = _get_conn()
    try:
        row = conn.execute("SELECT * FROM items WHERE id = ?", (iid,)).fetchone()
        return _row_to_dict(row) if row else None
    finally:
        conn.close()


def update_item(iid: str, updates: dict) -> dict | None:
    conn = _get_conn()
    try:
        existing = get_item(iid)
        if not existing:
            return None
        merged = {**existing, **updates, "id": iid}
        abilities = json.dumps(merged.get("abilities", []), ensure_ascii=False)
        conn.execute("""
            UPDATE items SET name=?, description=?, type=?, rarity=?, abilities=?,
            origin_chapter=?, current_owner=?, status=?, updated_at=datetime('now')
            WHERE id=?
        """, (merged["name"], merged["description"], merged["type"], merged["rarity"],
              abilities, merged["origin_chapter"], merged["current_owner"], merged["status"], iid))
        conn.commit()
        return get_item(iid)
    finally:
        conn.close()


def get_character_inventory(character_id: str) -> list[dict]:
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM items WHERE current_owner = ? AND status = 'active'",
            (character_id,)
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def record_transaction(item_id: str, from_owner: str, to_owner: str, chapter: int, description: str = "") -> dict:
    conn = _get_conn()
    try:
        tid = str(uuid.uuid4())
        conn.execute("""
            INSERT INTO item_transactions (id, item_id, from_owner, to_owner, chapter, description)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (tid, item_id, from_owner, to_owner, chapter, description))
        conn.commit()
        # 更新物品持有者
        update_item(item_id, {"current_owner": to_owner})
        return dict(conn.execute("SELECT * FROM item_transactions WHERE id = ?", (tid,)).fetchone())
    finally:
        conn.close()


def build_inventory_context(characters: list[dict]) -> str:
    """构建角色背包上下文。"""
    if not characters:
        return ""
    lines = ["## 角色背包"]
    for char in characters:
        cid = char.get("id", "")
        cname = char.get("name", "")
        if not cid:
            continue
        items = get_character_inventory(cid)
        if items:
            item_str = "、".join(f"{it['name']}" + (f"({it['rarity']})" if it.get('rarity') != 'common' else "")
                                 for it in items[:5])
            lines.append(f"- {cname}当前持有：{item_str}")
            if len(items) > 5:
                lines[-1] += f" 等{len(items)}件物品"
    return "\n".join(lines) if len(lines) > 1 else ""


def build_item_context(task_id: str) -> str:
    """构建 Writer prompt 用的物品上下文（按任务聚合）。"""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT name, type, rarity, description, current_owner FROM items WHERE task_id = ? AND status = 'active' ORDER BY rarity DESC LIMIT 30",
            (task_id,)).fetchall()
        if not rows:
            return ""
        lines = ["## 关键物品"]
        for r in rows:
            owner = f"（持有者: {r['current_owner']}）" if r["current_owner"] else ""
            lines.append(f"- {r['name']} [{r['type']}, {r['rarity']}] {owner}")
            if r["description"]:
                lines.append(f"  {r['description'][:80]}")
        return "\n".join(lines) if len(lines) > 1 else ""
    finally:
        conn.close()
