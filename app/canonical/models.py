"""Canonical Schema v0 SQLAlchemy mappings.

Alembic migrations own physical schema creation; this metadata is for typed
runtime access and migration comparison only.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
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
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
