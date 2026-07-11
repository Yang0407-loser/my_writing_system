"""端点共享辅助函数。从 main.py 提取，供 routers 使用。"""

import os
import re


# ── 草稿处理 ──────────────────────────────────────────────────────

def assemble_draft_from_checkpoint(checkpoint: dict) -> str:
    """从 checkpoint 的 section_texts 拼装完整草稿。"""
    section_texts = checkpoint.get("section_texts", {})
    if section_texts:
        keys = sorted(section_texts.keys(), key=lambda k: int(k) if str(k).isdigit() else k)
        return "\n\n".join(section_texts.get(k, "") for k in keys)
    return checkpoint.get("draft", "")


def parse_sections_from_draft(draft_text: str) -> dict[str, str]:
    """从完整草稿反解出 section_texts。格式: 第N节：标题\\n\\n内容..."""
    sections = {}
    pattern = r'(第(\d+)节[：:][^\n]*\n)'
    parts = re.split(pattern, draft_text)
    i = 1
    while i + 2 <= len(parts):
        header = parts[i]
        sec_num = parts[i + 1]
        content = parts[i + 2] if i + 2 < len(parts) else ""
        sections[sec_num] = (header + content).strip()
        i += 3
    return sections


