from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.canonical.database import build_engine, build_session_factory
from app.canonical.models import CanonicalProject, IdempotencyRecord
from app.canonical.repositories import CanonicalRepository


pytestmark = pytest.mark.postgres


def _id(_prefix: str) -> str:
    return str(uuid4())


def test_postgres_is_at_alembic_head_and_enforces_dual_heads_and_unique(
    postgres_database_url,
):
    engine = build_engine(postgres_database_url)
    session_factory = build_session_factory(engine)
    tenant_id = _id("tenant")
    project_a = _id("project")
    project_b = _id("project")
    with session_factory() as session:
        repo_a = CanonicalRepository(session, tenant_id, project_a)
        repo_b = CanonicalRepository(session, tenant_id, project_b)
        repo_a.create_project(
            owner_id="test",
            name="A",
            genesis_state_json={},
            genesis_state_version_id=_id("state"),
        )
        repo_b.create_project(
            owner_id="test",
            name="B",
            genesis_state_json={},
            genesis_state_version_id=_id("state"),
        )
        repo_a.create_document(_id("document"), "A")
        session.flush()

        head = session.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        assert head == "0003_p3a_projection_backfill"

        with session.begin_nested():
            session.add(
                IdempotencyRecord(
                    id=_id("idem"),
                    tenant_id=tenant_id,
                    project_id=project_a,
                    idempotency_key="same-key",
                    candidate_hash="a" * 64,
                    status="reserved",
                )
            )
            session.flush()
        with pytest.raises(IntegrityError):
            with session.begin_nested():
                session.add(
                    IdempotencyRecord(
                        id=_id("idem"),
                        tenant_id=tenant_id,
                        project_id=project_a,
                        idempotency_key="same-key",
                        candidate_hash="a" * 64,
                        status="reserved",
                    )
                )
                session.flush()

        state_b = repo_b.get_current_state().id
        with pytest.raises(IntegrityError):
            with session.begin_nested():
                session.execute(
                    text(
                        "UPDATE canonical_projects SET current_state_version_id=:state "
                        "WHERE id=:project"
                    ),
                    {"state": state_b, "project": project_a},
                )
                session.flush()
        session.rollback()
    engine.dispose()


def test_postgres_uncommitted_work_is_invisible_and_rollback_is_complete(
    postgres_database_url,
):
    engine = build_engine(postgres_database_url)
    session_factory = build_session_factory(engine)
    tenant_id = _id("tenant")
    project_id = _id("project")
    session_one = session_factory()
    session_two = session_factory()
    try:
        repo = CanonicalRepository(session_one, tenant_id, project_id)
        repo.create_project(
            owner_id="test",
            name="Uncommitted",
            genesis_state_json={},
            genesis_state_version_id=_id("state"),
        )
        assert session_two.scalar(
            select(CanonicalProject).where(CanonicalProject.id == project_id)
        ) is None
        session_one.rollback()
        session_two.expire_all()
        assert session_two.scalar(
            select(CanonicalProject).where(CanonicalProject.id == project_id)
        ) is None
    finally:
        session_one.close()
        session_two.close()
        engine.dispose()
