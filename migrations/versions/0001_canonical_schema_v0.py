"""Create Canonical Schema v0 and explicit dual Heads."""

from alembic import op
import sqlalchemy as sa


revision = "0001_canonical_schema_v0"
down_revision = None
branch_labels = None
depends_on = None


UTC_NOW = sa.text("CURRENT_TIMESTAMP")


def _timestamps():
    return (
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=UTC_NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=UTC_NOW),
    )


def upgrade() -> None:
    op.create_table(
        "canonical_projects",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("owner_id", sa.String(36), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("current_state_version_id", sa.String(36), nullable=True),
        *_timestamps(),
    )
    op.create_index("ix_canonical_projects_tenant", "canonical_projects", ["tenant_id"])

    op.create_table(
        "canonical_documents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["project_id"], ["canonical_projects.id"], name="fk_document_project"),
        sa.UniqueConstraint("project_id", "id", name="uq_document_project_id"),
    )
    op.create_index("ix_canonical_documents_scope", "canonical_documents", ["tenant_id", "project_id"])

    op.create_table(
        "canonical_subsections",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("document_id", sa.String(36), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("legacy_section", sa.Integer(), nullable=True),
        sa.Column("legacy_subsection", sa.Integer(), nullable=True),
        sa.Column("current_revision_id", sa.String(36), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["project_id"], ["canonical_projects.id"], name="fk_subsection_project"),
        sa.ForeignKeyConstraint(["document_id"], ["canonical_documents.id"], name="fk_subsection_document"),
        sa.UniqueConstraint("document_id", "ordinal", name="uq_subsection_document_ordinal"),
        sa.UniqueConstraint("project_id", "id", name="uq_subsection_project_id"),
    )
    op.create_index(
        "ix_canonical_subsections_scope",
        "canonical_subsections",
        ["tenant_id", "project_id", "document_id"],
    )

    op.create_table(
        "canonical_commits",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("candidate_hash", sa.String(64), nullable=False),
        sa.Column("base_revision_number", sa.Integer(), nullable=False),
        sa.Column("base_state_version_id", sa.String(36), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=UTC_NOW),
        sa.ForeignKeyConstraint(["project_id"], ["canonical_projects.id"], name="fk_commit_project"),
        sa.CheckConstraint("status = 'committed'", name="ck_canonical_commit_status"),
    )
    op.create_index("ix_canonical_commits_scope", "canonical_commits", ["tenant_id", "project_id"])

    op.create_table(
        "canonical_state_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("commit_id", sa.String(36), nullable=True),
        sa.Column("origin", sa.String(20), nullable=False),
        sa.Column("parent_state_version_id", sa.String(36), nullable=True),
        sa.Column("transition_version", sa.String(100), nullable=False),
        sa.Column("schema_version", sa.String(100), nullable=False),
        sa.Column("state_json", sa.JSON(), nullable=False),
        sa.Column("state_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=UTC_NOW),
        sa.ForeignKeyConstraint(["project_id"], ["canonical_projects.id"], name="fk_state_project"),
        sa.ForeignKeyConstraint(["commit_id"], ["canonical_commits.id"], name="fk_state_commit"),
        sa.ForeignKeyConstraint(["parent_state_version_id"], ["canonical_state_versions.id"], name="fk_state_parent"),
        sa.UniqueConstraint("project_id", "id", name="uq_state_version_project_id"),
        sa.CheckConstraint(
            "(origin = 'genesis' AND commit_id IS NULL AND parent_state_version_id IS NULL) "
            "OR (origin = 'commit' AND commit_id IS NOT NULL AND parent_state_version_id IS NOT NULL)",
            name="ck_state_version_origin_shape",
        ),
    )
    op.create_index(
        "ix_canonical_state_versions_scope",
        "canonical_state_versions",
        ["tenant_id", "project_id"],
    )

    with op.batch_alter_table("canonical_commits") as batch:
        batch.create_foreign_key(
            "fk_commit_base_state",
            "canonical_state_versions",
            ["base_state_version_id"],
            ["id"],
        )

    op.create_table(
        "document_revisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("commit_id", sa.String(36), nullable=False),
        sa.Column("subsection_id", sa.String(36), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("parent_revision_id", sa.String(36), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("creator", sa.String(100), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=UTC_NOW),
        sa.ForeignKeyConstraint(["project_id"], ["canonical_projects.id"], name="fk_revision_project"),
        sa.ForeignKeyConstraint(["commit_id"], ["canonical_commits.id"], name="fk_revision_commit"),
        sa.ForeignKeyConstraint(["subsection_id"], ["canonical_subsections.id"], name="fk_revision_subsection"),
        sa.ForeignKeyConstraint(["parent_revision_id"], ["document_revisions.id"], name="fk_revision_parent"),
        sa.UniqueConstraint("subsection_id", "revision_number", name="uq_revision_number"),
        sa.UniqueConstraint("subsection_id", "id", name="uq_revision_subsection_id"),
    )
    op.create_index(
        "ix_document_revisions_scope",
        "document_revisions",
        ["tenant_id", "project_id", "subsection_id"],
    )

    with op.batch_alter_table("canonical_projects") as batch:
        batch.create_foreign_key(
            "fk_project_current_state_same_project",
            "canonical_state_versions",
            ["id", "current_state_version_id"],
            ["project_id", "id"],
        )
    with op.batch_alter_table("canonical_subsections") as batch:
        batch.create_foreign_key(
            "fk_subsection_current_revision_same_subsection",
            "document_revisions",
            ["id", "current_revision_id"],
            ["subsection_id", "id"],
        )

    op.create_table(
        "event_ledger",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("commit_id", sa.String(36), nullable=False),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("evidence_refs_json", sa.JSON(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=UTC_NOW),
        sa.ForeignKeyConstraint(["commit_id"], ["canonical_commits.id"], name="fk_ledger_commit"),
        sa.UniqueConstraint("commit_id", "ordinal", name="uq_event_ledger_commit_ordinal"),
    )
    op.create_index("ix_event_ledger_scope", "event_ledger", ["tenant_id", "project_id"])

    op.create_table(
        "idempotency_records",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("idempotency_key", sa.String(500), nullable=False),
        sa.Column("candidate_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("commit_id", sa.String(36), nullable=True),
        sa.Column("result_json", sa.JSON(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["commit_id"], ["canonical_commits.id"], name="fk_idempotency_commit"),
        sa.UniqueConstraint("tenant_id", "project_id", "idempotency_key", name="uq_idempotency_scope_key"),
        sa.CheckConstraint("status IN ('reserved', 'completed')", name="ck_idempotency_status"),
    )

    op.create_table(
        "outbox_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("commit_id", sa.String(36), nullable=False),
        sa.Column("projection_name", sa.String(100), nullable=False),
        sa.Column("barrier_kind", sa.String(20), nullable=False),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["commit_id"], ["canonical_commits.id"], name="fk_outbox_commit"),
        sa.UniqueConstraint("commit_id", "projection_name", name="uq_outbox_projection"),
        sa.CheckConstraint("barrier_kind IN ('critical', 'non_blocking')", name="ck_outbox_barrier_kind"),
        sa.CheckConstraint("status IN ('pending', 'processing', 'published', 'failed')", name="ck_outbox_status"),
    )
    op.create_index("ix_outbox_dispatch", "outbox_events", ["status", "available_at"])


def downgrade() -> None:
    op.drop_table("outbox_events")
    op.drop_table("idempotency_records")
    op.drop_table("event_ledger")
    with op.batch_alter_table("canonical_subsections") as batch:
        batch.drop_constraint("fk_subsection_current_revision_same_subsection", type_="foreignkey")
    with op.batch_alter_table("canonical_projects") as batch:
        batch.drop_constraint("fk_project_current_state_same_project", type_="foreignkey")
    op.drop_table("document_revisions")
    with op.batch_alter_table("canonical_commits") as batch:
        batch.drop_constraint("fk_commit_base_state", type_="foreignkey")
    op.drop_table("canonical_state_versions")
    op.drop_table("canonical_commits")
    op.drop_table("canonical_subsections")
    op.drop_table("canonical_documents")
    op.drop_table("canonical_projects")
