"""伏笔管理存储 —— SQLite 持久化。"""

import sqlite3
import json
import uuid
import os
from pathlib import Path
from .config import settings


FORESHADOWING_DB_PATH = os.path.join(
    os.path.dirname(settings.TASK_DB_PATH),
    "foreshadowings.db"
)


def _get_conn() -> sqlite3.Connection:
    Path(FORESHADOWING_DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(FORESHADOWING_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS foreshadowings (
            id TEXT PRIMARY KEY,
            task_id TEXT DEFAULT '',
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            plant_chapter INTEGER DEFAULT 0,
            resolve_chapter INTEGER DEFAULT NULL,
            status TEXT DEFAULT 'pending',
            related_characters TEXT DEFAULT '[]',
            related_items TEXT DEFAULT '[]',
            importance INTEGER DEFAULT 5,
            tags TEXT DEFAULT '[]',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_foreshadowings_task ON foreshadowings(task_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_foreshadowings_status ON foreshadowings(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_foreshadowings_plant ON foreshadowings(plant_chapter)")
    conn.commit()
    return conn


def _row_to_dict(row) -> dict:
    d = dict(row)
    for field in ("related_characters", "related_items", "tags"):
        raw = d.get(field, "[]")
        if isinstance(raw, str):
            try:
                d[field] = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                d[field] = []
    return d


def _serialize_lists(data: dict) -> dict:
    d = dict(data)
    for field in ("related_characters", "related_items", "tags"):
        if field in d and isinstance(d[field], list):
            d[field] = json.dumps(d[field], ensure_ascii=False)
    return d


# ── CRUD ─────────────────────────────────────────────────────────

def list_foreshadowings(task_id: str = "") -> list[dict]:
    conn = _get_conn()
    try:
        if task_id:
            rows = conn.execute(
                "SELECT * FROM foreshadowings WHERE task_id = ? ORDER BY plant_chapter ASC",
                (task_id,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM foreshadowings ORDER BY plant_chapter ASC").fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def get_foreshadowing(fs_id: str) -> dict | None:
    conn = _get_conn()
    try:
        row = conn.execute("SELECT * FROM foreshadowings WHERE id = ?", (fs_id,)).fetchone()
        return _row_to_dict(row) if row else None
    finally:
        conn.close()


def create_foreshadowing(data: dict) -> dict:
    conn = _get_conn()
    try:
        fs_id = data.get("id") or str(uuid.uuid4())
        d = _serialize_lists(data)
        conn.execute("""
            INSERT OR REPLACE INTO foreshadowings
            (id, task_id, name, description, plant_chapter, resolve_chapter, status,
             related_characters, related_items, importance, tags)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            fs_id,
            d.get("task_id", ""),
            d.get("name", ""),
            d.get("description", ""),
            d.get("plant_chapter", 0),
            d.get("resolve_chapter"),
            d.get("status", "pending"),
            d.get("related_characters", "[]"),
            d.get("related_items", "[]"),
            d.get("importance", 5),
            d.get("tags", "[]"),
        ))
        conn.commit()
        return get_foreshadowing(fs_id)
    finally:
        conn.close()


def update_foreshadowing(fs_id: str, updates: dict) -> dict | None:
    conn = _get_conn()
    try:
        existing = get_foreshadowing(fs_id)
        if not existing:
            return None
        merged = _serialize_lists({**existing, **updates, "id": fs_id})
        conn.execute("""
            UPDATE foreshadowings SET
                name=?, description=?, plant_chapter=?, resolve_chapter=?, status=?,
                related_characters=?, related_items=?, importance=?, tags=?,
                updated_at=datetime('now')
            WHERE id=?
        """, (
            merged["name"], merged["description"], merged["plant_chapter"],
            merged["resolve_chapter"], merged["status"],
            merged["related_characters"], merged["related_items"],
            merged["importance"], merged["tags"], fs_id,
        ))
        conn.commit()
        return get_foreshadowing(fs_id)
    finally:
        conn.close()


def delete_foreshadowing(fs_id: str) -> bool:
    conn = _get_conn()
    try:
        conn.execute("DELETE FROM foreshadowings WHERE id = ?", (fs_id,))
        conn.commit()
        return conn.total_changes > 0
    finally:
        conn.close()


def get_active_for_chapter(task_id: str, chapter: int) -> list[dict]:
    """获取某章节相关的所有活跃伏笔。"""
    conn = _get_conn()
    try:
        rows = conn.execute("""
            SELECT * FROM foreshadowings
            WHERE task_id = ?
              AND status IN ('pending', 'planted', 'hinted')
              AND (
                plant_chapter = ? OR
                (resolve_chapter IS NOT NULL AND resolve_chapter >= ? AND plant_chapter <= ?)
              )
            ORDER BY importance DESC
        """, (task_id, chapter, chapter, chapter)).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def build_foreshadowing_context(task_id: str, chapter: int) -> str:
    """构建注入 prompt 的伏笔上下文。"""
    active = get_active_for_chapter(task_id, chapter)
    if not active:
        return ""

    plant_lines = []
    resolve_lines = []
    active_lines = []
    anchor_lines = []

    for f in active:
        tags = f.get("tags", [])
        is_anchor = "world_anchor" in tags or f.get("resolve_chapter") == 999

        if is_anchor:
            # 世界锚点：不可变的世界设定，硬约束
            anchor_lines.append(f"- 「{f['name']}」—— {f['description']}")
        elif f["plant_chapter"] == chapter and f["status"] == "pending":
            plant_lines.append(f"- 需要在当前章节埋下伏笔：「{f['name']}」—— {f['description']}")
        elif f.get("resolve_chapter") == chapter:
            resolve_lines.append(f"- 需要在当前章节回收伏笔：「{f['name']}」(importance={f['importance']})")
        else:
            active_lines.append(f"- 活跃伏笔：「{f['name']}」({f['status']}) —— {f['description']}")

    parts = []
    if anchor_lines:
        parts.append("## 已确立的世界设定（对照检查，大纲安排的变动优先）\n" + "\n".join(anchor_lines))
    if plant_lines:
        parts.append("## 待埋设伏笔\n" + "\n".join(plant_lines))
    if resolve_lines:
        parts.append("## 待回收伏笔\n" + "\n".join(resolve_lines))
    if active_lines:
        parts.append("## 当前活跃伏笔\n" + "\n".join(active_lines))

    return "\n\n".join(parts) if parts else ""


def ensure_world_anchors(task_id: str, world_setting_text: str, characters: list[dict] | None = None) -> int:
    """确保世界锚点已写入伏笔表。返回新创建的锚点数。

    Args:
        task_id: 项目/任务 ID
        world_setting_text: 世界观设定文本
        characters: 角色列表 [{name, background, world_position, ...}]
    """
    if not world_setting_text.strip():
        return 0

    existing = list_foreshadowings(task_id=task_id)
    existing_names = {f["name"] for f in existing}

    # 提取世界锚点
    anchors = _extract_world_anchors(world_setting_text, characters or [])
    created = 0
    for a in anchors:
        if a["name"] not in existing_names:
            try:
                create_foreshadowing({
                    "task_id": task_id,
                    "name": a["name"],
                    "description": a["description"],
                    "plant_chapter": 0,
                    "resolve_chapter": 999,
                    "status": "planted",
                    "importance": 10,
                    "tags": ["world_anchor"],
                })
                existing_names.add(a["name"])
                created += 1
            except Exception:
                pass
    return created


def _extract_world_anchors(world_setting_text: str, characters: list[dict]) -> list[dict]:
    """从世界观设定中提取关键专有名词（世界锚点）。

    Returns: [{"name": "青云宗", "description": "玄黄大陆第一仙门"}, ...]
    """
    anchors = []
    seen_names = set()

    # 角色名作为基础锚点
    for c in characters:
        name = c.get("name", "").strip()
        if name and name not in seen_names:
            seen_names.add(name)
            desc = (c.get("world_position", "") or c.get("role", "") or
                    f"主角: {c.get('background', '')[:60]}" if c.get("background") else f"角色: {name}")
            anchors.append({"name": name, "description": desc})

    # LLM 提取世界观专有名词
    try:
        from .utils.llm_client import get_llm_client
        from .utils.json_parser import parse_json
        llm = get_llm_client()
        prompt = f"""从以下世界观设定中提取所有**关键专有名词**（大陆名、宗门名、地名、重要机构名、力量体系名等）。
包括：大陆名、宗门名、地名、重要机构名、力量体系名等。
不要提取角色名。

世界观设定：
{world_setting_text[:2000]}

输出 JSON 数组：
[{{"name": "专有名词", "description": "一句话说明（包含其世界观定位）"}}]"""

        resp = llm.chat_completion(
            [{"role": "system", "content": "你是文学设定分析助手。提取世界观专有名词。输出JSON数组。"},
             {"role": "user", "content": prompt}],
            temperature=0.2, max_tokens=400, json_mode=True,
        )
        result = parse_json(resp)
        if isinstance(result, list):
            for item in result:
                name = item.get("name", "").strip()
                if name and name not in seen_names:
                    seen_names.add(name)
                    anchors.append({
                        "name": name,
                        "description": item.get("description", f"世界观设定: {name}"),
                    })
    except Exception:
        pass

    return anchors
