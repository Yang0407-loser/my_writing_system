"""共享测试 fixtures。"""

import pytest
from unittest.mock import MagicMock


@pytest.fixture
def sample_style():
    """中性风格预设（完整 50 维）。"""
    return {
        "narrative_density": 0.7,
        "primary_emotion": "中性", "emotion_intensity": 50, "emotion_subtlety": "含蓄",
        "emotion_blend": {},
        "emotion_curve": "平稳", "emotional_peaks": "均匀分布", "catharsis_style": "渐进式",
        "narrative_empathy": "适度共情", "inner_monologue_ratio": 0.2, "show_vs_tell": "平衡",
        "emotional_registry": "文学抒情", "emotional_contrast": "渐进演变",
        "short_sentence_ratio": 0.3, "long_sentence_ratio": 0.2,
        "sentence_pattern": "长短交替", "paragraph_rhythm": "均匀块状",
        "paragraph_length_avg": 200, "dialogue_ratio": 0.3, "dialogue_tag_style": "稀疏标记",
        "pacing": "中等", "scene_transition": "过渡铺垫", "tension_curve": "波浪起伏",
        "metaphor_frequency": "适度", "personification": "适度",
        "vocabulary_register": "文学化", "vocabulary_richness": "中等",
        "chengyu_frequency": "适度", "adjective_density": 0.15, "adverb_policy": "适度",
        "sensory_density": "适度", "sensory_spectrum": "视觉为主", "color_use": "暖色调",
        "imagery_domain": "自然", "style_brief": "中性风格，均衡叙事",
        "preset_name": "中性",
    }


@pytest.fixture
def sample_outline():
    """3 节 x 3 小节标准大纲。"""
    return [
        {
            "section": 1, "title": "开端",
            "key_points": ["引入主角", "建立世界观", "触发事件"],
            "subsections": [
                {"subsection": 1, "title": "相遇", "description": "主角在废弃星域发现蓝色星盘",
                 "key_points": ["星盘初次出现", "主角的反应"], "target_words": 1500},
                {"subsection": 2, "title": "冲突", "description": "追兵出现，主角被迫逃亡",
                 "key_points": ["逃亡场景", "第一次展现能力"], "target_words": 1500},
                {"subsection": 3, "title": "转折", "description": "意外获得盟友帮助",
                 "key_points": ["盟友出场", "信息揭露"], "target_words": 1500},
            ],
        },
        {
            "section": 2, "title": "发展",
            "key_points": ["深入调查", "结识同伴", "揭示阴谋"],
            "subsections": [
                {"subsection": 1, "title": "调查", "description": "主角开始调查星盘的来历",
                 "key_points": ["线索搜集", "世界观展开"], "target_words": 1500},
                {"subsection": 2, "title": "同伴", "description": "遇到关键盟友",
                 "key_points": ["盟友背景", "合作关系建立"], "target_words": 1500},
                {"subsection": 3, "title": "阴谋", "description": "发现更大的阴谋",
                 "key_points": ["阴谋揭露", "危机升级"], "target_words": 1500},
            ],
        },
        {
            "section": 3, "title": "高潮",
            "key_points": ["最终对决", "主题升华", "结局"],
            "subsections": [
                {"subsection": 1, "title": "准备", "description": "主角为最终对决做准备",
                 "key_points": ["内心挣扎", "最后准备"], "target_words": 1500},
                {"subsection": 2, "title": "对决", "description": "最终决战",
                 "key_points": ["高潮战斗", "关键抉择"], "target_words": 1500},
                {"subsection": 3, "title": "余波", "description": "战斗后的新秩序",
                 "key_points": ["结局", "主题呼应"], "target_words": 1500},
            ],
        },
    ]


@pytest.fixture
def sample_characters():
    """2 个完整角色 + 弧线。"""
    return [
        {
            "id": "char-001", "name": "江辰", "gender": "男", "age": "28",
            "personality": ["内向", "敏锐", "固执"],
            "motivation": "寻找失踪的妹妹",
            "background": "曾是星际探险队的导航员，妹妹在一次任务中失踪后独自追寻真相",
            "strengths": ["方向感极强", "在压力下保持冷静"],
            "weaknesses": ["不信任他人", "过度自责"],
        },
        {
            "id": "char-002", "name": "林雨", "gender": "女", "age": "25",
            "personality": ["热情", "冲动", "忠诚"],
            "motivation": "推翻腐败的星区政府",
            "background": "地下反抗组织成员，父母被星区政府迫害",
            "strengths": ["战斗技巧", "人脉广泛"],
            "weaknesses": ["容易情绪化", "冒进"],
        },
    ]


@pytest.fixture
def sample_arcs():
    """2 个角色弧线。"""
    return [
        {
            "character_id": "char-001", "name": "江辰",
            "starting_state": "孤独的追寻者",
            "ending_state": "找到真相后的释然",
            "key_milestones": [
                {"section": 1, "subsection": 1, "event": "江辰在废弃星域发现蓝色星盘",
                 "emotional_shift": "麻木→好奇"},
                {"section": 2, "subsection": 2, "event": "江辰与林雨结盟",
                 "emotional_shift": "不信任→开始信任"},
                {"section": 3, "subsection": 2, "event": "江辰揭穿星区政府阴谋",
                 "emotional_shift": "困惑→坚定"},
            ],
        },
        {
            "character_id": "char-002", "name": "林雨",
            "starting_state": "狂热的反抗者",
            "ending_state": "学会冷静策略",
            "key_milestones": [
                {"section": 1, "subsection": 3, "event": "林雨在逃亡中救了江辰",
                 "emotional_shift": "怀疑→好奇"},
                {"section": 2, "subsection": 2, "event": "林雨同意联合行动",
                 "emotional_shift": "独立→合作"},
                {"section": 3, "subsection": 1, "event": "林雨面临复仇与大局的抉择",
                 "emotional_shift": "冲动→克制"},
            ],
        },
    ]


@pytest.fixture
def mock_llm(mocker):
    """Mock LLM 客户端，注入到所有 Agent 的 self.llm。"""

    def _mock(response_text="{}"):
        mock_client = MagicMock()
        mock_client.chat_completion.return_value = response_text
        mock_client.chat_completion_stream.return_value = [response_text]

        def fake_init(self, llm_client=None):
            self.llm = mock_client
            self.last_raw_response = ""

        mocker.patch("app.agents.base.BaseAgent.__init__", fake_init)
        return mock_client

    return _mock


@pytest.fixture
def mock_redis_store(mocker):
    """Mock Redis，用于 Blackboard / EventGraph 测试。"""
    from fakeredis import FakeRedis
    fr = FakeRedis()
    mocker.patch("app.blackboard.Blackboard._redis", new_callable=lambda: fr)
    # Also need to mock the connection in blackboard's __init__
    import app.blackboard
    original = app.blackboard.Blackboard.__init__

    def mock_init(self):
        self._redis = fr

    mocker.patch.object(app.blackboard.Blackboard, "__init__", mock_init)
    return fr
