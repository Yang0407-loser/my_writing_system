"""Deprecated compatibility API for long-term experience events.

``event_store`` is the sole persistence owner.  This module keeps the existing
router and Writer imports stable while providing extraction/formatting only.
The legacy ``experience.db`` file is intentionally left untouched as a
recoverable backup, but this code no longer reads or writes it.
"""

import logging

from .utils.llm_client import get_llm_client
from . import event_store as _es


logger = logging.getLogger(__name__)


def _to_legacy(event: dict) -> dict:
    """Expose legacy field names without creating another stored record."""
    result = dict(event)
    result["event_type"] = result.get("type", result.get("event_type", "major_event"))
    return result


def add_event(data: dict) -> dict:
    return _to_legacy(_es.add_event(
        task_id=data.get("task_id", ""),
        event_type=data.get("event_type", "major_event"),
        description=data.get("description", ""),
        chapter=data.get("chapter", 0),
        subsection=data.get("subsection", 0),
        importance=data.get("importance", 5),
        related_characters=list(data.get("related_characters") or []),
        related_items=list(data.get("related_items") or []),
        related_locations=list(data.get("related_locations") or []),
        emotional_impact=data.get("emotional_impact", ""),
        consequences=data.get("consequences", ""),
    ))


def get_event(eid: str) -> dict | None:
    event = _es.get_event(eid)
    return _to_legacy(event) if event else None


def get_relevant_events(task_id: str, chapter: int, top_k: int = 10) -> list[dict]:
    """获取与当前写作相关的重要事件。importance>=7的永久保留，5-6的按recency衰减。"""
    return [_to_legacy(event) for event in _es.get_relevant_events(task_id, chapter, top_k)]


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
        logger.warning(f"经历事件 LLM 提取失败 (第{chapter}章)", exc_info=True)
        return []


def list_events(task_id: str = "") -> list[dict]:
    if not task_id:
        return []
    return [
        _to_legacy(event)
        for event in _es.get_events(task_id=task_id, limit=5000)
    ]
