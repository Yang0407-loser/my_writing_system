"""Expand P2 Canonical storage with P3A projection control tables.

The nullable position columns remain nullable until the deterministic P2
backfill has assigned every existing committed row a project-local position.
"""

from alembic import op
import sqlalchemy as sa


revision = "0002_p3a_projection_expand"
down_revision = "0001_canonical_schema_v0"
branch_labels = None
depends_on = None


UTC_NOW = sa.text("CURRENT_TIMESTAMP")


def _timestamps():
    return (
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=UTC_NOW
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=UTC_NOW
        ),
    )


def upgrade() -> None:
    op.add_column(
        "canonical_projects", sa.Column("next_stream_position", sa.BigInteger(), nullable=True)
    )
    op.add_column(
        "canonical_commits", sa.Column("stream_position", sa.BigInteger(), nullable=True)
    )
    op.add_column(
        "outbox_events", sa.Column("stream_position", sa.BigInteger(), nullable=True)
    )

    op.create_table(
        "projection_deliveries",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("outbox_event_id", sa.String(36), nullable=False),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("projector_id", sa.String(100), nullable=False),
        sa.Column("projector_version", sa.String(100), nullable=False),
        sa.Column("barrier_kind", sa.String(20), nullable=False),
        sa.Column("stream_position", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_token", sa.String(64), nullable=True),
        sa.Column("leased_by", sa.String(255), nullable=True),
        sa.Column("leased_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(100), nullable=True),
        sa.Column("last_error_class", sa.String(255), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("receipt_json", sa.JSON(), nullable=True),
        sa.Column("receipt_digest", sa.String(64), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["outbox_event_id"], ["outbox_events.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["canonical_projects.id"]),
        sa.UniqueConstraint(
            "outbox_event_id", "projector_id", name="uq_projection_delivery_envelope_projector"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "projector_id",
            "stream_position",
            name="uq_projection_delivery_partition_position",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'published', 'dead_letter')",
            name="ck_projection_delivery_status",
        ),
        sa.CheckConstraint(
            "barrier_kind IN ('critical', 'non_blocking')",
            name="ck_projection_delivery_barrier_kind",
        ),
        sa.CheckConstraint(
            "status != 'processing' OR (lease_token IS NOT NULL AND leased_by IS NOT NULL "
            "AND leased_until IS NOT NULL)",
            name="ck_projection_delivery_processing_lease",
        ),
        sa.CheckConstraint(
            "status != 'published' OR (published_at IS NOT NULL AND receipt_json IS NOT NULL "
            "AND receipt_digest IS NOT NULL)",
            name="ck_projection_delivery_published_receipt",
        ),
        sa.CheckConstraint(
            "status != 'pending' OR (lease_token IS NULL AND leased_by IS NULL "
            "AND leased_until IS NULL)",
            name="ck_projection_delivery_pending_lease",
        ),
    )
    op.create_index(
        "ix_projection_deliveries_claim",
        "projection_deliveries",
        ["tenant_id", "project_id", "projector_id", "status", "available_at", "stream_position"],
    )

    op.create_table(
        "projection_attempts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("delivery_id", sa.String(36), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("lease_token", sa.String(64), nullable=False),
        sa.Column("leased_by", sa.String(255), nullable=False),
        sa.Column("trigger_source", sa.String(50), nullable=False),
        sa.Column("outcome", sa.String(30), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=UTC_NOW),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.Column("error_class", sa.String(255), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("retry_delay_seconds", sa.Integer(), nullable=True),
        sa.Column("operator_id", sa.String(255), nullable=True),
        sa.Column("operator_reason", sa.Text(), nullable=True),
        sa.Column("rebuild_run_id", sa.String(36), nullable=True),
        sa.ForeignKeyConstraint(["delivery_id"], ["projection_deliveries.id"]),
        sa.UniqueConstraint("delivery_id", "attempt_number", name="uq_projection_attempt_delivery_attempt"),
        sa.CheckConstraint(
            "outcome IN ('claimed', 'succeeded', 'retry_scheduled', 'lease_expired', "
            "'dead_lettered', 'requeued', 'superseded')",
            name="ck_projection_attempt_outcome",
        ),
    )
    op.create_index(
        "ix_projection_attempts_delivery", "projection_attempts", ["delivery_id", "attempt_number"]
    )

    op.create_table(
        "projection_partitions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("projector_id", sa.String(100), nullable=False),
        sa.Column("projector_version", sa.String(100), nullable=False),
        sa.Column("enrollment_status", sa.String(20), nullable=False, server_default="disabled"),
        sa.Column("runtime_status", sa.String(30), nullable=False, server_default="active"),
        sa.Column("last_published_position", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("last_published_event_id", sa.String(64), nullable=True),
        sa.Column("activation_after_position", sa.BigInteger(), nullable=True),
        sa.Column("active_rebuild_run_id", sa.String(36), nullable=True),
        sa.Column("maintenance_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("health_checked_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["project_id"], ["canonical_projects.id"]),
        sa.UniqueConstraint("tenant_id", "project_id", "projector_id", name="uq_projection_partition_scope"),
        sa.CheckConstraint(
            "enrollment_status IN ('disabled', 'bootstrapping', 'active')",
            name="ck_projection_partition_enrollment_status",
        ),
        sa.CheckConstraint(
            "runtime_status IN ('active', 'pause_requested', 'maintenance', 'catching_up')",
            name="ck_projection_partition_runtime_status",
        ),
    )
    op.create_index(
        "ix_projection_partitions_scope",
        "projection_partitions",
        ["tenant_id", "project_id", "projector_id"],
    )

    op.create_table(
        "projection_rebuild_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("projector_id", sa.String(100), nullable=False),
        sa.Column("projector_version", sa.String(100), nullable=False),
        sa.Column("run_kind", sa.String(30), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="requested"),
        sa.Column("watermark_position", sa.BigInteger(), nullable=False),
        sa.Column("watermark_commit_id", sa.String(36), nullable=True),
        sa.Column("watermark_revision_id", sa.String(36), nullable=True),
        sa.Column("watermark_state_version_id", sa.String(36), nullable=True),
        sa.Column("checkpoint_position", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("activation_after_position", sa.BigInteger(), nullable=True),
        sa.Column("lease_token", sa.String(64), nullable=True),
        sa.Column("leased_by", sa.String(255), nullable=True),
        sa.Column("leased_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expected_record_count", sa.Integer(), nullable=True),
        sa.Column("processed_record_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("expected_manifest_json", sa.JSON(), nullable=True),
        sa.Column("expected_manifest_digest", sa.String(64), nullable=True),
        sa.Column("actual_manifest_json", sa.JSON(), nullable=True),
        sa.Column("actual_manifest_digest", sa.String(64), nullable=True),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("operator_id", sa.String(255), nullable=True),
        sa.Column("operator_reason", sa.Text(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["project_id"], ["canonical_projects.id"]),
        sa.CheckConstraint(
            "run_kind IN ('maintenance', 'projector_bootstrap')",
            name="ck_projection_rebuild_run_kind",
        ),
        sa.CheckConstraint(
            "status IN ('requested', 'pausing', 'clearing', 'rebuilding', 'reconciling', "
            "'catching_up', 'completed', 'failed', 'reconciliation_failed')",
            name="ck_projection_rebuild_status",
        ),
    )
    op.create_index(
        "ux_projection_rebuild_runs_active_scope",
        "projection_rebuild_runs",
        ["tenant_id", "project_id", "projector_id"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('requested', 'pausing', 'clearing', 'rebuilding', "
            "'reconciling', 'catching_up')"
        ),
    )
    op.create_index(
        "ix_projection_rebuild_runs_scope",
        "projection_rebuild_runs",
        ["tenant_id", "project_id", "projector_id", "status"],
    )

    op.create_table(
        "projection_reconciliations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("rebuild_run_id", sa.String(36), nullable=False),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("projector_id", sa.String(100), nullable=False),
        sa.Column("watermark_position", sa.BigInteger(), nullable=False),
        sa.Column("expected_manifest_json", sa.JSON(), nullable=False),
        sa.Column("actual_manifest_json", sa.JSON(), nullable=False),
        sa.Column("expected_digest", sa.String(64), nullable=False),
        sa.Column("actual_digest", sa.String(64), nullable=False),
        sa.Column("diff_summary_json", sa.JSON(), nullable=False),
        sa.Column("reconciled_at", sa.DateTime(timezone=True), nullable=False, server_default=UTC_NOW),
        *_timestamps(),
        sa.ForeignKeyConstraint(["rebuild_run_id"], ["projection_rebuild_runs.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["canonical_projects.id"]),
        sa.UniqueConstraint("rebuild_run_id", name="uq_projection_reconciliation_run"),
    )
    op.create_index(
        "ix_projection_reconciliations_scope",
        "projection_reconciliations",
        ["tenant_id", "project_id", "projector_id"],
    )

    op.create_table(
        "projection_analytics_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("projection_event_id", sa.String(64), nullable=False),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("projector_id", sa.String(100), nullable=False),
        sa.Column("stream_position", sa.BigInteger(), nullable=False),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["project_id"], ["canonical_projects.id"]),
        sa.UniqueConstraint("projection_event_id", name="uq_projection_analytics_event_identity"),
    )
    op.create_index(
        "ix_projection_analytics_events_scope",
        "projection_analytics_events",
        ["tenant_id", "project_id"],
    )


def downgrade() -> None:
    op.drop_table("projection_analytics_events")
    op.drop_table("projection_reconciliations")
    op.drop_table("projection_rebuild_runs")
    op.drop_table("projection_partitions")
    op.drop_table("projection_attempts")
    op.drop_table("projection_deliveries")
    op.drop_column("outbox_events", "stream_position")
    op.drop_column("canonical_commits", "stream_position")
    op.drop_column("canonical_projects", "next_stream_position")
