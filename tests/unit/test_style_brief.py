"""测试 StyleSummarizer。"""

from app.utils.style_brief import StyleSummarizer


class TestForWriter:
    def test_returns_non_empty_string(self, sample_style):
        result = StyleSummarizer.for_writer(sample_style)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_includes_sentence_ratio(self, sample_style):
        result = StyleSummarizer.for_writer(sample_style)
        assert "30%" in result  # short_sentence_ratio = 0.3

    def test_includes_dialogue_ratio(self, sample_style):
        result = StyleSummarizer.for_writer(sample_style)
        assert "30%" in result

    def test_includes_paragraph_rhythm(self, sample_style):
        result = StyleSummarizer.for_writer(sample_style)
        assert "均匀块状" in result

    def test_empty_style(self):
        result = StyleSummarizer.for_writer({})
        assert isinstance(result, str)


class TestForPlanner:
    def test_returns_non_empty_string(self, sample_style):
        result = StyleSummarizer.for_planner(sample_style)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_includes_emotion(self, sample_style):
        result = StyleSummarizer.for_planner(sample_style)
        assert "中性" in result
        assert "50" in result

    def test_empty_style(self):
        result = StyleSummarizer.for_planner({})
        assert isinstance(result, str)


class TestForReviewer:
    def test_returns_dict(self, sample_style):
        result = StyleSummarizer.for_reviewer(sample_style)
        assert isinstance(result, dict)

    def test_has_key_dimensions(self, sample_style):
        result = StyleSummarizer.for_reviewer(sample_style)
        assert "dialogue_ratio" in result
        assert "emotion_intensity" in result
        assert result["emotion_intensity"] == 50

    def test_empty_style(self):
        result = StyleSummarizer.for_reviewer({})
        assert isinstance(result, dict)
