from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.orm import Session

from app.canonical.database import build_engine, build_session_factory
from app.canonical.errors import ScopeRequired
from app.canonical.hashing import sha256_text
from app.canonical.repositories import CanonicalRepository


def _migrate(url: str) -> None:
    root = Path(__file__).resolve().parents[3]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")


@pytest.fixture
def session(tmp_path):
    url = f"sqlite+pysqlite:///{(tmp_path / 'repo.db').as_posix()}"
    _migrate(url)
    engine = build_engine(url)
    with build_session_factory(engine)() as db_session:
        yield db_session
    engine.dispose()


def _repository(session: Session) -> CanonicalRepository:
    return CanonicalRepository(
        session=session,
        tenant_id="tenant-1",
        project_id="project-1",
    )


def _create_project(repo: CanonicalRepository):
    return repo.create_project(
        owner_id="owner-1",
        name="Golden",
        genesis_state_json={"foundation_state_v0": {"ledger_events": []}},
        genesis_state_version_id="state-genesis",
    )


def test_repository_requires_tenant_and_project_scope(session):
    with pytest.raises(ScopeRequired):
        CanonicalRepository(session=session, tenant_id="", project_id="project-1")
    with pytest.raises(ScopeRequired):
        CanonicalRepository(session=session, tenant_id="tenant-1", project_id="")


def test_create_project_atomically_creates_explicit_genesis_state_head(session):
    repo = _repository(session)

    project = _create_project(repo)

    assert project.current_state_version_id == "state-genesis"
    state = repo.get_current_state()
    assert state is not None
    assert state.id == "state-genesis"
    assert state.origin == "genesis"
    assert state.commit_id is None
    assert state.parent_state_version_id is None
    assert repo.get_project().current_state_version_id == state.id


def test_repository_never_commits_callers_transaction(session, tmp_path):
    repo = _repository(session)
    _create_project(repo)
    session.rollback()

    assert repo.get_project() is None


def test_revision_chain_heads_and_full_document_materialization(session):
    repo = _repository(session)
    _create_project(repo)
    repo.create_document(document_id="document-1", title="Document")
    repo.create_subsection(
        subsection_id="subsection-1", document_id="document-1", ordinal=1
    )
    repo.create_subsection(
        subsection_id="subsection-2", document_id="document-1", ordinal=2
    )
    repo.create_commit_envelope(
        commit_id="commit-1",
        candidate_hash="a" * 64,
        base_revision_number=0,
        base_state_version_id="state-genesis",
    )
    first = repo.append_revision(
        revision_id="revision-1",
        commit_id="commit-1",
        subsection_id="subsection-1",
        content="First v1",
        creator="test",
    )
    second = repo.append_revision(
        revision_id="revision-2",
        commit_id="commit-1",
        subsection_id="subsection-2",
        content="Second",
        creator="test",
    )
    repo.create_commit_envelope(
        commit_id="commit-2",
        candidate_hash="b" * 64,
        base_revision_number=1,
        base_state_version_id="state-genesis",
    )
    revised = repo.append_revision(
        revision_id="revision-3",
        commit_id="commit-2",
        subsection_id="subsection-1",
        content="First v2",
        creator="test",
    )

    assert first.parent_revision_id is None
    assert revised.parent_revision_id == first.id
    assert revised.revision_number == 2
    assert repo.get_current_revision("subsection-1").id == revised.id
    assert repo.get_current_revision("subsection-2").id == second.id
    assert repo.materialize_document("document-1") == "First v2\n\nSecond"
    assert repo.materialize_document_hash("document-1") == sha256_text(
        "First v2\n\nSecond"
    )


def test_scoped_reads_do_not_cross_tenant_or_project(session):
    repo = _repository(session)
    _create_project(repo)

    other_project = CanonicalRepository(
        session=session, tenant_id="tenant-1", project_id="project-2"
    )
    other_tenant = CanonicalRepository(
        session=session, tenant_id="tenant-2", project_id="project-1"
    )

    assert other_project.get_project() is None
    assert other_tenant.get_project() is None
