"""经历事件线 —— 主角记忆库的长期记忆索引。

与 ContextManager 互补：ContextManager 做短期压缩，ExperienceTimeline 做跨卷重要事件索引。

P5a: 存储已迁移到 event_store，本模块保留 LLM 提取逻辑。
"""

import json
import uuid
import os
from pathlib import Path
from .config import settings
from .utils.llm_client import get_llm_client
from . import event_store as _es

DB_PATH = os.path.join(os.path.dirname(settings.TASK_DB_PATH), "experience.db")


def _get_conn():
    import sqlite3
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS experience_events (
            id TEXT PRIMARY KEY,
            task_id TEXT DEFAULT '',
            chapter INTEGER DEFAULT 0,
            event_type TEXT DEFAULT 'major_event',
            description TEXT DEFAULT '',
            importance INTEGER DEFAULT 5,
            related_characters TEXT DEFAULT '[]',
            related_items TEXT DEFAULT '[]',
            related_locations TEXT DEFAULT '[]',
            emotional_impact TEXT DEFAULT '',
            consequences TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_exp_task ON experience_events(task_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_exp_importance ON experience_events(importance DESC)")
    conn.commit()
    return conn


def add_event(data: dict) -> dict:
    conn = _get_conn()
    try:
        eid = data.get("id") or str(uuid.uuid4())
        for f in ("related_characters", "related_items", "related_locations"):
            if f in data and isinstance(data[f], list):
                data[f] = json.dumps(data[f], ensure_ascii=False)
        conn.execute("""
            INSERT OR REPLACE INTO experience_events
            (id, task_id, chapter, event_type, description, importance,
             related_characters, related_items, related_locations, emotional_impact, consequences)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            eid, data.get("task_id", ""), data.get("chapter", 0), data.get("event_type", "major_event"),
            data.get("description", ""), data.get("importance", 5),
            data.get("related_characters", "[]"), data.get("related_items", "[]"),
            data.get("related_locations", "[]"), data.get("emotional_impact", ""),
            data.get("consequences", ""),
        ))
        conn.commit()
        # P5a: 双写到统一 event_store
        try:
            _es.add_event(
                task_id=data.get("task_id", ""),
                event_type=data.get("event_type", "major_event"),
                description=data.get("description", ""),
                chapter=data.get("chapter", 0),
                importance=data.get("importance", 5),
                emotional_impact=data.get("emotional_impact", ""),
                consequences=data.get("consequences", ""),
            )
        except Exception:
            pass
        return get_event(eid)
    finally:
        conn.close()


def get_event(eid: str) -> dict | None:
    conn = _get_conn()
    try:
        row = conn.execute("SELECT * FROM experience_events WHERE id = ?", (eid,)).fetchone()
        if not row:
            return None
        d = dict(row)
        for f in ("related_characters", "related_items", "related_locations"):
            try:
                d[f] = json.loads(d.get(f, "[]"))
            except (json.JSONDecodeError, TypeError):
                d[f] = []
        return d
    finally:
        conn.close()


def get_relevant_events(task_id: str, chapter: int, top_k: int = 10) -> list[dict]:
    """获取与当前写作相关的重要事件。importance>=7的永久保留，5-6的按recency衰减。"""
    conn = _get_conn()
    try:
        rows = conn.execute("""
            SELECT *, (importance * 10 - ABS(chapter - ?)) as relevance
            FROM experience_events
            WHERE task_id = ? AND chapter <= ?
            ORDER BY relevance DESC
            LIMIT ?
        """, (chapter, task_id, chapter, top_k)).fetchall()
        events = []
        for row in rows:
            d = dict(row)
            for f in ("related_characters", "related_items", "related_locations"):
                try:
                    d[f] = json.loads(d.get(f, "[]"))
                except (json.JSONDecodeError, TypeError):
                    d[f] = []
            events.append(d)
        return events
    finally:
        conn.close()


def build_experience_context(task_id: str, chapter: int, max_tokens: int = 1000) -> str:
    """构建注入prompt的经历事件上下文。"""
    events = get_relevant_events(task_id, chapter, top_k=10)
    if not events:
        return ""
    # 按重要性过滤: importance>=7或前5条
    filtered = [e for e in events if e["importance"] >= 7]
    if len(filtered) < 3:
        filtered = events[:5]
    lines = ["## 主角重要经历（长期记忆）"]
    for e in filtered:
        lines.append(f"- 第{e['chapter']}章 [{e['event_type']}] {e['description']} (重要性:{e['importance']})")
    return "\n".join(lines)


def extract_from_section(task_id: str, chapter: int, section_text: str) -> list[dict]:
    """从章节正文中用LLM提取经历事件。"""
    prompt = f"""从以下正文中提取主角的重要经历事件。只提取对后续剧情有影响的事件。

正文：
{section_text[:4000]}

请以 JSON 数组格式输出（不要其他内容）：
[
  {{
    "event_type": "major_event|item_gain|item_loss|relationship_change|decision|power_up|death",
    "description": "事件描述（一句话）",
    "importance": 1-10重要性评分,
    "related_characters": ["相关角色名"],
    "related_items": ["相关物品名"],
    "related_locations": ["相关地点"],
    "emotional_impact": "对主角的情绪影响",
    "consequences": "后续影响"
  }}
]

提取规则：
1. importance>=7: 改变故事走向的重大事件
2. importance 5-6: 重要的角色互动/物品获得/能力提升
3. importance 1-4: 过渡性事件，只提取特别关键的
4. 如果正文中没有值得记录的重要事件，返回空数组 []
5. 每个事件一句话概括即可"""

    try:
        llm = get_llm_client()
        resp = llm.chat_completion(
            [{"role": "system", "content": "你是一位文学分析助手。请提取重要事件。输出JSON数组。"},
             {"role": "user", "content": prompt}],
            temperature=0.2, max_tokens=1000, json_mode=True,
        )
        from .utils.json_parser import parse_json
        result = parse_json(resp)
        if isinstance(result, list):
            saved = []
            for ev in result:
                if isinstance(ev, dict) and ev.get("description"):
                    ev["task_id"] = task_id
                    ev["chapter"] = chapter
                    saved.append(add_event(ev))
            return saved
        return []
    except Exception:
        return []


def list_events(task_id: str = "") -> list[dict]:
    conn = _get_conn()
    try:
        if task_id:
            rows = conn.execute(
                "SELECT * FROM experience_events WHERE task_id = ? ORDER BY chapter ASC",
                (task_id,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM experience_events ORDER BY chapter ASC").fetchall()
        events = []
        for row in rows:
            d = dict(row)
            for f in ("related_characters", "related_items", "related_locations"):
                try:
                    d[f] = json.loads(d.get(f, "[]"))
                except (json.JSONDecodeError, TypeError):
                    d[f] = []
            events.append(d)
        return events
    finally:
        conn.close()
