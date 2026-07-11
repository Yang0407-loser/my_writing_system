"""测试 TaskStore。"""

import os
import pytest
from app.task_store import TaskStore


@pytest.fixture
def ts():
    """使用临时 SQLite 数据库的 TaskStore。"""
    import uuid
    path = f"tests/unit/_test_tasks_{uuid.uuid4().hex[:8]}.db"
    store = TaskStore(db_path=path)
    yield store
    store._conn.close()
    for ext in ["", "-shm", "-wal"]:
        try:
            os.remove(path + ext)
        except OSError:
            pass


class TestSaveAndList:
    def test_save_new(self, ts):
        ts.save("task-1", {
            "topic": "测试主题",
            "word_count": 500,
            "section_count": 3,
            "status": "completed",
            "mode": "celery",
            "style": {},
            "outline": [],
            "handover_notes": [],
            "characters": [],
            "review": {},
            "world_setting": "",
            "story_synopsis": "",
            "target_words": 10000,
            "world_state": {},
            "draft": "草稿内容。",
            "output_file": "",
            "events": [],
            "analysis": {},
        })
        tasks = ts.list_all()
        assert len(tasks) == 1

    def test_list_returns_latest_first(self, ts):
        ts.save("task-1", {"topic": "旧", "word_count": 100, "section_count": 1, "status": "completed",
                           "mode": "celery", "style": {}, "outline": [], "handover_notes": [],
                           "characters": [], "review": {}, "world_setting": "", "story_synopsis": "",
                           "target_words": 0, "world_state": {}, "draft": "", "output_file": "", "events": [], "analysis": {}})
        ts.save("task-2", {"topic": "新", "word_count": 200, "section_count": 2, "status": "completed",
                           "mode": "celery", "style": {}, "outline": [], "handover_notes": [],
                           "characters": [], "review": {}, "world_setting": "", "story_synopsis": "",
                           "target_words": 0, "world_state": {}, "draft": "", "output_file": "", "events": [], "analysis": {}})
        tasks = ts.list_all()
        assert len(tasks) == 2
        ids = {t["task_id"] for t in tasks}
        assert ids == {"task-1", "task-2"}

    def test_save_update(self, ts):
        ts.save("task-1", {"topic": "旧主题", "word_count": 100, "section_count": 1, "status": "completed",
                           "mode": "celery", "style": {}, "outline": [], "handover_notes": [],
                           "characters": [], "review": {}, "world_setting": "", "story_synopsis": "",
                           "target_words": 0, "world_state": {}, "draft": "", "output_file": "", "events": [], "analysis": {}})
        ts.save("task-1", {"topic": "更新主题", "word_count": 100, "section_count": 1, "status": "completed",
                           "mode": "celery", "style": {}, "outline": [], "handover_notes": [],
                           "characters": [], "review": {}, "world_setting": "", "story_synopsis": "",
                           "target_words": 0, "world_state": {}, "draft": "", "output_file": "", "events": [], "analysis": {}})
        tasks = ts.list_all()
        assert len(tasks) == 1
        assert tasks[0]["topic"] == "更新主题"

    def test_get(self, ts):
        ts.save("task-1", {"topic": "测试", "word_count": 100, "section_count": 1, "status": "completed",
                           "mode": "celery", "style": {}, "outline": [], "handover_notes": [],
                           "characters": [], "review": {}, "world_setting": "", "story_synopsis": "",
                           "target_words": 0, "world_state": {}, "draft": "", "output_file": "", "events": [], "analysis": {}})
        result = ts.get("task-1")
        assert result["topic"] == "测试"

    def test_get_nonexistent(self, ts):
        assert ts.get("no-such") is None

    def test_delete(self, ts):
        ts.save("task-1", {"topic": "删除", "word_count": 100, "section_count": 1, "status": "completed",
                           "mode": "celery", "style": {}, "outline": [], "handover_notes": [],
                           "characters": [], "review": {}, "world_setting": "", "story_synopsis": "",
                           "target_words": 0, "world_state": {}, "draft": "", "output_file": "", "events": [], "analysis": {}})
        assert ts.delete("task-1") is True
        assert ts.list_all() == []

    def test_no_char_timeline_column(self, ts):
        """B3: char_timeline 列已移除。"""
        ts.save("task-1", {"topic": "测试", "word_count": 100, "section_count": 1, "status": "completed",
                           "mode": "celery", "style": {}, "outline": [], "handover_notes": [],
                           "characters": [], "review": {}, "world_setting": "", "story_synopsis": "",
                           "target_words": 0, "world_state": {}, "draft": "", "output_file": "", "events": [], "analysis": {}})
        task = ts.get("task-1")
        assert "char_timeline" not in task
