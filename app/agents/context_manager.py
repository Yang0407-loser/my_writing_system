from ..utils.llm_client import get_llm_client


class ContextManager:
    """长文本上下文管理器。

    维护"已写内容"的运行摘要，避免在每次 LLM 调用时传入全部历史文本。
    当累积文本超过阈值时，用 LLM 压缩为摘要（保留关键人物、事件、风格）。
    """

    def __init__(self, llm_client=None):
        self.llm = llm_client or get_llm_client()
        self._buffer: list[str] = []          # 最近几个小节的原始文本
        self._buffer_char_count = 0
        self.running_summary: str = ""        # 压缩后的摘要
        self.compress_threshold = 6000         # 超过此字符数触发压缩
        self.section_drafts: dict[int, str] = {}  # 每节的完整草稿

    def add_subsection(self, text: str, section_num: int) -> None:
        """添加新写完的小节文本，必要时触发摘要压缩。"""
        self._buffer.append(text)
        self._buffer_char_count += len(text)

        # 累积该节的完整草稿
        if section_num not in self.section_drafts:
            self.section_drafts[section_num] = ""
        self.section_drafts[section_num] += text + "\n\n"

        # 超过阈值时压缩
        if self._buffer_char_count > self.compress_threshold:
            self._compress()

    def _compress(self) -> None:
        """用 LLM 将缓冲区压缩为摘要。"""
        if not self._buffer:
            return

        full = "\n\n---\n\n".join(self._buffer)
        messages = [
            {
                "role": "system",
                "content": (
                    "你是一位编辑。请将以下故事内容压缩为简洁摘要（不超过 500 字），"
                    "必须保留：所有人物名称与关系、关键事件与转折、整体风格基调。"
                ),
            },
            {"role": "user", "content": f"待压缩内容：\n{full[:12000]}"},
        ]
        try:
            new_summary = self.llm.chat_completion(messages, temperature=0.3, max_tokens=600)
            if self.running_summary:
                self.running_summary = self.running_summary + "\n\n后续发展：" + new_summary
            else:
                self.running_summary = new_summary
        except Exception:
            # 压缩失败不阻塞写作，保留原始缓冲区
            pass

        self._buffer = []
        self._buffer_char_count = 0

    def get_summary(self) -> str:
        """获取当前运行摘要。"""
        parts = []
        if self.running_summary:
            parts.append("【前文摘要】\n" + self.running_summary)
        if self._buffer:
            parts.append("【最近内容】\n" + "\n\n".join(self._buffer[-3:]))
        return "\n\n".join(parts) if parts else "（故事开头，暂无前文）"

    def finalize(self) -> str:
        """最终压缩，返回完整的故事摘要。"""
        self._compress()
        return self.running_summary

    def serialize(self) -> dict:
        """导出当前状态，用于 checkpoint 持久化。"""
        return {
            "running_summary": self.running_summary,
            "buffer": list(self._buffer),
            "buffer_char_count": self._buffer_char_count,
            "section_drafts": dict(self.section_drafts),
        }

    def deserialize(self, data: dict) -> None:
        """从 checkpoint 恢复状态。"""
        self.running_summary = data.get("running_summary", "")
        self._buffer = list(data.get("buffer", []))
        self._buffer_char_count = data.get("buffer_char_count", 0)
        self.section_drafts = {int(k): v for k, v in data.get("section_drafts", {}).items()}
