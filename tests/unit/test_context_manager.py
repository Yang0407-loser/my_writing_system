"""测试 ContextManager。"""

from app.agents.context_manager import ContextManager


class TestContextManager:
    def test_init(self):
        cm = ContextManager()
        assert cm.running_summary == ""
        assert cm.compress_threshold == 6000

    def test_add_subsection(self):
        cm = ContextManager()
        cm.add_subsection("第一段内容。", section_num=1)
        assert len(cm._buffer) == 1
        assert cm.section_drafts[1] == "第一段内容。\n\n"

    def test_multiple_subsections(self):
        cm = ContextManager()
        cm.add_subsection("第一节内容。", section_num=1)
        cm.add_subsection("第二节内容。", section_num=2)
        assert len(cm._buffer) == 2
        assert 1 in cm.section_drafts
        assert 2 in cm.section_drafts

    def test_get_summary_empty(self):
        cm = ContextManager()
        assert "暂无前文" in cm.get_summary()

    def test_get_summary_has_content(self):
        cm = ContextManager()
        cm.add_subsection("测试内容。", section_num=1)
        summary = cm.get_summary()
        assert "最近内容" in summary

    def test_serialize_empty(self):
        cm = ContextManager()
        data = cm.serialize()
        assert data["running_summary"] == ""
        assert data["buffer"] == []

    def test_serialize_with_data(self):
        cm = ContextManager()
        cm.add_subsection("测试。", section_num=1)
        cm.running_summary = "前文摘要"
        data = cm.serialize()
        assert data["running_summary"] == "前文摘要"
        assert len(data["buffer"]) == 1
        assert data["section_drafts"] == {1: "测试。\n\n"}

    def test_deserialize_restores_state(self):
        cm = ContextManager()
        cm.add_subsection("原始数据。", section_num=1)
        cm.running_summary = "旧摘要"
        data = cm.serialize()

        cm2 = ContextManager()
        cm2.deserialize(data)
        assert cm2.running_summary == "旧摘要"
        assert cm2.section_drafts == {1: "原始数据。\n\n"}

    def test_finalize(self, mock_llm):
        mock_client = mock_llm('{"summary": "compressed"}')
        cm = ContextManager(llm_client=mock_client)
        cm.add_subsection("A" * 2000, section_num=1)
        cm.add_subsection("B" * 2000, section_num=2)
        cm.add_subsection("C" * 2000, section_num=3)
        cm.add_subsection("D" * 2000, section_num=4)
        # 超过 6000 字符阈值，触发压缩
        result = cm.finalize()
        assert isinstance(result, str)
