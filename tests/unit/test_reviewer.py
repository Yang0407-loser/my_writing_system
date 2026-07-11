"""测试 Reviewer。"""

import json
from app.agents.reviewer import Reviewer


class TestReviewSection:
    def test_fallback_on_parse_failure(self, mock_llm):
        """审阅返回非法 JSON 时回退到默认评分。"""
        mock_llm("not valid json at all")
        reviewer = Reviewer()
        result = reviewer.review_section(
            section_num=1, topic="test", style={},
            section_draft="测试内容。",
        )
        assert result["score"] == 6
        assert "consistency_notes" in result

    def test_parse_valid_json(self, mock_llm):
        mock_llm('{"score": 8, "consistency_notes": "很好", "improvement": "可以更好"}')
        reviewer = Reviewer()
        result = reviewer.review_section(
            section_num=1, topic="test", style={"style_brief": "测试"},
            section_draft="测试内容。",
        )
        assert result["score"] == 8


class TestReviewGlobal:
    def test_fallback_on_parse_failure(self, mock_llm):
        mock_llm("garbage response")
        reviewer = Reviewer()
        result = reviewer.review_global(
            topic="test", style={}, section_summaries="摘要",
            total_words=1000,
        )
        assert result["global_score"] == 6

    def test_with_characters(self, mock_llm, sample_characters, sample_arcs):
        mock_llm(json.dumps({
            "global_score": 7, "strength": "结构清晰", "weakness": "节奏稍快",
            "suggestion": "可以放慢一点", "handover_insight": "交接顺畅",
            "character_consistency": "一致", "character_arc_progress": "不错",
        }))
        reviewer = Reviewer()
        result = reviewer.review_global(
            topic="test", style={}, section_summaries="摘要",
            total_words=1000, characters=sample_characters,
            character_arcs=sample_arcs,
        )
        assert result["global_score"] == 7
        assert result["character_consistency"] == "一致"
