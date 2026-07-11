"""故事地图管理 —— 无限节点世界地图 + 主角路径追踪。"""

import json
import uuid
import os
from pathlib import Path
from .config import settings

DB_PATH = os.path.join(os.path.dirname(settings.TASK_DB_PATH), "maps.db")


def _get_conn():
    import sqlite3
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS map_nodes (
            id TEXT PRIMARY KEY,
            task_id TEXT DEFAULT '',
            name TEXT NOT NULL,
            type TEXT DEFAULT 'city',
            parent_id TEXT DEFAULT '',
            description TEXT DEFAULT '',
            x REAL DEFAULT 0,
            y REAL DEFAULT 0,
            properties TEXT DEFAULT '{}',
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS map_edges (
            id TEXT PRIMARY KEY,
            task_id TEXT DEFAULT '',
            source_id TEXT NOT NULL,
            target_id TEXT NOT NULL,
            type TEXT DEFAULT 'road',
            name TEXT DEFAULT '',
            travel_time TEXT DEFAULT '',
            distance TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS protagonist_routes (
            id TEXT PRIMARY KEY,
            task_id TEXT DEFAULT '',
            chapter_start INTEGER DEFAULT 1,
            chapter_end INTEGER DEFAULT 100,
            path_nodes TEXT DEFAULT '[]',
            current_node TEXT DEFAULT '',
            visited_nodes TEXT DEFAULT '[]',
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    return conn


NODE_TYPES = [
    ("world", "世界"), ("continent", "大陆"), ("country", "国家"),
    ("region", "区域"), ("city", "城市"), ("district", "城区"),
    ("location", "地点"), ("building", "建筑"), ("room", "房间"),
]

EDGE_TYPES = [
    ("road", "道路"), ("sea_route", "航线"), ("air_route", "空路"),
    ("teleport", "传送阵"), ("secret_passage", "密道"),
]


def create_node(data: dict) -> dict:
    conn = _get_conn()
    try:
        nid = data.get("id") or str(uuid.uuid4())
        props = json.dumps(data.get("properties", {}), ensure_ascii=False)
        conn.execute("""
            INSERT OR REPLACE INTO map_nodes
            (id, task_id, name, type, parent_id, description, x, y, properties)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (nid, data.get("task_id", ""), data.get("name", ""), data.get("type", "city"),
              data.get("parent_id", ""), data.get("description", ""),
              data.get("x", 0), data.get("y", 0), props))
        conn.commit()
        return get_node(nid)
    finally:
        conn.close()


def get_node(nid: str) -> dict | None:
    conn = _get_conn()
    try:
        row = conn.execute("SELECT * FROM map_nodes WHERE id = ?", (nid,)).fetchone()
        if row:
            d = dict(row)
            try:
                d["properties"] = json.loads(d.get("properties", "{}"))
            except (json.JSONDecodeError, TypeError):
                d["properties"] = {}
            return d
        return None
    finally:
        conn.close()


def list_nodes(task_id: str = "", parent_id: str = "") -> list[dict]:
    conn = _get_conn()
    try:
        sql = "SELECT * FROM map_nodes WHERE 1=1"
        params = []
        if task_id:
            sql += " AND task_id = ?"
            params.append(task_id)
        if parent_id:
            sql += " AND parent_id = ?"
            params.append(parent_id)
        else:
            sql += " AND parent_id = ''" if not parent_id else ""
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def create_edge(data: dict) -> dict:
    conn = _get_conn()
    try:
        eid = data.get("id") or str(uuid.uuid4())
        conn.execute("""
            INSERT OR REPLACE INTO map_edges
            (id, task_id, source_id, target_id, type, name, travel_time, distance)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (eid, data.get("task_id", ""), data.get("source_id", ""),
              data.get("target_id", ""), data.get("type", "road"),
              data.get("name", ""), data.get("travel_time", ""), data.get("distance", "")))
        conn.commit()
        return dict(conn.execute("SELECT * FROM map_edges WHERE id = ?", (eid,)).fetchone())
    finally:
        conn.close()


def list_edges(task_id: str = "") -> list[dict]:
    conn = _get_conn()
    try:
        if task_id:
            rows = conn.execute("SELECT * FROM map_edges WHERE task_id = ?", (task_id,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM map_edges").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_full_map(task_id: str) -> dict:
    """获取完整的可渲染地图数据 (节点 + 边)。"""
    nodes = list_nodes(task_id)
    edges = list_edges(task_id)
    return {"nodes": nodes, "edges": edges}


def set_protagonist_route(task_id: str, chapter_start: int, chapter_end: int,
                          path_nodes: list[str] = None) -> dict:
    conn = _get_conn()
    try:
        rid = str(uuid.uuid4())
        path = json.dumps(path_nodes or [], ensure_ascii=False)
        current = path_nodes[0] if path_nodes else ""
        conn.execute("""
            INSERT OR REPLACE INTO protagonist_routes
            (id, task_id, chapter_start, chapter_end, path_nodes, current_node, visited_nodes)
            VALUES (?, ?, ?, ?, ?, ?, '[]')
        """, (rid, task_id, chapter_start, chapter_end, path, current))
        conn.commit()
        return dict(conn.execute("SELECT * FROM protagonist_routes WHERE id = ?", (rid,)).fetchone())
    finally:
        conn.close()


def get_protagonist_route(task_id: str) -> dict | None:
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM protagonist_routes WHERE task_id = ? ORDER BY created_at DESC LIMIT 1",
            (task_id,)
        ).fetchone()
        if row:
            d = dict(row)
            for f in ("path_nodes", "visited_nodes"):
                try:
                    d[f] = json.loads(d.get(f, "[]"))
                except (json.JSONDecodeError, TypeError):
                    d[f] = []
            return d
        return None
    finally:
        conn.close()


def build_location_context(task_id: str) -> str:
    """构建 Writer prompt 用的地点上下文。"""
    nodes = list_nodes(task_id)
    if not nodes:
        return ""
    by_type = {}
    for n in nodes:
        t = n.get("type", "地点")
        by_type.setdefault(t, []).append(n)
    lines = ["## 故事地图"]
    for t in ["区域", "城市", "地点", "秘境", "房间"]:
        items = by_type.pop(t, [])
        if items:
            names = ", ".join(n["name"] for n in items[:8])
            lines.append(f"- [{t}] {names}")
    for t, items in sorted(by_type.items()):
        names = ", ".join(n["name"] for n in items[:8])
        lines.append(f"- [{t}] {names}")
    return "\n".join(lines)
