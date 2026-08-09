from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config

from app.canonical.database import build_engine, build_session_factory
from app.canonical.hashing import sha256_json
from app.canonical.repositories import CanonicalRepository
from app.canonical.snapshot import (
    canonical_snapshot_bytes,
    export_project_snapshot,
    import_project_snapshot,
)


def _migrate(url: str) -> None:
    root = Path(__file__).resolve().parents[3]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")


def _seed(session):
    repo = CanonicalRepository(session, "tenant-golden", "project-golden")
    repo.create_project(
        owner_id="owner-golden",
        name="Golden",
        genesis_state_json={"foundation_state_v0": {"ledger_events": []}},
        genesis_state_version_id="state-genesis",
    )
    repo.create_document("document-golden", "Golden document")
    repo.create_subsection("subsection-1", "document-golden", 1)
    repo.create_subsection("subsection-2", "document-golden", 2)
    repo.create_commit_envelope(
        "commit-1", "a" * 64, 0, "state-genesis"
    )
    repo.append_revision(
        "revision-1", "commit-1", "subsection-1", "Golden first", "test"
    )
    repo.append_revision(
        "revision-2", "commit-1", "subsection-2", "Golden second", "test"
    )
    repo.append_ledger_event(
        ledger_id="ledger-1",
        commit_id="commit-1",
        ordinal=1,
        event_type="golden_seeded",
        payload={"subsections": 2},
        evidence_refs=["fixture"],
    )
    session.commit()
    return repo


def test_portable_snapshot_restores_without_redis_chroma_or_output(tmp_path):
    source_url = f"sqlite+pysqlite:///{(tmp_path / 'source.db').as_posix()}"
    target_url = f"sqlite+pysqlite:///{(tmp_path / 'target.db').as_posix()}"
    _migrate(source_url)
    _migrate(target_url)
    source_engine = build_engine(source_url)
    target_engine = build_engine(target_url)

    with build_session_factory(source_engine)() as source_session:
        source_repo = _seed(source_session)
        expected_text_hash = source_repo.materialize_document_hash("document-golden")
        expected_state = source_repo.get_current_state()
        snapshot = export_project_snapshot(
            source_session, tenant_id="tenant-golden", project_id="project-golden"
        )
        assert canonical_snapshot_bytes(snapshot) == canonical_snapshot_bytes(
            export_project_snapshot(
                source_session,
                tenant_id="tenant-golden",
                project_id="project-golden",
            )
        )

    assert not (tmp_path / "redis").exists()
    assert not (tmp_path / "chroma").exists()
    assert not (tmp_path / "output").exists()

    with build_session_factory(target_engine)() as target_session:
        import_project_snapshot(target_session, snapshot)
        target_session.commit()
        restored = CanonicalRepository(
            target_session, "tenant-golden", "project-golden"
        )
        assert restored.materialize_document_hash("document-golden") == expected_text_hash
        assert restored.get_current_state().id == expected_state.id
        assert restored.get_current_state().state_hash == expected_state.state_hash
        assert sha256_json(restored.list_ledger_payloads()) == snapshot["integrity"]["ledger_hash"]

    source_engine.dispose()
    target_engine.dispose()
