from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from alembic import command
from alembic.config import Config
from sqlalchemy import select

from app.canonical.commit_service import CanonicalCommitService, PROJECTION_MANIFEST
from app.canonical.database import build_engine, build_session_factory
from app.canonical.models import CanonicalProject, OutboxEvent
from app.canonical.outbox import OutboxDispatcher
from tests.unit.canonical.test_commit_service import _prepared
from app.canonical.repositories import CanonicalRepository


def test_failed_outbox_survives_session_restart_and_retries(tmp_path):
    root = Path(__file__).resolve().parents[3]
    url = f"sqlite+pysqlite:///{(tmp_path / 'outbox-retry.db').as_posix()}"
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")
    engine = build_engine(url)
    factory = build_session_factory(engine)

    with factory() as session:
        repo = CanonicalRepository(session, "tenant-1", "project-1")
        repo.create_project(
            owner_id="owner",
            name="Outbox",
            genesis_state_json={"foundation_state_v0": {}},
            genesis_state_version_id="state-genesis",
        )
        repo.create_document("document-1", "Document")
        repo.create_subsection("subsection-1", "document-1", 1, 1, 1)
        session.commit()
        result = CanonicalCommitService(session, "tenant-1", "project-1").commit(
            _prepared(session), "outbox-restart"
        )
        project_head = session.get(CanonicalProject, "project-1").current_state_version_id
        failing = {name: MagicMock() for name, _ in PROJECTION_MANIFEST}
        failing["legacy_world_event"] = MagicMock(side_effect=RuntimeError("offline"))
        OutboxDispatcher(
            lambda: session, "tenant-1", "project-1", failing
        ).dispatch_critical(result.commit_id)

    with factory() as restarted_session:
        successful = {name: MagicMock() for name, _ in PROJECTION_MANIFEST}
        summary = OutboxDispatcher(
            lambda: restarted_session, "tenant-1", "project-1", successful
        ).dispatch_pending(limit=100)
        assert summary["failed"] == 0
        assert restarted_session.get(
            CanonicalProject, "project-1"
        ).current_state_version_id == project_head
        assert all(
            event.status == "published"
            for event in restarted_session.scalars(
                select(OutboxEvent).where(OutboxEvent.commit_id == result.commit_id)
            ).all()
        )
    engine.dispose()
