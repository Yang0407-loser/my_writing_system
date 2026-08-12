"""Deterministic projection manifests and fail-closed reconciliation evidence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import select
from sqlalchemy.orm import Session

from .hashing import sha256_json
from .models import ProjectionRebuildRun, ProjectionReconciliation
from .projection_ports import ProjectionManifest, ProjectionRecord, ProjectionScope
from .projection_registry import ProjectorSpec


@dataclass(frozen=True)
class ReconciliationResult:
    status: Literal["matched", "mismatch"]
    expected: ProjectionManifest
    actual: ProjectionManifest
    missing_ids: tuple[str, ...]
    extra_ids: tuple[str, ...]
    changed_ids: tuple[str, ...]


def build_manifest(
    scope: ProjectionScope,
    spec: ProjectorSpec,
    watermark_position: int,
    records: Iterable[ProjectionRecord],
    *,
    ledger: object | None = None,
) -> ProjectionManifest:
    if watermark_position < 0:
        raise ValueError("watermark_position must be non-negative")
    records = _normalized_records(records)
    record_ids = [record.record_id for record in records]
    duplicates = sorted(
        record_id for record_id in set(record_ids) if record_ids.count(record_id) > 1
    )
    if duplicates:
        raise ValueError(f"duplicate projection record_id: {duplicates[0]}")
    if any(record.stream_position > watermark_position for record in records):
        raise ValueError("projection record exceeds manifest watermark")
    serialized = [record.model_dump(mode="json") for record in records]
    coverage = [
        {
            "record_id": record.record_id,
            "stream_position": record.stream_position,
            "commit_id": record.commit_id,
            "revision_id": record.revision_id,
        }
        for record in records
    ]
    return ProjectionManifest(
        projector_id=spec.projector_id,
        projector_version=spec.version,
        tenant_id=scope.tenant_id,
        project_id=scope.project_id,
        watermark_position=watermark_position,
        record_count=len(records),
        content_digest=sha256_json(serialized),
        coverage_digest=sha256_json(coverage),
        ledger_digest=None if ledger is None else sha256_json(ledger),
    )


def reconcile_projection(
    expected: ProjectionManifest,
    actual: ProjectionManifest,
    *,
    expected_records: Iterable[ProjectionRecord] = (),
    actual_records: Iterable[ProjectionRecord] = (),
    session: Session | None = None,
    rebuild_run_id: str | None = None,
    sample_limit: int = 20,
) -> ReconciliationResult:
    if sample_limit < 1:
        raise ValueError("sample_limit must be positive")
    expected_by_id = _record_map(expected_records)
    actual_by_id = _record_map(actual_records)
    missing_ids = tuple(sorted(expected_by_id.keys() - actual_by_id.keys()))
    extra_ids = tuple(sorted(actual_by_id.keys() - expected_by_id.keys()))
    changed_ids = tuple(
        sorted(
            record_id
            for record_id in expected_by_id.keys() & actual_by_id.keys()
            if expected_by_id[record_id] != actual_by_id[record_id]
        )
    )
    matched = expected == actual and not (missing_ids or extra_ids or changed_ids)
    result = ReconciliationResult(
        status="matched" if matched else "mismatch",
        expected=expected,
        actual=actual,
        missing_ids=missing_ids,
        extra_ids=extra_ids,
        changed_ids=changed_ids,
    )
    if (session is None) != (rebuild_run_id is None):
        raise ValueError("session and rebuild_run_id must be provided together")
    if session is not None and rebuild_run_id is not None:
        _persist_evidence(session, rebuild_run_id, result, sample_limit)
    return result


def _record_map(records: Iterable[ProjectionRecord]) -> dict[str, ProjectionRecord]:
    result = {}
    for record in _normalized_records(records):
        if record.record_id in result:
            raise ValueError(f"duplicate projection record_id: {record.record_id}")
        result[record.record_id] = record
    return result


def _normalized_records(
    records: Iterable[ProjectionRecord],
) -> tuple[ProjectionRecord, ...]:
    normalized = tuple(ProjectionRecord.model_validate(record) for record in records)
    return tuple(
        sorted(normalized, key=lambda record: (record.stream_position, record.record_id))
    )


def _persist_evidence(
    session: Session,
    rebuild_run_id: str,
    result: ReconciliationResult,
    sample_limit: int,
) -> None:
    run = session.get(ProjectionRebuildRun, rebuild_run_id)
    if run is None:
        raise ValueError("rebuild run is required for reconciliation evidence")
    expected = result.expected
    actual = result.actual
    if (
        expected.tenant_id != run.tenant_id
        or expected.project_id != run.project_id
        or expected.projector_id != run.projector_id
        or expected.projector_version != run.projector_version
        or expected.watermark_position != run.watermark_position
        or actual.tenant_id != run.tenant_id
        or actual.project_id != run.project_id
        or actual.projector_id != run.projector_id
        or actual.projector_version != run.projector_version
        or actual.watermark_position != run.watermark_position
    ):
        raise ValueError("reconciliation manifests do not match rebuild run scope")
    expected_json = expected.model_dump(mode="json")
    actual_json = actual.model_dump(mode="json")
    diff = {
        "status": result.status,
        "missing_count": len(result.missing_ids),
        "extra_count": len(result.extra_ids),
        "changed_count": len(result.changed_ids),
        "missing_id_samples": list(result.missing_ids[:sample_limit]),
        "extra_id_samples": list(result.extra_ids[:sample_limit]),
        "changed_id_samples": list(result.changed_ids[:sample_limit]),
    }
    row = session.scalar(
        select(ProjectionReconciliation).where(
            ProjectionReconciliation.rebuild_run_id == rebuild_run_id
        )
    )
    values = {
        "tenant_id": run.tenant_id,
        "project_id": run.project_id,
        "projector_id": run.projector_id,
        "watermark_position": run.watermark_position,
        "expected_manifest_json": expected_json,
        "actual_manifest_json": actual_json,
        "expected_digest": sha256_json(expected_json),
        "actual_digest": sha256_json(actual_json),
        "diff_summary_json": diff,
    }
    if row is None:
        row = ProjectionReconciliation(
            id=str(uuid5(NAMESPACE_URL, f"projection-reconciliation:{rebuild_run_id}")),
            rebuild_run_id=rebuild_run_id,
            **values,
        )
        session.add(row)
    else:
        for key, value in values.items():
            setattr(row, key, value)
    session.flush()
