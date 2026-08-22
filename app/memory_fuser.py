"""Deprecated MemoryFuser prototype.

No production caller exists as of the 2026-07-17 state audit.  The file is
retained temporarily because it contains uncommitted user changes; new code
must use explicit state owners and the future Context Broker contract instead.
"""

import logging

from .narrative_event import rank_and_fill, format_events_for_prompt, NarrativeEvent

logger = logging.getLogger(__name__)


class MemoryFuser:
    """Deprecated: do not add new callers."""

    def __init__(self, event_graph=None, vector_store=None, context_manager=None):
        self._event_graph = event_graph
        self._vector_store = vector_store
        self._context_manager = context_manager

    def get_context(self, task_id: str, section_num: int, sub_num: int,
                    topic: str = "", key_points: list[str] | None = None,
                    max_tokens: int = 8000) -> str:
        """返回合并后的上下文字符串，可直接注入 Writer prompt。"""
        parts = []

        # 1. 弧线事件（本节必须体现）
        if self._event_graph:
            events = self._event_graph.query_relevant(section_num, sub_num)
            if events:
                parts.append("## 本节关键事件（按重要性排序）\n" + format_events_for_prompt(events))

        # 2. RAG 检索
        if self._vector_store:
            query = f"{topic} {' '.join(key_points or [])}"
            try:
                chunks = self._vector_store.search(query, k=3, task_id=task_id)
                if chunks:
                    rag_text = "\n".join(f"- {c[:200]}" for c in chunks[:3])
                    parts.append(f"## 相关历史段落\n{rag_text}")
            except Exception:
                logger.warning(f"[{task_id[:8]}] RAG 检索失败，跳过相关段落上下文", exc_info=True)

        # 3. 最近摘要
        if self._context_manager:
            summary = self._context_manager.get_summary()
            if summary and summary != "（故事开头，暂无前文）":
                parts.append(f"## 最近情节\n{summary[:500]}")

        return "\n\n".join(parts) if parts else "（无前文上下文）"
