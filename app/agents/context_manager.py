"""上下文管理器 v0.9.2 — 简化版。

职责：维护最近原文缓冲，供 Writer 接续时保持行文连贯。

v0.9.1: LLM 摘要压缩（每 6000 字一次 LLM 调用 → ~500 字 running_summary）
v0.9.2: 去除 running_summary。交接简报已覆盖跨章节叙事信号（伏笔/状态/线索），
        重复生成摘要既废 token 又与交接简报重叠。改为只保留最近 3 小节原文。
"""


class ContextManager:
    """长文本上下文管理器 — 滑动窗口原文缓冲。"""

    def __init__(self, llm_client=None):
        # llm_client 参数保留以兼容旧调用，v0.9.2 不再使用
        self._buffer: list[str] = []       # 最近几个小节的原始文本
        self._char_count = 0
        self.max_recent = 3                # 保留最近 N 个小节原文
        self.section_drafts: dict[int, str] = {}  # 每节的完整草稿 (供 checkpoint)

    def add_subsection(self, text: str, section_num: int) -> None:
        """添加新写完的小节文本，保持缓冲上限。"""
        self._buffer.append(text)
        self._char_count += len(text)

        # 累积每节完整草稿
        if section_num not in self.section_drafts:
            self.section_drafts[section_num] = ""
        self.section_drafts[section_num] += text + "\n\n"

        # 超出上限：丢弃最旧
        while len(self._buffer) > self.max_recent:
            dropped = self._buffer.pop(0)
            self._char_count -= len(dropped)

    def get_summary(self) -> str:
        """返回最近原文，供 Writer 接续使用。"""
        if not self._buffer:
            return "（故事开头，暂无前文）"
        return "【最近内容】\n" + "\n\n".join(self._buffer)

    def finalize(self) -> str:
        """返回全篇草稿摘要（供导出）。"""
        parts = []
        for k in sorted(self.section_drafts.keys()):
            parts.append(self.section_drafts[k][:200] + "..." if len(self.section_drafts[k]) > 200 else self.section_drafts[k])
        return "\n\n".join(parts) if parts else ""

    def serialize(self) -> dict:
        """导出当前状态，用于 checkpoint 持久化。"""
        return {
            "buffer": list(self._buffer),
            "char_count": self._char_count,
            "section_drafts": dict(self.section_drafts),
        }

    def deserialize(self, data: dict) -> None:
        """从 checkpoint 恢复状态。

        v0.9.1 checkpoint 可能仍包含 ``running_summary`` 和
        ``compress_threshold``。两者在 v0.9.2 已停用，因此恢复时有意忽略；
        原文缓冲始终收敛到当前契约规定的最近 3 小节。
        """
        buffer = list(data.get("buffer", []))
        self._buffer = buffer[-self.max_recent:]
        self._char_count = sum(len(text) for text in self._buffer)
        self.section_drafts = {int(k): v for k, v in data.get("section_drafts", {}).items()}
