"""Backfill P2 envelopes into P3A deliveries in canonical state-chain order.

This is an expand-only operational migration.  Its downgrade is for empty test
schemas only; production rollback keeps the P3A tables and delivery evidence.
"""

from alembic import op
import sqlalchemy as sa


revision = "0003_p3a_projection_backfill"
down_revision = "0002_p3a_projection_expand"
branch_labels = None
depends_on = None


_VALIDATE_LINEAR_STATE_CHAINS = """
DO $$
DECLARE
    invalid_count bigint;
BEGIN
    SELECT count(*) INTO invalid_count
    FROM (
        SELECT project_id
        FROM canonical_state_versions
        GROUP BY project_id
        HAVING count(*) FILTER (WHERE origin = 'genesis') <> 1
    ) invalid_projects;
    IF invalid_count > 0 THEN
        RAISE EXCEPTION 'P3A backfill requires exactly one genesis state per project';
    END IF;

    SELECT count(*) INTO invalid_count
    FROM (
        SELECT parent_state_version_id
        FROM canonical_state_versions
        WHERE parent_state_version_id IS NOT NULL
        GROUP BY parent_state_version_id
        HAVING count(*) > 1
    ) branching_states;
    IF invalid_count > 0 THEN
        RAISE EXCEPTION 'P3A backfill requires a linear Canon state chain per project';
    END IF;

    WITH RECURSIVE state_chain AS (
        SELECT project_id, id, commit_id, 0::bigint AS position
        FROM canonical_state_versions
        WHERE origin = 'genesis'
        UNION ALL
        SELECT child.project_id, child.id, child.commit_id, parent.position + 1
        FROM state_chain parent
        JOIN canonical_state_versions child
          ON child.project_id = parent.project_id
         AND child.parent_state_version_id = parent.id
    )
    SELECT count(*) INTO invalid_count
    FROM canonical_state_versions state
    LEFT JOIN state_chain chain ON chain.id = state.id
    WHERE state.origin = 'commit' AND chain.id IS NULL;
    IF invalid_count > 0 THEN
        RAISE EXCEPTION 'P3A backfill found a committed state outside its project chain';
    END IF;

    SELECT count(*) INTO invalid_count
    FROM (
        SELECT canon_commit.id
        FROM canonical_commits canon_commit
        LEFT JOIN canonical_state_versions state
          ON state.project_id = canon_commit.project_id
         AND state.commit_id = canon_commit.id
         AND state.origin = 'commit'
        GROUP BY canon_commit.id
        HAVING count(state.id) <> 1
    ) invalid_commits;
    IF invalid_count > 0 THEN
        RAISE EXCEPTION 'P3A backfill requires every committed Canon commit on one state chain';
    END IF;
END $$;
"""


