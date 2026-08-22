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
        "legacy_pending": _id(),
        "redis_published": _id(),
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
            connection.execute(
                text(
                    "INSERT INTO outbox_events "
                    "(id, tenant_id, project_id, commit_id, projection_name, barrier_kind, "
                    "event_type, payload_json, status, attempts, available_at, published_at) "
                    "VALUES (:redis_published, :tenant, :project, :commit_one, 'redis_stream', "
                    "'non_blocking', 'commit.created', CAST('{}' AS json), 'published', 1, "
                    "TIMESTAMPTZ '2026-01-05 00:00:00+00', "
                    "TIMESTAMPTZ '2026-01-05 00:00:00+00')"
                ),
                ids,
            )
            connection.execute(
                text(
                    "INSERT INTO outbox_events "
                    "(id, tenant_id, project_id, commit_id, projection_name, barrier_kind, "
                    "event_type, payload_json, status, attempts, available_at) "
                    "VALUES (:legacy_pending, :tenant, :project, :commit_two, "
                    "'legacy_world_event', 'critical', 'commit.created', CAST('{}' AS json), "
                    "'pending', 0, TIMESTAMPTZ '2026-01-05 00:00:00+00')"
                ),
                ids,
            )
    finally:
        engine.dispose()
    return ids


def _insert_project(connection, project_id: str, tenant_id: str) -> None:
    connection.execute(
        text(
            "INSERT INTO canonical_projects (id, tenant_id, owner_id, name) "
            "VALUES (:project_id, :tenant_id, 'owner', 'invalid chain fixture')"
        ),
        {"project_id": project_id, "tenant_id": tenant_id},
    )


def _insert_genesis(connection, state_id: str, tenant_id: str, project_id: str) -> None:
    connection.execute(
        text(
            "INSERT INTO canonical_state_versions "
            "(id, tenant_id, project_id, commit_id, origin, parent_state_version_id, "
            "transition_version, schema_version, state_json, state_hash) "
            "VALUES (:state_id, :tenant_id, :project_id, NULL, 'genesis', NULL, "
            "'p2', 'v0', CAST('{}' AS json), :hash)"
        ),
        {
            "state_id": state_id,
            "tenant_id": tenant_id,
            "project_id": project_id,
            "hash": "f" * 64,
        },
    )


def _insert_commit(
    connection,
    commit_id: str,
    tenant_id: str,
    project_id: str,
    base_state_version_id: str,
) -> None:
    connection.execute(
        text(
            "INSERT INTO canonical_commits "
            "(id, tenant_id, project_id, candidate_hash, base_revision_number, "
            "base_state_version_id, status) "
            "VALUES (:commit_id, :tenant_id, :project_id, :hash, 0, "
            ":base_state_version_id, 'committed')"
        ),
        {
            "commit_id": commit_id,
            "tenant_id": tenant_id,
            "project_id": project_id,
            "base_state_version_id": base_state_version_id,
            "hash": "e" * 64,
        },
    )


def _insert_commit_state(
    connection,
    state_id: str,
    tenant_id: str,
    project_id: str,
    commit_id: str,
    parent_state_version_id: str,
) -> None:
    connection.execute(
        text(
            "INSERT INTO canonical_state_versions "
            "(id, tenant_id, project_id, commit_id, origin, parent_state_version_id, "
            "transition_version, schema_version, state_json, state_hash) "
            "VALUES (:state_id, :tenant_id, :project_id, :commit_id, 'commit', "
            ":parent_state_version_id, 'p2', 'v0', CAST('{}' AS json), :hash)"
        ),
        {
            "state_id": state_id,
            "tenant_id": tenant_id,
            "project_id": project_id,
            "commit_id": commit_id,
            "parent_state_version_id": parent_state_version_id,
            "hash": "d" * 64,
        },
    )


