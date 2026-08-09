from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text


pytestmark = pytest.mark.postgres


P2_ROWS = [
    ("legacy_world_event", "published", 1, None),
    ("handover_context", "failed", 2, "redis unavailable"),
    ("chroma_story_chunks", "pending", 0, None),
]

BASELINE_PROJECTORS = {
    "legacy_world_event",
    "handover_context",
    "chroma_story_chunks",
    "redis_stream",
    "task_preview",
    "markdown_export",
    "analytics",
}


def _migration_config(database_url: str) -> Config:
    root = Path(__file__).resolve().parents[3]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _reset_to_p2(database_url: str) -> Config:
    config = _migration_config(database_url)
    command.downgrade(config, "0001_canonical_schema_v0")
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(text("DROP SCHEMA public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))
    finally:
        engine.dispose()
    command.upgrade(config, "0001_canonical_schema_v0")
    return config


def _id() -> str:
    return str(uuid4())


def _insert_p2_fixture(database_url: str) -> dict[str, str]:
    ids = {
        "tenant": _id(),
        "project": _id(),
        "genesis": _id(),
        "commit_one": _id(),
        "state_one": _id(),
        "commit_two": _id(),
        "state_two": _id(),
        "published": _id(),
        "failed": _id(),
        "pending": _id(),
        "processing": _id(),
    }
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO canonical_projects "
                    "(id, tenant_id, owner_id, name) "
                    "VALUES (:project, :tenant, 'owner', 'P2 migration fixture')"
                ),
                ids,
            )
            connection.execute(
                text(
                    "INSERT INTO canonical_state_versions "
                    "(id, tenant_id, project_id, commit_id, origin, parent_state_version_id, "
                    "transition_version, schema_version, state_json, state_hash, created_at) "
                    "VALUES (:genesis, :tenant, :project, NULL, 'genesis', NULL, 'p2', 'v0', "
                    "CAST('{}' AS json), :hash, TIMESTAMPTZ '2026-01-03 00:00:00+00')"
                ),
                {**ids, "hash": "a" * 64},
            )
            connection.execute(
                text(
                    "INSERT INTO canonical_commits "
                    "(id, tenant_id, project_id, candidate_hash, base_revision_number, "
                    "base_state_version_id, status, created_at) "
                    "VALUES (:commit_one, :tenant, :project, :hash, 0, :genesis, 'committed', "
                    "TIMESTAMPTZ '2026-01-02 00:00:00+00')"
                ),
                {**ids, "hash": "b" * 64},
            )
            connection.execute(
                text(
                    "INSERT INTO canonical_state_versions "
                    "(id, tenant_id, project_id, commit_id, origin, parent_state_version_id, "
                    "transition_version, schema_version, state_json, state_hash, created_at) "
                    "VALUES (:state_one, :tenant, :project, :commit_one, 'commit', :genesis, "
                    "'p2', 'v0', CAST('{}' AS json), :hash, "
                    "TIMESTAMPTZ '2026-01-04 00:00:00+00')"
                ),
                {**ids, "hash": "c" * 64},
            )
            connection.execute(
                text(
                    "INSERT INTO canonical_commits "
                    "(id, tenant_id, project_id, candidate_hash, base_revision_number, "
                    "base_state_version_id, status, created_at) "
                    "VALUES (:commit_two, :tenant, :project, :hash, 0, :state_one, 'committed', "
                    "TIMESTAMPTZ '2026-01-01 00:00:00+00')"
                ),
                {**ids, "hash": "d" * 64},
            )
            connection.execute(
                text(
                    "INSERT INTO canonical_state_versions "
                    "(id, tenant_id, project_id, commit_id, origin, parent_state_version_id, "
                    "transition_version, schema_version, state_json, state_hash, created_at) "
                    "VALUES (:state_two, :tenant, :project, :commit_two, 'commit', :state_one, "
                    "'p2', 'v0', CAST('{}' AS json), :hash, "
                    "TIMESTAMPTZ '2026-01-01 00:00:00+00')"
                ),
                {**ids, "hash": "e" * 64},
            )
            connection.execute(
                text(
                    "UPDATE canonical_projects SET current_state_version_id=:state_two "
                    "WHERE id=:project"
                ),
                ids,
            )
            for index, (projector, status, attempts, last_error) in enumerate(P2_ROWS):
                connection.execute(
                    text(
                        "INSERT INTO outbox_events "
                        "(id, tenant_id, project_id, commit_id, projection_name, barrier_kind, "
                        "event_type, payload_json, status, attempts, available_at, published_at, "
                        "last_error) "
                        "VALUES (:id, :tenant, :project, :commit_id, :projector, :barrier, "
                        "'commit.created', CAST('{}' AS json), :status, :attempts, "
                        "TIMESTAMPTZ '2026-01-05 00:00:00+00', :published_at, :last_error)"
                    ),
                    {
                        "id": (ids["published"], ids["failed"], ids["pending"])[index],
                        "tenant": ids["tenant"],
                        "project": ids["project"],
                        "commit_id": ids["commit_one"] if index == 0 else ids["commit_two"],
                        "projector": projector,
                        "barrier": "critical",
                        "status": status,
                        "attempts": attempts,
                        "published_at": (
                            "2026-01-05 00:00:00+00" if status == "published" else None
                        ),
                        "last_error": last_error,
                    },
                )
            connection.execute(
                text(
                    "INSERT INTO outbox_events "
                    "(id, tenant_id, project_id, commit_id, projection_name, barrier_kind, "
                    "event_type, payload_json, status, attempts, available_at, last_error) "
                    "VALUES (:processing, :tenant, :project, :commit_two, 'redis_stream', "
                    "'non_blocking', 'commit.created', CAST('{}' AS json), 'processing', 4, "
                    "TIMESTAMPTZ '2026-01-05 00:00:00+00', 'worker disappeared')"
                ),
                ids,
            )
    finally:
        engine.dispose()
    return ids


