"""支线故事管理系统 —— 七要素模型 + 章节绑定 + 热点图。"""

import json
import uuid
import os
from pathlib import Path
from .config import settings

DB_PATH = os.path.join(os.path.dirname(settings.TASK_DB_PATH), "subplots.db")


def _get_conn():
    import sqlite3
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS subplots (
            id TEXT PRIMARY KEY,
            task_id TEXT DEFAULT '',
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            type TEXT DEFAULT 'character_arc',
            protagonist_id TEXT DEFAULT '',
            antagonist_id TEXT DEFAULT '',
            volume_start INTEGER DEFAULT 1,
            volume_end INTEGER DEFAULT 5,
            elements TEXT DEFAULT '[]',
            status TEXT DEFAULT 'planned',
            priority INTEGER DEFAULT 5,
            related_subplots TEXT DEFAULT '[]',
            pov TEXT DEFAULT 'protagonist',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)
    _migrate(conn)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_subplots_task ON subplots(task_id)")
    conn.commit()
    return conn


def _migrate(conn):
    """增量迁移：为已有数据库添加新列。"""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(subplots)").fetchall()}
    if "pov" not in cols:
        conn.execute("ALTER TABLE subplots ADD COLUMN pov TEXT DEFAULT 'protagonist'")


ELEMENT_TYPES = ["desire", "obstacle", "action", "result", "surprise", "twist", "ending"]
ELEMENT_LABELS = {
    "desire": "欲望", "obstacle": "阻碍", "action": "行动",
    "result": "结果", "surprise": "意外", "twist": "转折", "ending": "结局",
}


def _row_to_dict(row) -> dict:
    d = dict(row)
    for f in ("elements", "related_subplots"):
        if f in d and isinstance(d[f], str):
            try:
                d[f] = json.loads(d[f])
            except (json.JSONDecodeError, TypeError):
                d[f] = []
    return d


