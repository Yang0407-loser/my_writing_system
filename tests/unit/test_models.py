"""测试 Pydantic 模型。"""

from app.models import WriteRequest, WriteResponse, CharacterProfile, StyleProfile


class TestWriteRequest:
    def test_minimal_request(self):
        req = WriteRequest(topic="test", reference_text="ref")
        assert req.topic == "test"
        assert req.target_words_per_section == 10000
        assert req.characters == []
        assert req.style_profile == {}

    def test_full_request(self):
        req = WriteRequest(
            topic="星际冒险",
            reference_text="参考文本",
            target_words_per_section=5000,
            world_setting="赛博朋克2099",
            story_synopsis="一个修理工的故事",
            style_profile={"primary_emotion": "激昂"},
        )
        assert req.world_setting == "赛博朋克2099"
        assert req.style_profile["primary_emotion"] == "激昂"

    def test_character_timelines_removed(self):
        """B3: character_timelines 字段已删除。"""
        req = WriteRequest(topic="t", reference_text="r")
        assert not hasattr(req, "character_timelines")


class TestStyleProfile:
    def test_default_narrative_density(self):
        sp = StyleProfile()
        assert sp.narrative_density == 0.7

    def test_full_profile(self, sample_style):
        sp = StyleProfile(**sample_style)
        assert sp.primary_emotion == "中性"
        assert sp.emotion_intensity == 50
        assert sp.short_sentence_ratio == 0.3
        assert sp.dialogue_ratio == 0.3
