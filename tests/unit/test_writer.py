"""测试 Writer Agent 核心逻辑。"""

import pytest
from unittest.mock import MagicMock


class TestHandoverExtraction:
    """_extract_handover 方法测试。"""

    def test_handover_with_valid_json(self, mock_llm):
        """正常返回：LLM 返回合法 JSON dict。"""
        mock_llm('{"foreshadowing": "主角秘密身份", "character_state": "愤怒", '
                 '"open_threads": "追踪线索", "new_facts": []}')

        from app.agents.writer import Writer
        writer = Writer()
        result = writer._extract_handover(
            "主角终于明白了自己的真实身份，他愤怒地握紧拳头。",
            section_num=3, sub_num=1, character_context="主角",
        )
        assert result is not None
        assert result["foreshadowing"] == "主角秘密身份"
        assert result["character_state"] == "愤怒"

    def test_handover_with_invalid_json(self, mock_llm):
        """LLM 返回非法文本时，返回 None 且不抛异常。"""
        mock_llm("这不是 JSON，只是一段描述文字。")

        from app.agents.writer import Writer
        writer = Writer()
        result = writer._extract_handover(
            "测试文本", section_num=1, sub_num=1,
        )
        assert result is None

    def test_handover_with_list_response(self, mock_llm):
        """LLM 返回 JSON 数组时（非 dict），返回 None。"""
        mock_llm('[{"key": "value"}]')

        from app.agents.writer import Writer
        writer = Writer()
        result = writer._extract_handover(
            "测试文本", section_num=1, sub_num=1,
        )
        assert result is None

    def test_handover_empty_text(self, mock_llm):
        """空文本也能正常调用（不抛异常）。"""
        mock_llm('{"foreshadowing": "", "character_state": "", '
                 '"open_threads": "", "new_facts": []}')

        from app.agents.writer import Writer
        writer = Writer()
        result = writer._extract_handover(
            "", section_num=1, sub_num=1,
        )
        assert result is not None
        assert result["foreshadowing"] == ""


class TestBackrefParsing:
    """_parse_backrefs 方法测试。"""

    def test_parse_single_backref(self):
        from app.agents.writer import Writer
        writer = Writer()
        text = "第2节：角色'江辰'此时应该已经知道真相，但前文尚未交代其信息来源。"
        refs = writer._parse_backrefs(text, from_section=3)
        assert len(refs) == 1
        assert refs[0]["target_section"] == 2
        assert refs[0]["severity"] == "minor"

    def test_parse_multiple_backrefs(self):
        from app.agents.writer import Writer
        writer = Writer()
        text = (
            "第1节第2小节：此处伏笔与第三章冲突。"
            "第4节：角色B的动机需要补充。"
        )
        refs = writer._parse_backrefs(text, from_section=5)
        assert len(refs) == 2
        assert refs[0]["target_section"] == 1
        assert refs[0]["target_subsection"] == 2
        assert refs[1]["target_section"] == 4

    def test_parse_empty_text(self):
        from app.agents.writer import Writer
        writer = Writer()
        refs = writer._parse_backrefs("", from_section=1)
        assert refs == []
