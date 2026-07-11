"""测试 Blackboard（使用 fakeredis）。"""

import json
import pytest
from app.blackboard import Blackboard


@pytest.fixture
def bb():
    """创建使用 fakeredis 的 Blackboard 实例。"""
    import fakeredis
    b = Blackboard()
    b._redis = fakeredis.FakeRedis()
    return b


class TestHashOps:
    def test_set_and_get(self, bb):
        bb.set("task-1", "status", "running")
        assert bb.get("task-1", "status") == "running"

    def test_get_nonexistent(self, bb):
        assert bb.get("no-such-task", "field") is None

    def test_get_all(self, bb):
        bb.set("task-1", "a", "1")
        bb.set("task-1", "b", "2")
        result = bb.get_all("task-1")
        assert result == {"a": "1", "b": "2"}

    def test_get_all_empty(self, bb):
        assert bb.get_all("no-such-task") == {}

    def test_delete(self, bb):
        bb.set("task-1", "status", "running")
        bb.delete("task-1")
        result = bb.get("task-1", "status")
        assert result is None or result == ""

    def test_json_serialization(self, bb):
        bb.set("task-1", "outline", [{"section": 1}, {"section": 2}])
        result = bb.get("task-1", "outline")
        if isinstance(result, str):
            import json
            result = json.loads(result)
        assert isinstance(result, list)
        assert result[0]["section"] == 1


class TestStreamOps:
    def test_xadd_and_xread(self, bb):
        bb.xadd_event("task-1", {"event": "token", "text": "hello"})
        events = bb.xread_events("task-1", "0-0")
        assert len(events) > 0

    def test_stream_delete(self, bb):
        bb.xadd_event("task-1", {"event": "test"})
        bb.stream_delete("task-1")
        events = bb.xread_events("task-1", "0-0")
        assert len(events) == 0


class TestCheckpoint:
    def test_save_and_load(self, bb):
        bb.save_checkpoint("task-1", {"phase": "writing", "data": [1, 2, 3]})
        loaded = bb.load_checkpoint("task-1")
        assert loaded["phase"] == "writing"
        assert loaded["data"] == [1, 2, 3]

    def test_load_nonexistent(self, bb):
        assert bb.load_checkpoint("no-such") is None

    def test_delete_checkpoint(self, bb):
        bb.save_checkpoint("task-1", {"x": 1})
        bb.delete_checkpoint("task-1")
        assert bb.load_checkpoint("task-1") is None


class TestDecisionQueue:
    def test_push_and_wait(self, bb):
        bb.push_decision("task-1", "outline", "approve")
        decision = bb.wait_for_decision("task-1", "outline", timeout=1)
        assert decision == "approve"

    def test_pop_decision(self, bb):
        bb.push_decision("task-1", "section", "approve")
        decision = bb.pop_decision("task-1", "section")
        assert decision == "approve"

    def test_clear_queue(self, bb):
        bb.push_decision("task-1", "outline", "approve")
        bb.clear_decision_queue("task-1", "outline")
        decision = bb.pop_decision("task-1", "outline")
        assert decision is None
