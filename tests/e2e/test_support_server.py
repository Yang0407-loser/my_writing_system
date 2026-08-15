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


def _production_bindings():
    from app.config import settings
    import app.dependencies as dependencies
    import app.routers.outline as outline_routes
    import app.routers.tasks as task_routes

    return {
        "task_db_path": settings.TASK_DB_PATH,
        "dependencies_bb": dependencies.bb,
        "task_routes_bb": task_routes.bb,
        "writing_task": task_routes.writing_task,
        "export_root": task_routes._export_root,
        "get_redis": outline_routes._get_redis,
    }


def _assert_production_bindings(bindings):
    from app.config import settings
    import app.dependencies as dependencies
    import app.routers.outline as outline_routes
    import app.routers.tasks as task_routes

    assert settings.TASK_DB_PATH == bindings["task_db_path"]
    assert dependencies.bb is bindings["dependencies_bb"]
    assert task_routes.bb is bindings["task_routes_bb"]
    assert task_routes.writing_task is bindings["writing_task"]
    assert task_routes._export_root is bindings["export_root"]
    assert outline_routes._get_redis is bindings["get_redis"]


def _restore_production_bindings(bindings):
    from app.config import settings
    import app.dependencies as dependencies
    import app.routers.outline as outline_routes
    import app.routers.tasks as task_routes

    settings.TASK_DB_PATH = bindings["task_db_path"]
    dependencies.bb = bindings["dependencies_bb"]
    task_routes.bb = bindings["task_routes_bb"]
    task_routes.writing_task = bindings["writing_task"]
    task_routes._export_root = bindings["export_root"]
    outline_routes._get_redis = bindings["get_redis"]


def test_wrapper_lifespan_runs_production_startup_and_restores_bindings(
    monkeypatch, tmp_path
):
    from app import rule_store

    runtime = tmp_path / "owned-runtime"
    runtime.mkdir()
    monkeypatch.setenv("WRITER_TESTING", "1")
    monkeypatch.setenv("WRITER_E2E_RUNTIME_DIR", str(runtime))
    monkeypatch.setenv("TASK_DB_PATH", str(runtime / "tasks.db"))
    monkeypatch.setenv("WRITER_E2E_SCENARIO", "automatic")
    monkeypatch.setenv("WRITER_E2E_STEP_DELAY_MS", "1")
    seeded = []
    monkeypatch.setattr(rule_store, "ensure_presets_seeded", lambda: seeded.append(True))
    before = _production_bindings()

    support = create_support_app()
    try:
        _assert_production_bindings(before)
        with TestClient(support):
            assert seeded == [True]
            assert support.state.writer._thread is not None
            assert support.state.writer._thread.is_alive()
        _assert_production_bindings(before)
        assert not support.state.writer._thread.is_alive()
    finally:
        _restore_production_bindings(before)


def test_production_startup_error_restores_bindings_without_starting_writer(
    monkeypatch, tmp_path
):
    from app import rule_store

    runtime = tmp_path / "owned-runtime"
    runtime.mkdir()
    monkeypatch.setenv("WRITER_TESTING", "1")
    monkeypatch.setenv("WRITER_E2E_RUNTIME_DIR", str(runtime))
    monkeypatch.setenv("TASK_DB_PATH", str(runtime / "tasks.db"))
    monkeypatch.setenv("WRITER_E2E_SCENARIO", "automatic")
    monkeypatch.setattr(
        rule_store,
        "ensure_presets_seeded",
        lambda: (_ for _ in ()).throw(RuntimeError("seed failure")),
    )
    before = _production_bindings()
    support = create_support_app()

    try:
        with pytest.raises(RuntimeError, match="seed failure"):
            with TestClient(support):
                pass
        _assert_production_bindings(before)
        assert support.state.writer._thread is None
    finally:
        _restore_production_bindings(before)


def test_storage_reset_page_clears_both_stores_and_navigates(client):
    response = client.get("/__e2e__/clear-storage")

    assert response.status_code == 200
    assert "localStorage.clear()" in response.text
    assert "sessionStorage.clear()" in response.text
    assert "window.location.assign('/write-ui-v2')" in response.text