def test_postgres_backfill_uses_state_chain_and_preserves_p2_delivery_evidence(
    postgres_database_url,
):
    config = _reset_to_p2(postgres_database_url)
    ids = _insert_p2_fixture(postgres_database_url)

    command.upgrade(config, "head")

    engine = create_engine(postgres_database_url)
    try:
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0003_p3a_projection_backfill"
            )
            commits = connection.execute(
                text(
                    "SELECT id, stream_position FROM canonical_commits "
                    "WHERE project_id=:project ORDER BY stream_position"
                ),
                ids,
            ).all()
            assert commits == [(ids["commit_one"], 1), (ids["commit_two"], 2)]
            assert connection.scalar(
                text(
                    "SELECT next_stream_position FROM canonical_projects WHERE id=:project"
                ),
                ids,
            ) == 2

            envelope_count = connection.scalar(
                text("SELECT count(*) FROM outbox_events WHERE project_id=:project"), ids
            )
            delivery_count = connection.scalar(
                text("SELECT count(*) FROM projection_deliveries WHERE project_id=:project"),
                ids,
            )
            assert envelope_count == 4
            assert delivery_count == envelope_count
            assert connection.scalar(
                text(
                    "SELECT count(*) FROM projection_deliveries d "
                    "JOIN outbox_events e ON e.id=d.outbox_event_id "
                    "WHERE e.project_id=:project"
                ),
                ids,
            ) == envelope_count
            assert connection.scalar(
                text(
                    "SELECT count(DISTINCT d.outbox_event_id) FROM projection_deliveries d "
                    "WHERE d.project_id=:project"
                ),
                ids,
            ) == envelope_count

            deliveries = {
                row.projector_id: row
                for row in connection.execute(
                    text(
                        "SELECT projector_id, status, attempt_count, last_error_message, "
                        "receipt_json, receipt_digest, stream_position "
                        "FROM projection_deliveries WHERE project_id=:project"
                    ),
                    ids,
                ).mappings()
            }
            assert deliveries["legacy_world_event"]["status"] == "published"
            assert deliveries["legacy_world_event"]["receipt_json"] == {
                "kind": "p2_migration_receipt",
                "outbox_event_id": ids["published"],
                "projector_id": "legacy_world_event",
                "stream_position": 1,
            }
            assert deliveries["legacy_world_event"]["receipt_digest"] is not None
            assert deliveries["handover_context"] == {
                "projector_id": "handover_context",
                "status": "pending",
                "attempt_count": 2,
                "last_error_message": "redis unavailable",
                "receipt_json": None,
                "receipt_digest": None,
                "stream_position": 2,
            }
            assert deliveries["chroma_story_chunks"]["status"] == "pending"
            assert deliveries["redis_stream"] == {
                "projector_id": "redis_stream",
                "status": "pending",
                "attempt_count": 4,
                "last_error_message": "worker disappeared",
                "receipt_json": None,
                "receipt_digest": None,
                "stream_position": 2,
            }

            partitions = {
                row.projector_id: row
                for row in connection.execute(
                    text(
                        "SELECT projector_id, enrollment_status, activation_after_position, "
                        "last_published_position FROM projection_partitions "
                        "WHERE project_id=:project"
                    ),
                    ids,
                ).mappings()
            }
            assert set(partitions) == BASELINE_PROJECTORS
            assert all(
                row["enrollment_status"] == "active"
                and row["activation_after_position"] == 0
                for row in partitions.values()
            )
            assert partitions["legacy_world_event"]["last_published_position"] == 1
            assert partitions["handover_context"]["last_published_position"] == 0
            assert partitions["chroma_story_chunks"]["last_published_position"] == 0
            assert partitions["redis_stream"]["last_published_position"] == 0
    finally:
        engine.dispose()


def test_postgres_empty_schema_can_downgrade_to_p2(postgres_database_url):
    config = _reset_to_p2(postgres_database_url)
    command.upgrade(config, "head")
    command.downgrade(config, "0001_canonical_schema_v0")

    engine = create_engine(postgres_database_url)
    try:
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0001_canonical_schema_v0"
            )
            assert connection.scalar(
                text("SELECT to_regclass('public.projection_deliveries')")
            ) is None
    finally:
        engine.dispose()
    command.upgrade(config, "head")
