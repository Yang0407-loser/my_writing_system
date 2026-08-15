"""Isolated same-origin server for browser end-to-end scenarios."""

from contextlib import asynccontextmanager
import os
from pathlib import Path


def validate_environment() -> tuple[Path, Path]:
    """Reject a browser harness that could touch non-E2E state."""
    if os.environ.get("WRITER_TESTING") != "1":
        raise RuntimeError("browser E2E requires WRITER_TESTING=1")

    runtime = Path(os.environ["WRITER_E2E_RUNTIME_DIR"]).resolve()
    database = Path(os.environ["TASK_DB_PATH"]).resolve()
    if runtime != database.parent and runtime not in database.parents:
        raise RuntimeError("TASK_DB_PATH must be inside WRITER_E2E_RUNTIME_DIR")

    if os.environ.get("WRITER_E2E_SCENARIO") not in {"automatic", "interactive"}:
        raise RuntimeError("WRITER_E2E_SCENARIO must be automatic or interactive")
    return runtime, database


def create_support_app():
    """Build a test-only wrapper before importing the production application."""
    runtime, database = validate_environment()

    import fakeredis
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse

    from app.blackboard import Blackboard
    from app.config import settings
    import app.dependencies as dependencies
    from app.main import app as production_app
    import app.routers.outline as outline_routes
    import app.routers.tasks as task_routes
    import app.rule_store as rule_store
    from tests.e2e.support.deterministic_writer import DeterministicWriter

    board = Blackboard()
    board._redis = fakeredis.FakeRedis(decode_responses=False)
    writer = DeterministicWriter(
        board,
        step_delay_ms=int(os.environ.get("WRITER_E2E_STEP_DELAY_MS", "250")),
    )

    export_root = runtime / "exports"

    def isolated_export_root() -> Path:
        export_root.mkdir(parents=True, exist_ok=True)
        return export_root

    original_bindings = {
        "task_db_path": settings.TASK_DB_PATH,
        "dependencies_bb": dependencies.bb,
        "task_routes_bb": task_routes.bb,
        "writing_task": task_routes.writing_task,
        "export_root": task_routes._export_root,
        "get_redis": outline_routes._get_redis,
        "rules_db_path": rule_store.RULES_DB_PATH,
    }

    def install_test_bindings() -> None:
        settings.TASK_DB_PATH = str(database)
        dependencies.bb = board
        task_routes.bb = board
        task_routes.writing_task = writer
        task_routes._export_root = isolated_export_root
        outline_routes._get_redis = lambda: board._redis
        rule_store.RULES_DB_PATH = str(runtime / "rules.db")

    def restore_production_bindings() -> None:
        settings.TASK_DB_PATH = original_bindings["task_db_path"]
        dependencies.bb = original_bindings["dependencies_bb"]
        task_routes.bb = original_bindings["task_routes_bb"]
        task_routes.writing_task = original_bindings["writing_task"]
        task_routes._export_root = original_bindings["export_root"]
        outline_routes._get_redis = original_bindings["get_redis"]
        rule_store.RULES_DB_PATH = original_bindings["rules_db_path"]

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            install_test_bindings()
            async with production_app.router.lifespan_context(production_app):
                writer.start()
                try:
                    yield
                finally:
                    writer.shutdown()
        finally:
            restore_production_bindings()

    support = FastAPI(title="Writer browser E2E support", lifespan=lifespan)
    support.state.writer = writer
    support.state.runtime_dir = runtime

    @support.get("/__e2e__/clear-storage", response_class=HTMLResponse)
    def clear_storage():
        return """<!doctype html><html><body>
        <button id="clear-and-return" onclick="localStorage.clear();sessionStorage.clear();window.location.assign('/write-ui-v2')">
          清除浏览器缓存并返回写作页
        </button></body></html>"""

    support.mount("/", production_app)
    return support


def main() -> None:
    validate_environment()
    import uvicorn

    uvicorn.run(
        create_support_app(),
        host="127.0.0.1",
        port=int(os.environ.get("WRITER_E2E_PORT", "2947")),
    )


if __name__ == "__main__":
    main()
