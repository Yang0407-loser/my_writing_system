from __future__ import annotations

import pytest

from app.canonical.database import build_engine, build_session_factory
from app.canonical.models import Base
from app.canonical.repositories import CanonicalRepository


@pytest.fixture
def canonical_session(tmp_path):
    engine = build_engine(f"sqlite+pysqlite:///{(tmp_path / 'canonical.db').as_posix()}")
    Base.metadata.create_all(engine)
    factory = build_session_factory(engine)
    with factory() as session:
        repo = CanonicalRepository(session, "tenant-1", "project-1")
        repo.create_project(
            owner_id="owner-1",
            name="Project",
            genesis_state_json={
                "foundation_state_v0": {"world_mutations": [], "ledger_events": []}
            },
            genesis_state_version_id="state-genesis",
        )
        repo.create_document("document-1", "Document")
        repo.create_subsection("subsection-1", "document-1", 1, 1, 1)
        repo.create_subsection("subsection-2", "document-1", 2, 1, 2)
        session.commit()
        yield session
    engine.dispose()
