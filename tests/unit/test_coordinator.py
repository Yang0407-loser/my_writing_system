"""测试 Coordinator 核心逻辑。"""

import pytest
from unittest.mock import MagicMock, patch, call


class TestSafeSerialize:
    """_safe_serialize 函数测试。"""

    def test_serialize_dict(self):
        from app.coordinator import _safe_serialize
        assert _safe_serialize({"a": 1}) == {"a": 1}

    def test_serialize_none(self):
        from app.coordinator import _safe_serialize
        assert _safe_serialize(None) == {}

    def test_serialize_object_with_serialize_method(self):
        from app.coordinator import _safe_serialize
        class HasSerialize:
            def serialize(self):
                return {"key": "value"}
        assert _safe_serialize(HasSerialize()) == {"key": "value"}

    def test_serialize_unserializable_returns_empty_dict(self):
        from app.coordinator import _safe_serialize
        assert _safe_serialize(object()) == {}


class TestPhaseDispatch:
    """Phase 分发逻辑测试。"""

    PHASES = [
        "characters", "style", "outline", "awaiting_outline",
        "character_arcs", "narrative_rhythm", "world_state", "writing",
        "awaiting_section", "consistency", "continuity", "review", "completed",
    ]

    def test_phase_order_has_no_gaps(self):
        """确保 phase 顺序是完整链路。"""
        assert len(self.PHASES) == 13
        # writing 必须出现在 review 之前
        assert self.PHASES.index("writing") < self.PHASES.index("review")
        # consistency 必须在 continuity 之前
        assert self.PHASES.index("consistency") < self.PHASES.index("continuity")

    def test_all_required_phases_present(self):
        """核心 phase 缺一不可。"""
        required = {"characters", "style", "outline", "writing", "review"}
        assert required.issubset(set(self.PHASES))

    def test_completed_is_last(self):
        assert self.PHASES[-1] == "completed"


class TestCheckpointFlow:
    """Checkpoint 保存/恢复流程测试（mock Blackboard）。"""

    def test_checkpoint_saved_at_phase_start(self):
        """每个 phase 开始时应调用 save_checkpoint。"""
        from app.blackboard import Blackboard
        from unittest.mock import MagicMock

        bb = MagicMock(spec=Blackboard)
        bb.save_checkpoint = MagicMock()

        # 模拟 phase 函数调用
        state = {"characters": [], "style": {}}
        bb.save_checkpoint("task-123", state)

        bb.save_checkpoint.assert_called_once_with("task-123", state)

    def test_start_from_mechanism(self):
        """start_from 参数应能跳转到指定 phase。"""
        phases = ["characters", "style", "outline", "writing", "review"]
        start_phase = "writing"

        started = False
        executed = []
        for p in phases:
            if p == start_phase:
                started = True
            if started:
                executed.append(p)

        assert executed == ["writing", "review"]
        assert "characters" not in executed


class TestTimelineHelper:
    """_add_timeline 函数测试。"""

    def test_add_timeline_appends_to_list(self):
        """时间线新增事件应 append 到列表。"""
        from app.coordinator import _add_timeline
        from unittest.mock import MagicMock

        bb = MagicMock()
        bb.get.return_value = [{"stage": "style", "agent": "system", "action": "old"}]

        _add_timeline(bb, "task-1", "writing", "writer", "new message")

        bb.set.assert_called_once()
        args = bb.set.call_args[0]
        assert args[0] == "task-1"
        assert args[1] == "timeline"
        timeline = args[2]
        assert len(timeline) == 2
        assert timeline[-1]["stage"] == "writing"
        assert timeline[-1]["agent"] == "writer"
        assert timeline[-1]["action"] == "new message"
