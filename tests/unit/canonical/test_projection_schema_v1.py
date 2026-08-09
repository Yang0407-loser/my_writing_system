from __future__ import annotations

from sqlalchemy import BigInteger, CheckConstraint, UniqueConstraint

from app.canonical.models import (
    CanonicalCommit,
    CanonicalProject,
    OutboxEvent,
    ProjectionAnalyticsEvent,
    ProjectionAttempt,
    ProjectionDelivery,
    ProjectionPartition,
    ProjectionRebuildRun,
    ProjectionReconciliation,
)


def _constraint_names(model, kind):
    return {constraint.name for constraint in model.__table__.constraints if isinstance(constraint, kind)}


def _index_names(model):
    return {index.name for index in model.__table__.indexes}


def test_canon_and_outbox_share_explicit_project_stream_positions():
    assert isinstance(CanonicalProject.__table__.c.next_stream_position.type, BigInteger)
    assert isinstance(CanonicalCommit.__table__.c.stream_position.type, BigInteger)
    assert isinstance(OutboxEvent.__table__.c.stream_position.type, BigInteger)
    assert "uq_canonical_commit_project_stream_position" in _constraint_names(
        CanonicalCommit, UniqueConstraint
    )
    assert "uq_outbox_projection" in _constraint_names(OutboxEvent, UniqueConstraint)


def test_delivery_is_one_to_one_with_envelope_and_owns_durable_scheduling_state():
    table = ProjectionDelivery.__table__
    assert "uq_projection_delivery_envelope_projector" in _constraint_names(
        ProjectionDelivery, UniqueConstraint
    )
    assert "uq_projection_delivery_partition_position" in _constraint_names(
        ProjectionDelivery, UniqueConstraint
    )
    assert {
        "ck_projection_delivery_status",
        "ck_projection_delivery_barrier_kind",
        "ck_projection_delivery_processing_lease",
        "ck_projection_delivery_published_receipt",
        "ck_projection_delivery_pending_lease",
    }.issubset(_constraint_names(ProjectionDelivery, CheckConstraint))
    assert {
        "lease_token",
        "leased_by",
        "leased_until",
        "attempt_count",
        "last_attempt_at",
        "receipt_json",
        "receipt_digest",
        "last_error_code",
        "last_error_class",
        "last_error_message",
    }.issubset(table.c.keys())
    assert "ix_projection_deliveries_claim" in _index_names(ProjectionDelivery)


def test_attempt_partition_rebuild_and_reconciliation_metadata_freeze_state_ownership():
    assert "uq_projection_attempt_delivery_attempt" in _constraint_names(
        ProjectionAttempt, UniqueConstraint
    )
    assert "ck_projection_attempt_outcome" in _constraint_names(
        ProjectionAttempt, CheckConstraint
    )
    assert "uq_projection_partition_scope" in _constraint_names(
        ProjectionPartition, UniqueConstraint
    )
    assert {
        "ck_projection_partition_enrollment_status",
        "ck_projection_partition_runtime_status",
    }.issubset(_constraint_names(ProjectionPartition, CheckConstraint))
    assert "activation_after_position" in ProjectionPartition.__table__.c
    assert "ux_projection_rebuild_runs_active_scope" in _index_names(
        ProjectionRebuildRun
    )
    assert {
        "ck_projection_rebuild_run_kind",
        "ck_projection_rebuild_status",
    }.issubset(_constraint_names(ProjectionRebuildRun, CheckConstraint))
    assert {
        "watermark_position",
        "checkpoint_position",
        "activation_after_position",
        "lease_token",
        "leased_by",
        "leased_until",
        "expected_manifest_json",
        "actual_manifest_json",
    }.issubset(ProjectionRebuildRun.__table__.c.keys())
    assert "uq_projection_reconciliation_run" in _constraint_names(
        ProjectionReconciliation, UniqueConstraint
    )


def test_every_p3a_state_table_has_its_scoped_index():
    assert "ix_projection_attempts_delivery" in _index_names(ProjectionAttempt)
    assert "ix_projection_partitions_scope" in _index_names(ProjectionPartition)
    assert "ix_projection_rebuild_runs_scope" in _index_names(ProjectionRebuildRun)
    assert "ix_projection_reconciliations_scope" in _index_names(
        ProjectionReconciliation
    )


def test_analytics_rows_are_deduplicated_by_semantic_projection_identity():
    assert "uq_projection_analytics_event_identity" in _constraint_names(
        ProjectionAnalyticsEvent, UniqueConstraint
    )
    assert "ix_projection_analytics_events_scope" in _index_names(
        ProjectionAnalyticsEvent
    )
