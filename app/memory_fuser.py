"""MemoryFuser — 统一记忆入口，收集所有上下文源并按权重合并。"""

from .narrative_event import rank_and_fill, format_events_for_prompt, NarrativeEvent


class MemoryFuser:
    """在 Writer 调用前统一收集所有记忆源，按优先级合并为压缩上下文。"""

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
                pass

        # 3. 最近摘要
        if self._context_manager:
            summary = self._context_manager.get_summary()
            if summary and summary != "（故事开头，暂无前文）":
                parts.append(f"## 最近情节\n{summary[:500]}")

        return "\n\n".join(parts) if parts else "（无前文上下文）"
