import fakeredis
import pytest
from fastapi.testclient import TestClient

from app.blackboard import Blackboard
from app.config import settings
from app.main import app
import app.dependencies as dependencies
import app.routers.outline as outline_routes
import app.routers.tasks as task_routes
from tests.e2e.support.deterministic_writer import DeterministicWriter


@pytest.fixture
def e2e_client(monkeypatch, tmp_path):
    board = Blackboard()
    board._redis = fakeredis.FakeRedis(decode_responses=False)
    writer = DeterministicWriter(board)
    test_db = tmp_path / "tasks.db"
    export_root = tmp_path / "exports"
    export_root.mkdir()
    monkeypatch.setattr(settings, "TASK_DB_PATH", str(test_db))
    monkeypatch.setattr(dependencies, "bb", board)
    monkeypatch.setattr(task_routes, "bb", board)
    monkeypatch.setattr(task_routes, "writing_task", writer)
    monkeypatch.setattr(task_routes, "_export_root", lambda: export_root)
    monkeypatch.setattr(outline_routes, "_get_redis", lambda: board._redis)
    with TestClient(app) as client:
        yield client, writer, board
    writer.shutdown()
