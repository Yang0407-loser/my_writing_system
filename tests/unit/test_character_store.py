"""测试 CharacterStore。"""

import os
import pytest
from app.character_store import CharacterStore


@pytest.fixture
def cs():
    """使用临时 SQLite 数据库的 CharacterStore。"""
    import uuid
    path = f"tests/unit/_test_chars_{uuid.uuid4().hex[:8]}.db"
    store = CharacterStore(db_path=path)
    yield store
    store._conn.close()
    for ext in ["", "-shm", "-wal"]:
        try:
            os.remove(path + ext)
        except OSError:
            pass


class TestCRUD:
    def test_create(self, cs):
        cs.create({"id": "char-1", "name": "江辰", "gender": "男", "age": "28",
                   "personality": ["内向", "敏锐"], "motivation": "寻找真相"})
        char = cs.get("char-1")
        assert char["name"] == "江辰"
        assert char["personality"] == ["内向", "敏锐"]

    def test_update(self, cs):
        cs.create({"id": "char-1", "name": "江辰"})
        cs.update("char-1", {"name": "江辰 V2", "age": "30"})
        char = cs.get("char-1")
        assert char["name"] == "江辰 V2"

    def test_delete(self, cs):
        cs.create({"id": "char-1", "name": "江辰"})
        assert cs.delete("char-1") is True
        assert cs.get("char-1") is None

    def test_delete_nonexistent(self, cs):
        assert cs.delete("no-such") is False

    def test_find_by_name(self, cs):
        cs.create({"id": "c1", "name": "江辰"})
        cs.create({"id": "c2", "name": "林雨"})
        results = cs.find_by_name("江")
        assert len(results) == 1

    def test_list_all(self, cs):
        cs.create({"id": "c1", "name": "江辰"})
        cs.create({"id": "c2", "name": "林雨"})
        results = cs.list_all()
        assert len(results) == 2

    def test_list_all_with_search(self, cs):
        cs.create({"id": "c1", "name": "江辰", "personality": ["内向"]})
        cs.create({"id": "c2", "name": "林雨", "personality": ["热情"]})
        results = cs.list_all(search="江")
        assert len(results) == 1
