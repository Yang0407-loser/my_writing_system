from fastapi.testclient import TestClient
import pytest

from tests.e2e.support.server import create_support_app, validate_environment


@pytest.fixture
def client(monkeypatch, tmp_path):
    runtime = tmp_path / "owned-runtime"
    runtime.mkdir()
    monkeypatch.setenv("WRITER_TESTING", "1")
    monkeypatch.setenv("WRITER_E2E_RUNTIME_DIR", str(runtime))
    monkeypatch.setenv("TASK_DB_PATH", str(runtime / "tasks.db"))
    monkeypatch.setenv("WRITER_E2E_SCENARIO", "automatic")
    monkeypatch.setenv("WRITER_E2E_STEP_DELAY_MS", "1")
    with TestClient(create_support_app()) as support_client:
        yield support_client


def test_server_refuses_non_test_mode(monkeypatch):
    monkeypatch.setenv("WRITER_TESTING", "0")

    with pytest.raises(RuntimeError, match="WRITER_TESTING=1"):
        validate_environment()


def test_server_refuses_database_outside_owned_runtime(monkeypatch, tmp_path):
    runtime = tmp_path / "owned"
    runtime.mkdir()
    monkeypatch.setenv("WRITER_TESTING", "1")
    monkeypatch.setenv("WRITER_E2E_RUNTIME_DIR", str(runtime))
    monkeypatch.setenv("TASK_DB_PATH", str(tmp_path / "outside.db"))

    with pytest.raises(RuntimeError, match="inside WRITER_E2E_RUNTIME_DIR"):
        validate_environment()


def test_storage_reset_page_clears_both_stores_and_navigates(client):
    response = client.get("/__e2e__/clear-storage")

    assert response.status_code == 200
    assert "localStorage.clear()" in response.text
    assert "sessionStorage.clear()" in response.text
    assert "window.location.assign('/write-ui-v2')" in response.text
