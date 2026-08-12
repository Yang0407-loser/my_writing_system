"""Canonical Schema v0 SQLAlchemy mappings.

Alembic migrations own physical schema creation; this metadata is for typed
runtime access and migration comparison only.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    BigInteger,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class CanonicalProject(TimestampMixin, Base):
    __tablename__ = "canonical_projects"
    __table_args__ = (
        ForeignKeyConstraint(
            ["id", "current_state_version_id"],
            ["canonical_state_versions.project_id", "canonical_state_versions.id"],
            name="fk_project_current_state_same_project",
            use_alter=True,
        ),
        Index("ix_canonical_projects_tenant", "tenant_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    owner_id: Mapped[str] = mapped_column(String(36), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    current_state_version_id: Mapped[str | None] = mapped_column(String(36))
    next_stream_position: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=1
    )


class CanonicalDocument(TimestampMixin, Base):
    __tablename__ = "canonical_documents"
    __table_args__ = (
        UniqueConstraint("project_id", "id", name="uq_document_project_id"),
        Index("ix_canonical_documents_scope", "tenant_id", "project_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("canonical_projects.id"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)


class CanonicalSubsection(TimestampMixin, Base):
    __tablename__ = "canonical_subsections"
    __table_args__ = (
        UniqueConstraint("document_id", "ordinal", name="uq_subsection_document_ordinal"),
        UniqueConstraint("project_id", "id", name="uq_subsection_project_id"),
        ForeignKeyConstraint(
            ["id", "current_revision_id"],
            ["document_revisions.subsection_id", "document_revisions.id"],
            name="fk_subsection_current_revision_same_subsection",
            use_alter=True,
        ),
        Index("ix_canonical_subsections_scope", "tenant_id", "project_id", "document_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("canonical_projects.id"), nullable=False
    )
    document_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("canonical_documents.id"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    legacy_section: Mapped[int | None] = mapped_column(Integer)
    legacy_subsection: Mapped[int | None] = mapped_column(Integer)
    current_revision_id: Mapped[str | None] = mapped_column(String(36))


class CanonicalCommit(Base):
    __tablename__ = "canonical_commits"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "stream_position",
            name="uq_canonical_commit_project_stream_position",
        ),
        CheckConstraint("status = 'committed'", name="ck_canonical_commit_status"),
        Index("ix_canonical_commits_scope", "tenant_id", "project_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("canonical_projects.id"), nullable=False
    )
    candidate_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    base_revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    base_state_version_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("canonical_state_versions.id"), nullable=False
    )
    stream_position: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="committed")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class CanonicalStateVersion(Base):
    __tablename__ = "canonical_state_versions"
    __table_args__ = (
        UniqueConstraint("project_id", "id", name="uq_state_version_project_id"),
        CheckConstraint(
            "(origin = 'genesis' AND commit_id IS NULL AND parent_state_version_id IS NULL) "
            "OR (origin = 'commit' AND commit_id IS NOT NULL AND parent_state_version_id IS NOT NULL)",
            name="ck_state_version_origin_shape",
        ),
        Index("ix_canonical_state_versions_scope", "tenant_id", "project_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("canonical_projects.id"), nullable=False
    )
    commit_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("canonical_commits.id")
    )
    origin: Mapped[str] = mapped_column(String(20), nullable=False)
    parent_state_version_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("canonical_state_versions.id")
    )
    transition_version: Mapped[str] = mapped_column(String(100), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(100), nullable=False)
    state_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    state_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class DocumentRevision(Base):
    __tablename__ = "document_revisions"
    __table_args__ = (
        UniqueConstraint("subsection_id", "revision_number", name="uq_revision_number"),
        UniqueConstraint("subsection_id", "id", name="uq_revision_subsection_id"),
        Index("ix_document_revisions_scope", "tenant_id", "project_id", "subsection_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("canonical_projects.id"), nullable=False
    )
    commit_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("canonical_commits.id"), nullable=False
    )
    subsection_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("canonical_subsections.id"), nullable=False
    )
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_revision_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("document_revisions.id")
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    creator: Mapped[str] = mapped_column(String(100), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class EventLedger(Base):
    __tablename__ = "event_ledger"
    __table_args__ = (
        UniqueConstraint("commit_id", "ordinal", name="uq_event_ledger_commit_ordinal"),
        Index("ix_event_ledger_scope", "tenant_id", "project_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    project_id: Mapped[str] = mapped_column(String(36), nullable=False)
    commit_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("canonical_commits.id"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    evidence_refs_json: Mapped[list[Any]] = mapped_column(JSON, nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class IdempotencyRecord(TimestampMixin, Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "project_id", "idempotency_key", name="uq_idempotency_scope_key"
        ),
        CheckConstraint(
            "status IN ('reserved', 'completed')", name="ck_idempotency_status"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    project_id: Mapped[str] = mapped_column(String(36), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(500), nullable=False)
    candidate_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    commit_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("canonical_commits.id")
    )
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class OutboxEvent(TimestampMixin, Base):
    __tablename__ = "outbox_events"
    __table_args__ = (
        UniqueConstraint("commit_id", "projection_name", name="uq_outbox_projection"),
        CheckConstraint(
            "barrier_kind IN ('critical', 'non_blocking')", name="ck_outbox_barrier_kind"
        ),
        CheckConstraint(
            "status IN ('pending', 'processing', 'published', 'failed')",
            name="ck_outbox_status",
        ),
        Index("ix_outbox_dispatch", "status", "available_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    project_id: Mapped[str] = mapped_column(String(36), nullable=False)
    commit_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("canonical_commits.id"), nullable=False
    )
    projection_name: Mapped[str] = mapped_column(String(100), nullable=False)
    barrier_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    stream_position: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)


class ProjectionDelivery(TimestampMixin, Base):
    """Mutable PostgreSQL scheduling state for one immutable Outbox envelope."""

    __tablename__ = "projection_deliveries"
    __table_args__ = (
        UniqueConstraint(
            "outbox_event_id",
            "projector_id",
            name="uq_projection_delivery_envelope_projector",
        ),
        UniqueConstraint(
            "tenant_id",
            "project_id",
            "projector_id",
            "stream_position",
            name="uq_projection_delivery_partition_position",
        ),
        CheckConstraint(
            "status IN ('pending', 'processing', 'published', 'dead_letter')",
            name="ck_projection_delivery_status",
        ),
        CheckConstraint(
            "barrier_kind IN ('critical', 'non_blocking')",
            name="ck_projection_delivery_barrier_kind",
        ),
        CheckConstraint(
            "status != 'processing' OR (lease_token IS NOT NULL AND leased_by IS NOT NULL "
            "AND leased_until IS NOT NULL)",
            name="ck_projection_delivery_processing_lease",
        ),
        CheckConstraint(
            "status != 'published' OR (published_at IS NOT NULL AND receipt_json IS NOT NULL "
            "AND receipt_digest IS NOT NULL)",
            name="ck_projection_delivery_published_receipt",
        ),
        CheckConstraint(
            "status != 'pending' OR (lease_token IS NULL AND leased_by IS NULL "
            "AND leased_until IS NULL)",
            name="ck_projection_delivery_pending_lease",
        ),
        Index(
            "ix_projection_deliveries_claim",
            "tenant_id",
            "project_id",
            "projector_id",
            "status",
            "available_at",
            "stream_position",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    outbox_event_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("outbox_events.id"), nullable=False
    )
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("canonical_projects.id"), nullable=False
    )
    projector_id: Mapped[str] = mapped_column(String(100), nullable=False)
    projector_version: Mapped[str] = mapped_column(String(100), nullable=False)
    barrier_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    stream_position: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    lease_token: Mapped[str | None] = mapped_column(String(64))
    leased_by: Mapped[str | None] = mapped_column(String(255))
    leased_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(100))
    last_error_class: Mapped[str | None] = mapped_column(String(255))
    last_error_message: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    receipt_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    receipt_digest: Mapped[str | None] = mapped_column(String(64))


class ProjectionAttempt(Base):
    """Append-only evidence for each claimed Delivery attempt."""

    __tablename__ = "projection_attempts"
    __table_args__ = (
        UniqueConstraint(
            "delivery_id", "attempt_number", name="uq_projection_attempt_delivery_attempt"
        ),
        CheckConstraint(
            "outcome IN ('claimed', 'succeeded', 'retry_scheduled', 'lease_expired', "
            "'dead_lettered', 'requeued', 'superseded')",
            name="ck_projection_attempt_outcome",
        ),
        Index("ix_projection_attempts_delivery", "delivery_id", "attempt_number"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    delivery_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projection_deliveries.id"), nullable=False
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    lease_token: Mapped[str] = mapped_column(String(64), nullable=False)
    leased_by: Mapped[str] = mapped_column(String(255), nullable=False)
    trigger_source: Mapped[str] = mapped_column(String(50), nullable=False)
    outcome: Mapped[str] = mapped_column(String(30), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_class: Mapped[str | None] = mapped_column(String(255))
    error_message: Mapped[str | None] = mapped_column(Text)
    retry_delay_seconds: Mapped[int | None] = mapped_column(Integer)
    operator_id: Mapped[str | None] = mapped_column(String(255))
    operator_reason: Mapped[str | None] = mapped_column(Text)
    rebuild_run_id: Mapped[str | None] = mapped_column(String(36))


class ProjectionRequeueAudit(Base):
    """Append-only operator evidence; never scheduling authority."""

    __tablename__ = "projection_requeue_audits"
    __table_args__ = (
        Index("ix_projection_requeue_audits_delivery", "delivery_id", "created_at"),
        Index(
            "ix_projection_requeue_audits_scope",
            "tenant_id",
            "project_id",
            "projector_id",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    delivery_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projection_deliveries.id"), nullable=False
    )
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    project_id: Mapped[str] = mapped_column(String(36), nullable=False)
    projector_id: Mapped[str] = mapped_column(String(100), nullable=False)
    prior_attempt_count: Mapped[int] = mapped_column(Integer, nullable=False)
    operator_id: Mapped[str] = mapped_column(String(255), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class ProjectionPartition(TimestampMixin, Base):
    """The ordered cursor and enrollment state of one projector/project scope."""

    __tablename__ = "projection_partitions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "project_id", "projector_id", name="uq_projection_partition_scope"
        ),
        CheckConstraint(
            "enrollment_status IN ('disabled', 'bootstrapping', 'active')",
            name="ck_projection_partition_enrollment_status",
        ),
        CheckConstraint(
            "runtime_status IN ('active', 'pause_requested', 'maintenance', 'catching_up')",
            name="ck_projection_partition_runtime_status",
        ),
        Index("ix_projection_partitions_scope", "tenant_id", "project_id", "projector_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("canonical_projects.id"), nullable=False
    )
    projector_id: Mapped[str] = mapped_column(String(100), nullable=False)
    projector_version: Mapped[str] = mapped_column(String(100), nullable=False)
    enrollment_status: Mapped[str] = mapped_column(String(20), nullable=False, default="disabled")
    runtime_status: Mapped[str] = mapped_column(String(30), nullable=False, default="active")
    last_published_position: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    last_published_event_id: Mapped[str | None] = mapped_column(String(64))
    activation_after_position: Mapped[int | None] = mapped_column(BigInteger)
    active_rebuild_run_id: Mapped[str | None] = mapped_column(String(36))
    maintenance_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    health_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProjectionRebuildRun(TimestampMixin, Base):
    """Durable state machine and manifest evidence for a scoped rebuild."""

    __tablename__ = "projection_rebuild_runs"
    __table_args__ = (
        Index(
            "ux_projection_rebuild_runs_active_scope",
            "tenant_id",
            "project_id",
            "projector_id",
            unique=True,
            postgresql_where=text(
                "status IN ('requested', 'pausing', 'clearing', 'rebuilding', "
                "'reconciling', 'catching_up')"
            ),
        ),
        CheckConstraint(
            "run_kind IN ('maintenance', 'projector_bootstrap')",
            name="ck_projection_rebuild_run_kind",
        ),
        CheckConstraint(
            "status IN ('requested', 'pausing', 'clearing', 'rebuilding', 'reconciling', "
            "'catching_up', 'completed', 'failed', 'reconciliation_failed')",
            name="ck_projection_rebuild_status",
        ),
        Index(
            "ix_projection_rebuild_runs_scope", "tenant_id", "project_id", "projector_id", "status"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("canonical_projects.id"), nullable=False
    )
    projector_id: Mapped[str] = mapped_column(String(100), nullable=False)
    projector_version: Mapped[str] = mapped_column(String(100), nullable=False)
    run_kind: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="requested")
    watermark_position: Mapped[int] = mapped_column(BigInteger, nullable=False)
    watermark_commit_id: Mapped[str | None] = mapped_column(String(36))
    watermark_revision_id: Mapped[str | None] = mapped_column(String(36))
    watermark_state_version_id: Mapped[str | None] = mapped_column(String(36))
    checkpoint_position: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    activation_after_position: Mapped[int | None] = mapped_column(BigInteger)
    lease_token: Mapped[str | None] = mapped_column(String(64))
    leased_by: Mapped[str | None] = mapped_column(String(255))
    leased_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expected_record_count: Mapped[int | None] = mapped_column(Integer)
    processed_record_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    expected_manifest_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    expected_manifest_digest: Mapped[str | None] = mapped_column(String(64))
    actual_manifest_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    actual_manifest_digest: Mapped[str | None] = mapped_column(String(64))
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    operator_id: Mapped[str | None] = mapped_column(String(255))
    operator_reason: Mapped[str | None] = mapped_column(Text)


class ProjectionReconciliation(TimestampMixin, Base):
    """Expected-versus-actual manifest evidence; never a Canon authority."""

    __tablename__ = "projection_reconciliations"
    __table_args__ = (
        UniqueConstraint("rebuild_run_id", name="uq_projection_reconciliation_run"),
        Index("ix_projection_reconciliations_scope", "tenant_id", "project_id", "projector_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    rebuild_run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projection_rebuild_runs.id"), nullable=False
    )
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("canonical_projects.id"), nullable=False
    )
    projector_id: Mapped[str] = mapped_column(String(100), nullable=False)
    watermark_position: Mapped[int] = mapped_column(BigInteger, nullable=False)
    expected_manifest_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    actual_manifest_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    expected_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    actual_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    diff_summary_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    reconciled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class ProjectionAnalyticsEvent(TimestampMixin, Base):
    """Idempotent analytics sink representation keyed by semantic event identity."""

    __tablename__ = "projection_analytics_events"
    __table_args__ = (
        UniqueConstraint(
            "projection_event_id", name="uq_projection_analytics_event_identity"
        ),
        Index("ix_projection_analytics_events_scope", "tenant_id", "project_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    projection_event_id: Mapped[str] = mapped_column(String(64), nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("canonical_projects.id"), nullable=False
    )
    projector_id: Mapped[str] = mapped_column(String(100), nullable=False)
    stream_position: Mapped[int] = mapped_column(BigInteger, nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