def _insert_invalid_chain_fixture(database_url: str, scenario: str) -> None:
    tenant_id = _id()
    project_id = _id()
    other_project_id = _id()
    genesis_id = _id()
    other_genesis_id = _id()
    commit_id = _id()
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            _insert_project(connection, project_id, tenant_id)
            if scenario == "missing_genesis":
                _insert_project(connection, other_project_id, tenant_id)
                _insert_genesis(connection, other_genesis_id, tenant_id, other_project_id)
                _insert_commit(
                    connection, commit_id, tenant_id, project_id, other_genesis_id
                )
                _insert_commit_state(
                    connection,
                    _id(),
                    tenant_id,
                    project_id,
                    commit_id,
                    other_genesis_id,
                )
            else:
                _insert_genesis(connection, genesis_id, tenant_id, project_id)
                _insert_commit(connection, commit_id, tenant_id, project_id, genesis_id)
                if scenario == "branching":
                    second_commit_id = _id()
                    _insert_commit(
                        connection,
                        second_commit_id,
                        tenant_id,
                        project_id,
                        genesis_id,
                    )
                    _insert_commit_state(
                        connection, _id(), tenant_id, project_id, commit_id, genesis_id
                    )
                    _insert_commit_state(
                        connection,
                        _id(),
                        tenant_id,
                        project_id,
                        second_commit_id,
                        genesis_id,
                    )
                elif scenario == "disconnected":
                    _insert_project(connection, other_project_id, tenant_id)
                    _insert_genesis(connection, other_genesis_id, tenant_id, other_project_id)
                    _insert_commit_state(
                        connection,
                        _id(),
                        tenant_id,
                        project_id,
                        commit_id,
                        other_genesis_id,
                    )
                elif scenario == "duplicate_commit_state":
                    first_state_id = _id()
                    _insert_commit_state(
                        connection,
                        first_state_id,
                        tenant_id,
                        project_id,
                        commit_id,
                        genesis_id,
                    )
                    _insert_commit_state(
                        connection,
                        _id(),
                        tenant_id,
                        project_id,
                        commit_id,
                        first_state_id,
                    )
                elif scenario != "missing_commit_state":
                    raise AssertionError(f"unsupported invalid chain scenario: {scenario}")
    finally:
        engine.dispose()


def _assert_backfill_aborts_without_p3a_authority(
    database_url: str, config: Config, error_message: str
) -> None:
    with pytest.raises(Exception, match=error_message):
        command.upgrade(config, "head")

    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0001_canonical_schema_v0"
            )
            assert connection.scalar(
                text("SELECT to_regclass('public.projection_deliveries')")
            ) is None
            assert connection.scalar(
                text("SELECT to_regclass('public.projection_partitions')")
            ) is None
            assert connection.scalar(
                text(
                    "SELECT count(*) FROM information_schema.columns "
                    "WHERE table_name='canonical_commits' AND column_name='stream_position'"
                )
            ) == 0
    finally:
        engine.dispose()


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
                "0004_p3a_requeue_audit"
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
            assert envelope_count == 6
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
                (row.projector_id, row.stream_position): row
                for row in connection.execute(
                    text(
                        "SELECT projector_id, status, attempt_count, last_error_message, "
                        "receipt_json, receipt_digest, stream_position "
                        "FROM projection_deliveries WHERE project_id=:project"
                    ),
                    ids,
                ).mappings()
            }
            assert deliveries[("legacy_world_event", 1)]["status"] == "published"
            assert deliveries[("legacy_world_event", 1)]["receipt_json"] == {
                "kind": "p2_migration_receipt",
                "outbox_event_id": ids["published"],
                "projector_id": "legacy_world_event",
                "stream_position": 1,
            }
            assert deliveries[("legacy_world_event", 1)]["receipt_digest"] is not None
            assert deliveries[("legacy_world_event", 2)]["status"] == "pending"
            assert deliveries[("handover_context", 2)] == {
                "projector_id": "handover_context",
                "status": "pending",
                "attempt_count": 2,
                "last_error_message": "redis unavailable",
                "receipt_json": None,
                "receipt_digest": None,
                "stream_position": 2,
            }
            assert deliveries[("chroma_story_chunks", 2)]["status"] == "pending"
            assert deliveries[("redis_stream", 1)]["status"] == "published"
            assert deliveries[("redis_stream", 2)] == {
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
            assert partitions["redis_stream"]["last_published_position"] == 1
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("scenario", "error_message"),
    [
        ("missing_genesis", "exactly one genesis state per project"),
        ("branching", "requires a linear Canon state chain per project"),
        ("disconnected", "committed state outside its project chain"),
        ("missing_commit_state", "every committed Canon commit on one state chain"),
        ("duplicate_commit_state", "every committed Canon commit on one state chain"),
    ],
)
def test_postgres_backfill_rejects_invalid_canon_chains_without_p3a_authority(
    postgres_database_url, scenario, error_message
):
    config = _reset_to_p2(postgres_database_url)
    _insert_invalid_chain_fixture(postgres_database_url, scenario)

    _assert_backfill_aborts_without_p3a_authority(
        postgres_database_url, config, error_message
    )


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
