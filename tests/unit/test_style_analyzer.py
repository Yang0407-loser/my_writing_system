"""测试 StyleAnalyzer。"""

from app.agents.style_analyzer import StyleAnalyzer, STYLE_PRESETS


class TestStylePresets:
    def test_all_presets_exist(self):
        expected = ["中性", "热血", "冷峻", "治愈", "压抑", "紧迫", "荒诞"]
        for name in expected:
            assert name in STYLE_PRESETS

    def test_preset_has_required_fields(self):
        for name, preset in STYLE_PRESETS.items():
            assert "primary_emotion" in preset
            assert "emotion_intensity" in preset
            assert "narrative_density" in preset
            assert isinstance(preset["narrative_density"], (int, float))

    def test_list_presets(self):
        names = StyleAnalyzer.list_presets()
        assert "热血" in names
        assert "冷峻" in names

    def test_get_preset_returns_copy(self):
        a = StyleAnalyzer.get_preset("热血")
        b = StyleAnalyzer.get_preset("热血")
        assert a is not b  # different objects
        a["narrative_density"] = 0.99
        assert b["narrative_density"] == 0.5  # original preserved

    def test_get_preset_unknown_falls_back(self):
        preset = StyleAnalyzer.get_preset("不存在的风格")
        assert preset["primary_emotion"] == "中性"


class TestFillDefaults:
    def test_fills_missing_fields(self):
        sa = StyleAnalyzer()
        result = sa._fill_defaults({"primary_emotion": "激昂"})
        assert result["primary_emotion"] == "激昂"
        assert "emotion_intensity" in result
        assert "short_sentence_ratio" in result
        assert result["emotion_intensity"] == 50  # from neutral default

    def test_preserves_provided_fields(self):
        sa = StyleAnalyzer()
        result = sa._fill_defaults({"emotion_intensity": 99})
        assert result["emotion_intensity"] == 99
