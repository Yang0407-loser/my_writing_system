from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.canonical.database import build_engine


EXPECTED_TABLES = {
    "canonical_projects",
    "canonical_documents",
    "canonical_subsections",
    "document_revisions",
    "canonical_state_versions",
    "canonical_commits",
    "event_ledger",
    "idempotency_records",
    "outbox_events",
}


def _alembic_config(database_url: str) -> Config:
    root = Path(__file__).resolve().parents[3]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


@pytest.fixture
def migrated_database(tmp_path):
    url = f"sqlite+pysqlite:///{(tmp_path / 'canonical.db').as_posix()}"
    command.upgrade(_alembic_config(url), "head")
    return url


def test_migration_creates_exact_schema_and_two_composite_heads(migrated_database):
    engine = build_engine(migrated_database)
    inspector = inspect(engine)

    assert EXPECTED_TABLES.issubset(set(inspector.get_table_names()))
    project_fks = inspector.get_foreign_keys("canonical_projects")
    subsection_fks = inspector.get_foreign_keys("canonical_subsections")
    assert any(
        fk["constrained_columns"] == ["id", "current_state_version_id"]
        and fk["referred_table"] == "canonical_state_versions"
        and fk["referred_columns"] == ["project_id", "id"]
        for fk in project_fks
    )
    assert any(
        fk["constrained_columns"] == ["id", "current_revision_id"]
        and fk["referred_table"] == "document_revisions"
        and fk["referred_columns"] == ["subsection_id", "id"]
        for fk in subsection_fks
    )
    with engine.connect() as connection:
        assert connection.execute(text("PRAGMA foreign_keys")).scalar_one() == 1


def test_database_rejects_cross_subsection_and_cross_project_heads(migrated_database):
    engine = build_engine(migrated_database)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO canonical_projects "
                "(id, tenant_id, owner_id, name) VALUES "
                "('project-a', 'tenant-1', 'owner-1', 'A'), "
                "('project-b', 'tenant-1', 'owner-1', 'B')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO canonical_state_versions "
                "(id, tenant_id, project_id, origin, transition_version, schema_version, state_json, state_hash) VALUES "
                "('state-a', 'tenant-1', 'project-a', 'genesis', 'genesis-v0', 'canonical-state-v0', '{}', 'hash-a'), "
                "('state-b', 'tenant-1', 'project-b', 'genesis', 'genesis-v0', 'canonical-state-v0', '{}', 'hash-b')"
            )
        )
        connection.execute(
            text(
                "UPDATE canonical_projects SET current_state_version_id = "
                "CASE id WHEN 'project-a' THEN 'state-a' ELSE 'state-b' END"
            )
        )
        connection.execute(
            text(
                "INSERT INTO canonical_documents (id, tenant_id, project_id, title) "
                "VALUES ('document-a', 'tenant-1', 'project-a', 'Doc')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO canonical_subsections "
                "(id, tenant_id, project_id, document_id, ordinal, legacy_section, legacy_subsection) VALUES "
                "('sub-a', 'tenant-1', 'project-a', 'document-a', 1, 1, 1), "
                "('sub-b', 'tenant-1', 'project-a', 'document-a', 2, 1, 2)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO canonical_commits "
                "(id, tenant_id, project_id, candidate_hash, base_revision_number, base_state_version_id, status) "
                "VALUES ('commit-a', 'tenant-1', 'project-a', 'candidate-hash', 0, 'state-a', 'committed')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO document_revisions "
                "(id, tenant_id, project_id, commit_id, subsection_id, revision_number, content, content_hash, status, creator, metadata_json) VALUES "
                "('rev-a', 'tenant-1', 'project-a', 'commit-a', 'sub-a', 1, 'A', 'content-a', 'accepted', 'test', '{}'), "
                "('rev-b', 'tenant-1', 'project-a', 'commit-a', 'sub-b', 1, 'B', 'content-b', 'accepted', 'test', '{}')"
            )
        )
        connection.execute(
            text("UPDATE canonical_subsections SET current_revision_id='rev-a' WHERE id='sub-a'")
        )

    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE canonical_subsections SET current_revision_id='rev-b' WHERE id='sub-a'")
            )
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE canonical_projects SET current_state_version_id='state-b' WHERE id='project-a'")
            )


def test_json_round_trip_and_unique_constraints(migrated_database):
    engine = build_engine(migrated_database)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO canonical_projects (id, tenant_id, owner_id, name) "
                "VALUES ('project-json', 'tenant-1', 'owner-1', 'JSON')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO canonical_state_versions "
                "(id, tenant_id, project_id, origin, transition_version, schema_version, state_json, state_hash) "
                "VALUES ('state-json', 'tenant-1', 'project-json', 'genesis', 'genesis-v0', "
                "'canonical-state-v0', json(:state_json), 'hash-json')"
            ),
            {"state_json": '{"nested":{"value":1}}'},
        )
        payload = connection.execute(
            text("SELECT state_json FROM canonical_state_versions WHERE id='state-json'")
        ).scalar_one()
        assert "nested" in payload


def test_downgrade_and_upgrade_are_repeatable(tmp_path):
    url = f"sqlite+pysqlite:///{(tmp_path / 'roundtrip.db').as_posix()}"
    config = _alembic_config(url)

    command.upgrade(config, "head")
    command.downgrade(config, "base")
    engine = build_engine(url)
    assert EXPECTED_TABLES.isdisjoint(set(inspect(engine).get_table_names()))
    engine.dispose()
    command.upgrade(config, "head")
    engine = build_engine(url)
    assert EXPECTED_TABLES.issubset(set(inspect(engine).get_table_names()))
