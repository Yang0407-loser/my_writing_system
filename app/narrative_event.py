"""统一叙事事件模型 — 合并弧线里程碑、伏笔线索、世界事实为单一事件类型。

事件在生命周期中变换 type:
    open plot_thread → resolved → world_fact (自动)
"""

import json
import uuid
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("writing_system.events")


# ══════════════════════════════════════════════════════════════════
# 数据模型
# ══════════════════════════════════════════════════════════════════

@dataclass
class NarrativeEvent:
    """统一叙事事件。type 区分用途，结构完全统一。"""
    event_id: str
    type: str                  # "arc_milestone" | "plot_thread" | "world_fact"
    description: str           # 自然语言描述
    section: int
    subsection: int = 0
    character_id: str = ""     # 关联角色
    status: str = "pending"    # arc: pending|done|deviated
                               # thread: open|resolved|abandoned
                               # fact: established
    weight: int = 5            # 1-10，静态叙事重要性
    span: str = "medium"       # short|medium|long
    urgency: str = "low"       # low|medium|high，动态紧迫度
    related_events: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)

    # ── 序列化 ──

    def to_dict(self) -> dict:
        return {
            "event_id": self.event_id,
            "type": self.type,
            "description": self.description,
            "section": self.section,
            "subsection": self.subsection,
            "character_id": self.character_id,
            "status": self.status,
            "weight": self.weight,
            "span": self.span,
            "urgency": self.urgency,
            "related_events": self.related_events,
            "tags": self.tags,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "NarrativeEvent":
        return cls(
            event_id=d.get("event_id", ""),
            type=d.get("type", "plot_thread"),
            description=d.get("description", ""),
            section=d.get("section", 0),
            subsection=d.get("subsection", 0),
            character_id=d.get("character_id", ""),
            status=d.get("status", "pending"),
            weight=d.get("weight", 5),
            span=d.get("span", "medium"),
            urgency=d.get("urgency", "low"),
            related_events=d.get("related_events", []),
            tags=d.get("tags", []),
        )


# ══════════════════════════════════════════════════════════════════
# 权重映射
# ══════════════════════════════════════════════════════════════════

PRIORITY_WEIGHT = {"critical": 9, "major": 5, "minor": 2}
SPAN_EXPIRY_OFFSET = {"short": 5, "medium": 10, "long": None}  # None = 永不过期


# ══════════════════════════════════════════════════════════════════
# rank_and_fill — 替代三层预算
# ══════════════════════════════════════════════════════════════════

def _estimate_tokens(text: str) -> int:
    """粗略估算中文字符数（1 中文字 ≈ 1.5 tokens）。"""
    import re
    cn = len(re.findall(r'[一-鿿]', text))
    en = len(text) - cn
    return int(cn * 1.5 + en * 0.3)


def rank_and_fill(items: list[tuple[str, int]], max_tokens: int) -> str:
    """按权重降序排列文本描述，合并为 prompt 段落。超 token 上限则截断。

    Args:
        items: [(description, weight), ...]
        max_tokens: 总 token 上限

    Returns:
        格式化的 prompt 字符串
    """
    scored = sorted(items, key=lambda x: x[1], reverse=True)

    lines, tokens = [], 0
    for i, (desc, weight) in enumerate(scored, 1):
        et = _estimate_tokens(desc)
        if tokens + et > max_tokens:
            break
        lines.append(f"{i}. [weight={weight}] {desc}")
        tokens += et
    return "\n".join(lines) if lines else "（无特殊事件）"


def format_events_for_prompt(events: list[NarrativeEvent]) -> str:
    """将事件列表格式化为 prompt 段落（向后兼容）。"""
    if not events:
        return "（无特殊事件）"
    items = [(e.description, e.weight) for e in events]
    return rank_and_fill(items, 8000)


# ══════════════════════════════════════════════════════════════════
# EventGraph — 事件图谱
# ══════════════════════════════════════════════════════════════════

class EventGraph:
    """弧线事件追踪器（v3.1 精简版）。

    只追踪 arc_milestone 类型事件。伏笔和世界事实的追踪已移到交接笔记链和 WorldState。
    事件存储在 Redis 黑板中，key 为 {task_id}:events。
    """

    def __init__(self, blackboard, task_id: str):
        self._bb = blackboard
        self._task_id = task_id
        self._events: dict[str, NarrativeEvent] = {}
        self._load()

    # ── 弧线 CRUD ──

    def add_arc_milestone(self, description: str, section: int, subsection: int = 0,
                          character_id: str = "", weight: int = 5) -> str:
        """添加一个弧线里程碑事件。"""
        event = NarrativeEvent(
            event_id=str(uuid.uuid4()), type="arc_milestone",
            description=description,
            section=section, subsection=subsection,
            character_id=character_id, status="pending",
            weight=weight,
        )
        self._events[event.event_id] = event
        self._save()
        return event.event_id

    def update_arc_status(self, character_id: str, status: str) -> int:
        """更新指定角色的所有 pending 弧线状态。status: done|deviated。
        Returns: 更新数量。
        """
        count = 0
        for e in self._events.values():
            if e.type == "arc_milestone" and e.character_id == character_id and e.status == "pending":
                e.status = status
                count += 1
        if count:
            self._save()
        return count

    def update_arc_by_section(self, section: int, status: str) -> int:
        """更新指定节的所有 pending 弧线状态。"""
        count = 0
        for e in self._events.values():
            if e.type == "arc_milestone" and e.section == section and e.status == "pending":
                e.status = status
                count += 1
        if count:
            self._save()
        return count

    # ── 查询 ──

    def get_arc_events(self, section: int, subsection: int = 0) -> list[NarrativeEvent]:
        """返回当前小节相关的弧线事件。"""
        result = []
        for e in self._events.values():
            if e.type == "arc_milestone" and e.section == section:
                if subsection == 0 or e.subsection == subsection:
                    result.append(e)
        return result

    def get_summary(self) -> dict:
        """返回弧线进度摘要。"""
        arc_events = [e for e in self._events.values() if e.type == "arc_milestone"]
        return {
            "arc_milestones_total": len(arc_events),
            "arc_milestones_done": len([e for e in arc_events if e.status == "done"]),
            "arc_milestones_deviated": len([e for e in arc_events if e.status == "deviated"]),
        }

    # ── 兼容旧接口 ──

    def add_event(self, event: NarrativeEvent) -> str:
        """保留用于 coordinator 的向后兼容。只接受 arc_milestone。"""
        if event.type != "arc_milestone":
            return ""
        return self.add_arc_milestone(event.description, event.section, event.subsection,
                                       event.character_id, event.weight)

    def query_relevant(self, section: int, subsection: int = 0) -> list[NarrativeEvent]:
        """返回当前小节的弧线事件（向后兼容）。"""
        return self.get_arc_events(section, subsection)

    # ── 持久化 ──

    def _save(self):
        if self._bb:
            data = [e.to_dict() for e in self._events.values()]
            self._bb.set(self._task_id, "event_graph", data)

    def _load(self):
        if not self._bb:
            return
        raw = self._bb.get(self._task_id, "event_graph")
        if not raw:
            return
        try:
            if isinstance(raw, str):
                raw = json.loads(raw)
            for d in (raw if isinstance(raw, list) else []):
                e = NarrativeEvent.from_dict(d)
                if e.type == "arc_milestone":  # v3.1: 只恢复弧线事件
                    self._events[e.event_id] = e
        except (json.JSONDecodeError, TypeError, KeyError):
            logger.warning("EventGraph 恢复失败，从空开始")
