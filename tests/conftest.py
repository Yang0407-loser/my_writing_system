"""共享测试 fixtures。"""

import os
from pathlib import Path
import shutil
import sys
import tempfile


pytest_plugins = ["tests.support.pytest_suite_plugin"]


_TEST_RUNTIME_DIR = Path(tempfile.mkdtemp(prefix="writer-tests-"))

# These assignments must run before any app.* import. Tests are isolated from
# developer .env files and from production/runtime stores by default.
os.environ["WRITER_TESTING"] = "1"
os.environ["WRITER_HANDOVER_CONTRACT_VERSION"] = "v1"
os.environ["WRITER_WORLD_RUNTIME_MODE"] = "off"
os.environ["RAG_PHASE3_SHADOW"] = "false"
os.environ["RAG_RERANKER_ENABLED"] = "false"
os.environ["CANONICAL_COMMIT_MODE"] = "legacy"
os.environ["CANONICAL_DATABASE_URL"] = (
    f"sqlite:///{(_TEST_RUNTIME_DIR / 'canonical.db').as_posix()}"
)
os.environ["TASK_DB_PATH"] = str(_TEST_RUNTIME_DIR / "tasks.db")
os.environ["CHARACTER_DB_PATH"] = str(_TEST_RUNTIME_DIR / "characters.db")
os.environ["CHROMA_DATA_PATH"] = str(_TEST_RUNTIME_DIR / "chroma")

# Historical quality audits are read-only and resolve their approved local
# fixture by hash. Keep the private database out of version control while
# making it available inside the isolated per-session runtime when present.
_LOCAL_TASK_FIXTURE = Path(__file__).resolve().parents[1] / "tasks.db"
if _LOCAL_TASK_FIXTURE.exists():
    shutil.copy2(_LOCAL_TASK_FIXTURE, _TEST_RUNTIME_DIR / "tasks.db")

import pytest  # noqa: E402
from unittest.mock import MagicMock  # noqa: E402


def pytest_sessionfinish(session, exitstatus):
    """Remove only the per-session test runtime directory owned above."""
    task_store = sys.modules.get("app.task_store")
    if task_store is not None:
        store_class = getattr(task_store, "TaskStore", None)
        if store_class is not None:
            store_class.close_all()
    dependencies = sys.modules.get("app.dependencies")
    if dependencies is not None:
        store = getattr(dependencies, "char_store", None)
        connection = getattr(store, "_conn", None)
        if connection is not None:
            connection.close()
    shutil.rmtree(_TEST_RUNTIME_DIR)


@pytest.fixture
def sample_style():
    """当前风格契约：4 个主旋钮 + 兼容字段。"""
    return {
        "emotion_intensity": 50,
        "dialogue_ratio": 0.3,
        "sentence_preference": "balanced",
        "sensory_density": "medium",
        "narrative_density": 0.5,
        "adjective_density": 0.15,
        "paragraph_length_avg": 200,
        "short_sentence_ratio": 0.33,
        "medium_sentence_ratio": 0.34,
        "long_sentence_ratio": 0.33,
        "dialogue_tag_style": "动作替代",
        "pacing": "中等",
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

        def fake_init(self, llm_client=None, model=None):
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
    def mock_init(self):
        self._redis = fr

    mocker.patch.object(app.blackboard.Blackboard, "__init__", mock_init)
    return fr