_BACKFILL_POSITIONS = """
WITH RECURSIVE state_chain AS (
  SELECT project_id, id, commit_id, 0::bigint AS position
  FROM canonical_state_versions
  WHERE origin = 'genesis'
  UNION ALL
  SELECT child.project_id, child.id, child.commit_id, parent.position + 1
  FROM state_chain parent
  JOIN canonical_state_versions child
    ON child.project_id = parent.project_id
   AND child.parent_state_version_id = parent.id
)
UPDATE canonical_commits AS commit
SET stream_position = chain.position
FROM state_chain AS chain
WHERE chain.commit_id = commit.id;
"""


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text(_VALIDATE_LINEAR_STATE_CHAINS))
    bind.execute(sa.text(_BACKFILL_POSITIONS))
    bind.execute(
        sa.text(
            "UPDATE outbox_events envelope SET stream_position = commit.stream_position "
            "FROM canonical_commits commit WHERE commit.id = envelope.commit_id"
        )
    )
    bind.execute(
        sa.text(
            "INSERT INTO projection_deliveries ("
            "id, outbox_event_id, tenant_id, project_id, projector_id, projector_version, "
            "barrier_kind, stream_position, status, available_at, attempt_count, "
            "last_attempt_at, last_error_message, published_at, receipt_json, receipt_digest, "
            "created_at, updated_at"
            ") "
            "SELECT md5('p3a-delivery:' || envelope.id || ':' || envelope.projection_name), "
            "envelope.id, envelope.tenant_id, envelope.project_id, envelope.projection_name, "
            "'v1', envelope.barrier_kind, envelope.stream_position, "
            "CASE WHEN envelope.status = 'published' THEN 'published' ELSE 'pending' END, "
            "envelope.available_at, envelope.attempts, "
            "CASE WHEN envelope.attempts > 0 THEN envelope.updated_at ELSE NULL END, "
            "envelope.last_error, "
            "CASE WHEN envelope.status = 'published' THEN COALESCE("
            "envelope.published_at, envelope.updated_at) ELSE NULL END, "
            "CASE WHEN envelope.status = 'published' THEN json_build_object("
            "'kind', 'p2_migration_receipt', 'outbox_event_id', envelope.id, "
            "'projector_id', envelope.projection_name, 'stream_position', envelope.stream_position"
            ") ELSE NULL END, "
            "CASE WHEN envelope.status = 'published' THEN md5("
            "'p2_migration_receipt:' || envelope.id || ':' || envelope.projection_name || ':' "
            "|| envelope.stream_position::text) ELSE NULL END, "
            "envelope.created_at, envelope.updated_at "
            "FROM outbox_events envelope"
        )
    )
    bind.execute(
        sa.text(
            "UPDATE canonical_projects project SET next_stream_position = COALESCE("
            "(SELECT max(commit.stream_position) FROM canonical_commits commit "
            "WHERE commit.project_id = project.id), 0)"
        )
    )
    bind.execute(
        sa.text(
            "INSERT INTO projection_partitions ("
            "id, tenant_id, project_id, projector_id, projector_version, enrollment_status, "
            "runtime_status, last_published_position, last_published_event_id, "
            "activation_after_position, created_at, updated_at"
            ") "
            "SELECT md5('p3a-partition:' || project.id || ':' || projector.projector_id), "
            "project.tenant_id, project.id, projector.projector_id, 'v1', 'active', 'active', "
            "COALESCE((SELECT min(position) - 1 FROM generate_series(1, project.next_stream_position) "
            "AS position LEFT JOIN projection_deliveries delivery "
            "ON delivery.project_id = project.id "
            "AND delivery.projector_id = projector.projector_id "
            "AND delivery.stream_position = position "
            "WHERE delivery.status IS DISTINCT FROM 'published'), project.next_stream_position), "
            "NULL, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP "
            "FROM canonical_projects project CROSS JOIN (VALUES "
            "('legacy_world_event'), ('handover_context'), ('chroma_story_chunks'), "
            "('redis_stream'), ('task_preview'), ('markdown_export'), ('analytics')"
            ") AS projector(projector_id)"
        )
    )
    bind.execute(
        sa.text(
            "UPDATE projection_partitions partition SET last_published_event_id = delivery.outbox_event_id "
            "FROM projection_deliveries delivery WHERE delivery.project_id = partition.project_id "
            "AND delivery.projector_id = partition.projector_id "
            "AND delivery.stream_position = partition.last_published_position "
            "AND partition.last_published_position > 0"
        )
    )

    op.alter_column(
        "canonical_projects", "next_stream_position", nullable=False, server_default="1"
    )
    op.alter_column("canonical_commits", "stream_position", nullable=False)
    op.alter_column("outbox_events", "stream_position", nullable=False)
    op.create_unique_constraint(
        "uq_canonical_commit_project_stream_position",
        "canonical_commits",
        ["project_id", "stream_position"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_canonical_commit_project_stream_position",
        "canonical_commits",
        type_="unique",
    )
    op.alter_column("outbox_events", "stream_position", nullable=True)
    op.alter_column("canonical_commits", "stream_position", nullable=True)
    op.alter_column(
        "canonical_projects", "next_stream_position", nullable=True, server_default=None
    )
