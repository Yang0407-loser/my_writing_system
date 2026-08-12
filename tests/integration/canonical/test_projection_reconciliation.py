from __future__ import annotations

from sqlalchemy import func, select

from app.canonical.commit_service import CanonicalCommitService
from app.canonical.models import (
    CanonicalCommit,
    ProjectionPartition,
    ProjectionRebuildRun,
    ProjectionReconciliation,
)
from app.canonical.projection_manifest import build_manifest, reconcile_projection
from app.canonical.projection_ports import ProjectionRecord, ProjectionScope
from app.canonical.projection_registry import DEFAULT_PROJECTOR_REGISTRY
from tests.unit.canonical.test_commit_service import _prepared


pytest_plugins = ("tests.unit.canonical.test_commit_service",)


def test_reconciliation_persists_bounded_evidence_without_mutating_canon(
    canonical_session,
):
    scope = ProjectionScope("tenant-1", "project-1")
    result = CanonicalCommitService(
        canonical_session, scope.tenant_id, scope.project_id
    ).commit(_prepared(canonical_session), "reconciliation-evidence")
    spec = DEFAULT_PROJECTOR_REGISTRY.get("analytics")
    position = canonical_session.get(CanonicalCommit, result.commit_id).stream_position
    run = ProjectionRebuildRun(
        id="rebuild-1",
        tenant_id=scope.tenant_id,
        project_id=scope.project_id,
        projector_id=spec.projector_id,
        projector_version=spec.version,
        run_kind="maintenance",
        status="reconciling",
        watermark_position=position,
        checkpoint_position=position,
    )
    canonical_session.add(run)
    canonical_session.flush()
    expected_records = tuple(
        ProjectionRecord(
            record_id=f"record-{index}",
            stream_position=position,
            commit_id=result.commit_id,
            revision_id=result.revision_id,
            payload={"hash": f"expected-{index}"},
        )
        for index in range(30)
    )
    actual_records = tuple(
        record.model_copy(update={"payload": {"hash": "changed"}})
        for record in expected_records
    )
    commit_count = canonical_session.scalar(
        select(func.count()).select_from(CanonicalCommit)
    )
    cursor = canonical_session.scalar(
        select(ProjectionPartition.last_published_position).where(
            ProjectionPartition.projector_id == spec.projector_id
        )
    )

    comparison = reconcile_projection(
        build_manifest(scope, spec, position, expected_records),
        build_manifest(scope, spec, position, actual_records),
        expected_records=expected_records,
        actual_records=actual_records,
        session=canonical_session,
        rebuild_run_id=run.id,
        sample_limit=5,
    )
    canonical_session.commit()

    assert comparison.status == "mismatch"
    evidence = canonical_session.scalar(select(ProjectionReconciliation))
    assert evidence.diff_summary_json["changed_count"] == 30
    assert len(evidence.diff_summary_json["changed_id_samples"]) == 5
    assert canonical_session.scalar(select(func.count()).select_from(CanonicalCommit)) == commit_count
    assert canonical_session.scalar(
        select(ProjectionPartition.last_published_position).where(
            ProjectionPartition.projector_id == spec.projector_id
        )
    ) == cursor
