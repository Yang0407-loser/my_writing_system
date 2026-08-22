"""Add append-only audited projection requeue evidence.

This audit table is deliberately separate from claim attempts, whose attempt
numbers and lease tokens must retain their original meaning.
"""

from alembic import op
import sqlalchemy as sa


revision = "0004_p3a_requeue_audit"
down_revision = "0003_p3a_projection_backfill"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "projection_requeue_audits",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("delivery_id", sa.String(36), nullable=False),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("projector_id", sa.String(100), nullable=False),
        sa.Column("prior_attempt_count", sa.Integer(), nullable=False),
        sa.Column("operator_id", sa.String(255), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["delivery_id"], ["projection_deliveries.id"]),
    )
    op.create_index(
        "ix_projection_requeue_audits_delivery",
        "projection_requeue_audits",
        ["delivery_id", "created_at"],
    )
    op.create_index(
        "ix_projection_requeue_audits_scope",
        "projection_requeue_audits",
        ["tenant_id", "project_id", "projector_id"],
    )


def downgrade() -> None:
    # P3A downgrades are only supported for empty/disposable test schemas.
    op.drop_table("projection_requeue_audits")
