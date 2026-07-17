"""测试 ContextManager。"""

from app.agents.context_manager import ContextManager


class TestContextManager:
    def test_init(self):
        cm = ContextManager()
        assert cm.max_recent == 3
        assert cm._buffer == []
        assert not hasattr(cm, "running_summary")

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
        assert data["buffer"] == []
        assert data["char_count"] == 0
        assert "running_summary" not in data

    def test_serialize_with_data(self):
        cm = ContextManager()
        cm.add_subsection("测试。", section_num=1)
        data = cm.serialize()
        assert len(data["buffer"]) == 1
        assert data["char_count"] == len("测试。")
        assert data["section_drafts"] == {1: "测试。\n\n"}

    def test_deserialize_restores_state(self):
        cm = ContextManager()
        cm.add_subsection("原始数据。", section_num=1)
        data = cm.serialize()

        cm2 = ContextManager()
        cm2.deserialize(data)
        assert cm2.get_summary() == "【最近内容】\n原始数据。"
        assert cm2._char_count == len("原始数据。")
        assert cm2.section_drafts == {1: "原始数据。\n\n"}

    def test_deserialize_historical_checkpoint_ignores_running_summary(self):
        historical = {
            "running_summary": "旧版压缩摘要，不应恢复",
            "compress_threshold": 6000,
            "buffer": ["第一小节", "第二小节", "第三小节", "第四小节"],
            "char_count": 999999,
            "section_drafts": {"1": "历史草稿\n\n"},
        }

        cm = ContextManager()
        cm.deserialize(historical)

        assert cm._buffer == ["第二小节", "第三小节", "第四小节"]
        assert cm._char_count == sum(map(len, cm._buffer))
        assert cm.section_drafts == {1: "历史草稿\n\n"}
        assert not hasattr(cm, "running_summary")

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
