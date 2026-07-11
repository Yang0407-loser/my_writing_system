"""项目级数据存储 —— SQLite 持久化，所有创作数据的根容器。"""

import sqlite3
import json
import uuid
import os
from pathlib import Path
from .config import settings

DB_PATH = os.path.join(os.path.dirname(settings.TASK_DB_PATH), "projects.db")


def _get_conn() -> sqlite3.Connection:
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS projects (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL DEFAULT '未命名项目',
            world_setting TEXT DEFAULT '',
            story_synopsis TEXT DEFAULT '',
            outline_json TEXT DEFAULT '[]',
            last_draft TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS project_outline_nodes (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            parent_id TEXT DEFAULT '',
            title TEXT DEFAULT '',
            description TEXT DEFAULT '',
            key_points TEXT DEFAULT '[]',
            target_words INTEGER DEFAULT 2000,
            locked INTEGER DEFAULT 0,
            injections TEXT DEFAULT '{}',
            status TEXT DEFAULT 'draft',
            sort_order INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_oln_project ON project_outline_nodes(project_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_oln_parent ON project_outline_nodes(parent_id)")
    # 迁移：为旧数据库添加缺失的列
    cols = {r[1] for r in conn.execute("PRAGMA table_info(project_outline_nodes)").fetchall()}
    if 'injections' not in cols:
        conn.execute("ALTER TABLE project_outline_nodes ADD COLUMN injections TEXT DEFAULT '{}'")
    if 'status' not in cols:
        conn.execute("ALTER TABLE project_outline_nodes ADD COLUMN status TEXT DEFAULT 'draft'")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS project_outline_versions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT NOT NULL,
            nodes_json TEXT DEFAULT '[]',
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_olv_project ON project_outline_versions(project_id)")
    # 迁移：添加 last_draft 字段
    cols = {r[1] for r in conn.execute("PRAGMA table_info(projects)").fetchall()}
    if "last_draft" not in cols:
        conn.execute("ALTER TABLE projects ADD COLUMN last_draft TEXT DEFAULT ''")
    conn.commit()
    return conn


def create_project(name: str = "未命名项目") -> dict:
    conn = _get_conn()
    try:
        pid = str(uuid.uuid4())
        conn.execute("INSERT INTO projects (id, name) VALUES (?, ?)", (pid, name))
        conn.commit()
        return get_project(pid)
    finally:
        conn.close()


def get_project(pid: str) -> dict | None:
    conn = _get_conn()
    try:
        row = conn.execute("SELECT * FROM projects WHERE id = ?", (pid,)).fetchone()
        if not row:
            return None
        d = dict(row)
        try:
            d["outline_json"] = json.loads(d.get("outline_json", "[]"))
        except (json.JSONDecodeError, TypeError):
            d["outline_json"] = []
        return d
    finally:
        conn.close()


def list_projects() -> list[dict]:
    conn = _get_conn()
    try:
        rows = conn.execute("SELECT * FROM projects ORDER BY updated_at DESC").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def update_project(pid: str, updates: dict) -> dict | None:
    conn = _get_conn()
    try:
        existing = get_project(pid)
        if not existing:
            return None
        merged = {**existing, **updates, "id": pid}
        if isinstance(merged.get("outline_json"), list):
            merged["outline_json"] = json.dumps(merged["outline_json"], ensure_ascii=False)
        conn.execute("""
            UPDATE projects SET name=?, world_setting=?, story_synopsis=?,
            outline_json=?, updated_at=datetime('now') WHERE id=?
        """, (merged["name"], merged.get("world_setting", ""),
              merged.get("story_synopsis", ""), merged.get("outline_json", "[]"), pid))
        conn.commit()
        return get_project(pid)
    finally:
        conn.close()


def delete_project(pid: str) -> bool:
    conn = _get_conn()
    try:
        conn.execute("DELETE FROM project_outline_nodes WHERE project_id = ?", (pid,))
        conn.execute("DELETE FROM projects WHERE id = ?", (pid,))
        conn.commit()
        return conn.total_changes > 0
    finally:
        conn.close()


def save_world_setting(pid: str, text: str) -> dict | None:
    return update_project(pid, {"world_setting": text})


def save_outline_nodes(pid: str, nodes: list[dict]) -> list[dict]:
    conn = _get_conn()
    try:
        # 先清除旧节点，防止累积孤儿行导致重复
        conn.execute("DELETE FROM project_outline_nodes WHERE project_id = ?", (pid,))
        saved = []
        for i, node in enumerate(nodes):
            nid = node.get("id") or str(uuid.uuid4())
            kp = json.dumps(node.get("key_points", []), ensure_ascii=False)
            injections_json = json.dumps(node.get("injections", {}), ensure_ascii=False)
            conn.execute("""
                INSERT OR REPLACE INTO project_outline_nodes
                (id, project_id, parent_id, title, description, key_points, target_words, locked, injections, status, sort_order)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (nid, pid, node.get("parent_id", ""), node.get("title", ""),
                  node.get("description", ""), kp, node.get("target_words", 2000),
                  1 if node.get("locked") else 0, injections_json,
                  node.get("status", "draft"), node.get("sort_order", i)))
            saved.append(nid)
        conn.commit()

        tree = _build_tree(conn, pid)
        conn.execute("UPDATE projects SET outline_json=?, updated_at=datetime('now') WHERE id=?",
                     (json.dumps(tree, ensure_ascii=False), pid))
        conn.commit()

        conn.execute(
            "INSERT INTO project_outline_versions (project_id, nodes_json) VALUES (?, ?)",
            (pid, json.dumps(tree, ensure_ascii=False)))
        conn.execute("""
            DELETE FROM project_outline_versions WHERE id NOT IN (
                SELECT id FROM project_outline_versions WHERE project_id = ?
                ORDER BY created_at DESC LIMIT 5
            ) AND project_id = ?
        """, (pid, pid))
        conn.commit()

        return get_outline_nodes(pid)
    finally:
        conn.close()


def get_outline_nodes(pid: str) -> list[dict]:
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM project_outline_nodes WHERE project_id = ? ORDER BY sort_order", (pid,)
        ).fetchall()
        return [_row_to_node(r) for r in rows]
    finally:
        conn.close()


def get_outline_tree(pid: str) -> list[dict]:
    conn = _get_conn()
    try:
        return _build_tree(conn, pid)
    finally:
        conn.close()


def get_outline_versions(pid: str) -> list[dict]:
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT id, created_at, nodes_json FROM project_outline_versions WHERE project_id=? ORDER BY created_at DESC",
            (pid,)
        ).fetchall()
        return [{"id": r["id"], "created_at": r["created_at"], "node_count": len(json.loads(r["nodes_json"]))} for r in rows]
    finally:
        conn.close()


def restore_outline_version(pid: str, version_id: int) -> list[dict]:
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT nodes_json FROM project_outline_versions WHERE id=? AND project_id=?",
            (version_id, pid)
        ).fetchone()
        if not row:
            return []
        tree = json.loads(row["nodes_json"])
        flat = []
        def flatten(ns, parent_id=""):
            for i, n in enumerate(ns):
                nid = n.get("id") or str(uuid.uuid4())
                flat.append({"id": nid, "parent_id": parent_id, "title": n.get("title",""),
                    "description": n.get("description",""), "key_points": n.get("key_points",[]),
                    "target_words": n.get("target_words",2000), "locked": n.get("locked",False),
                    "status": n.get("status","draft"), "injections": n.get("injections",{}), "sort_order": i})
                if n.get("children"):
                    flatten(n["children"], nid)
        flatten(tree)
        return save_outline_nodes(pid, flat)
    finally:
        conn.close()


def _row_to_node(row) -> dict:
    d = dict(row)
    try:
        d["key_points"] = json.loads(d.get("key_points", "[]"))
    except (json.JSONDecodeError, TypeError):
        d["key_points"] = []
    try:
        d["injections"] = json.loads(d.get("injections", "{}"))
    except (json.JSONDecodeError, TypeError):
        d["injections"] = {}
    d["locked"] = bool(d.get("locked", 0))
    return d


def _build_tree(conn, pid: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM project_outline_nodes WHERE project_id = ? ORDER BY sort_order", (pid,)
    ).fetchall()
    nodes = {r["id"]: _row_to_node(r) for r in rows}
    roots = []
    for n in nodes.values():
        p = n.get("parent_id", "")
        if p and p in nodes:
            nodes[p].setdefault("children", []).append(n)
        else:
            roots.append(n)
    return roots


def save_draft(pid: str, draft_text: str) -> bool:
    """保存项目草稿正文。"""
    conn = _get_conn()
    try:
        conn.execute("UPDATE projects SET last_draft = ?, updated_at = datetime('now') WHERE id = ?",
                     (draft_text[:50000], pid))
        conn.commit()
        return conn.total_changes > 0
    finally:
        conn.close()


def get_draft(pid: str) -> str:
    """读取项目草稿正文。"""
    conn = _get_conn()
    try:
        row = conn.execute("SELECT last_draft FROM projects WHERE id = ?", (pid,)).fetchone()
        return row["last_draft"] if row else ""
    finally:
        conn.close()