def create_subplot(data: dict) -> dict:
    conn = _get_conn()
    try:
        sid = data.get("id") or str(uuid.uuid4())
        elements = json.dumps(data.get("elements", []), ensure_ascii=False)
        related = json.dumps(data.get("related_subplots", []), ensure_ascii=False)
        conn.execute("""
            INSERT OR REPLACE INTO subplots
            (id, task_id, name, description, type, protagonist_id, antagonist_id,
             volume_start, volume_end, elements, status, priority, related_subplots, pov)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (sid, data.get("task_id", ""), data.get("name", ""), data.get("description", ""),
              data.get("type", "character_arc"), data.get("protagonist_id", ""),
              data.get("antagonist_id", ""), data.get("volume_start", 1),
              data.get("volume_end", 5), elements, data.get("status", "planned"),
              data.get("priority", 5), related, data.get("pov", "protagonist")))
        conn.commit()
        return get_subplot(sid)
    finally:
        conn.close()


def get_subplot(sid: str) -> dict | None:
    conn = _get_conn()
    try:
        row = conn.execute("SELECT * FROM subplots WHERE id = ?", (sid,)).fetchone()
        return _row_to_dict(row) if row else None
    finally:
        conn.close()


def list_subplots(task_id: str = "") -> list[dict]:
    conn = _get_conn()
    try:
        if task_id:
            rows = conn.execute("SELECT * FROM subplots WHERE task_id = ? ORDER BY priority DESC", (task_id,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM subplots ORDER BY priority DESC").fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def update_subplot(sid: str, updates: dict) -> dict | None:
    conn = _get_conn()
    try:
        existing = get_subplot(sid)
        if not existing:
            return None
        merged = {**existing, **updates, "id": sid}
        elements = json.dumps(merged.get("elements", []), ensure_ascii=False)
        related = json.dumps(merged.get("related_subplots", []), ensure_ascii=False)
        conn.execute("""
            UPDATE subplots SET name=?, description=?, type=?, protagonist_id=?, antagonist_id=?,
            volume_start=?, volume_end=?, elements=?, status=?, priority=?, related_subplots=?,
            pov=?, updated_at=datetime('now')
            WHERE id=?
        """, (merged["name"], merged["description"], merged["type"],
              merged["protagonist_id"], merged["antagonist_id"],
              merged["volume_start"], merged["volume_end"], elements,
              merged["status"], merged["priority"], related,
              merged.get("pov", "protagonist"), sid))
        conn.commit()
        return get_subplot(sid)
    finally:
        conn.close()


def delete_subplot(sid: str) -> bool:
    conn = _get_conn()
    try:
        conn.execute("DELETE FROM subplots WHERE id = ?", (sid,))
        conn.commit()
        return conn.total_changes > 0
    finally:
        conn.close()


def calculate_heat_map(subplots: list[dict], total_chapters: int = 50) -> dict[int, int]:
    """计算章节热点图：每章绑定了多少支线要素。"""
    heat = {ch: 0 for ch in range(1, total_chapters + 1)}
    for sp in subplots:
        for elem in sp.get("elements", []):
            for ch in elem.get("chapter_binding", []):
                if ch in heat:
                    heat[ch] += 1
    return heat


def get_heat_level(count: int) -> str:
    """热点级别。"""
    if count <= 1:
        return "cool"    # 正常
    elif count <= 2:
        return "warm"    # 适中
    elif count <= 4:
        return "hot"     # 偏多, 黄色警告
    else:
        return "critical"  # 太多, 红色警告


def auto_bind_subplot_elements(task_id: str, total_chapters: int = 50) -> list[dict]:
    """一键关联：将未绑定章节的支线要素自动分配到合适位置。"""
    subplots = list_subplots(task_id)
    updated = []
    for sp in subplots:
        modified = False
        elements = sp.get("elements", [])
        for i, elem in enumerate(elements):
            if not elem.get("chapter_binding"):
                # 简单分配: 按要素类型均匀分布
                elem_type = elem.get("element_type", "")
                type_idx = ELEMENT_TYPES.index(elem_type) if elem_type in ELEMENT_TYPES else 0
                chapter = max(1, min(total_chapters,
                    int(total_chapters * (type_idx + 0.5) / len(ELEMENT_TYPES))))
                elem["chapter_binding"] = [chapter]
                modified = True
        if modified:
            updated.append(update_subplot(sp["id"], {"elements": elements}))
    return [u for u in updated if u]


def build_subplot_context(task_id: str, section_num: int = 1,
                          chapter_num: int = 1) -> str:
    """构建注入 Writer prompt 的支线上下文。

    过滤条件：
    - 支线 status 为 planned 或 active（非 completed）
    - 至少有一个要素的 chapter_binding 包含当前章节号

    返回格式化后的支线上下文文本，无相关支线时返回空字符串。
    """
    subplots = list_subplots(task_id)
    if not subplots:
        return ""

    lines = []
    for sp in subplots:
        if sp.get("status") == "completed":
            continue
        elements = sp.get("elements", [])
        # 筛选绑定到当前章节的要素
        relevant = []
        prev_elem = None
        next_elem = None
        for i, elem in enumerate(elements):
            binding = elem.get("chapter_binding", [])
            if chapter_num in binding:
                relevant.append(elem)
                # 找上一个已完成的要素
                for j in range(i - 1, -1, -1):
                    if elements[j].get("chapter_binding"):
                        prev_elem = elements[j]
                        break
                # 找下一个待触发的要素
                for j in range(i + 1, len(elements)):
                    if elements[j].get("chapter_binding"):
                        next_elem = elements[j]
                        break
        if not relevant:
            continue

        pov = sp.get("pov", "protagonist")
        subplot_status = sp.get("status", "planned")
        label_map = {"planned": "待启动", "active": "进行中", "completed": "已完成"}
        status_label = label_map.get(subplot_status, subplot_status)

        for elem in relevant:
            elem_type = elem.get("element_type", "")
            elem_label = ELEMENT_LABELS.get(elem_type, elem_type)
            elem_name = elem.get("name", "")
            elem_desc = elem.get("description", "")

            lines.append(f"【{status_label}·{sp['name']}】(优先级 {sp.get('priority', 5)})")
            lines.append(f"  - 当前要素：{elem_label}" +
                        (f" {elem_name}" if elem_name else "") +
                        (f" — {elem_desc}" if elem_desc else ""))
            if prev_elem:
                prev_label = ELEMENT_LABELS.get(prev_elem.get("element_type", ""), "")
                lines.append(f"  - 上文：{prev_label} 已完成")
            else:
                lines.append(f"  - 上文：尚无上文（支线起点）")
            if next_elem:
                next_label = ELEMENT_LABELS.get(next_elem.get("element_type", ""), "")
                next_ch = next_elem.get("chapter_binding", [0])
                lines.append(f"  - 下文预告：{next_label} 第{next_ch[0]}章 — 为下文预留线索")
            # 融入指引
            lines.append(f"  融入方式：将本要素融入主线叙事，不单独扩展段落。如主线已在相关方向，用支线要素增加情感层次或信息增量。")

            if pov == "other":
                lines.append(f"  【间幕】本支线非主角视角。请在章节末尾添加 `【支线·{sp['name']}】` 段落。")
            elif pov == "omniscient":
                lines.append(f"  【旁白】本支线为全知视角。请添加简短旁白段落交代。")
            lines.append("")

    if not lines:
        return ""
    return "## 支线要素（第" + str(section_num) + "节）\n\n" + "\n".join(lines)
