from __future__ import annotations

import pytest

from app.canonical.projection_manifest import build_manifest, reconcile_projection
from app.canonical.projection_ports import ProjectionRecord, ProjectionScope
from app.canonical.projection_registry import DEFAULT_PROJECTOR_REGISTRY


SCOPE = ProjectionScope("tenant-1", "project-1")
SPEC = DEFAULT_PROJECTOR_REGISTRY.get("analytics")


def record(record_id, position, payload=None):
    return ProjectionRecord(
        record_id=record_id,
        stream_position=position,
        commit_id=f"commit-{position}",
        revision_id=f"revision-{position}",
        payload=payload or {"value": record_id},
    )


def test_manifest_is_order_and_serialization_stable():
    records = (record("b", 2, {"z": 1, "a": 2}), record("a", 1))
    first = build_manifest(SCOPE, SPEC, 2, records, ledger=[{"event": "x"}])
    second = build_manifest(
        SCOPE,
        SPEC,
        2,
        reversed(records),
        ledger=[{"event": "x"}],
    )
    assert first == second
    assert first.record_count == 2
    assert first.ledger_digest is not None


def test_empty_manifest_is_deterministic_and_duplicate_ids_fail_closed():
    assert build_manifest(SCOPE, SPEC, 0, ()) == build_manifest(SCOPE, SPEC, 0, [])
    with pytest.raises(ValueError, match="duplicate projection record_id"):
        build_manifest(SCOPE, SPEC, 2, (record("same", 1), record("same", 2)))


def test_count_match_cannot_hide_content_mismatch():
    expected_records = (record("a", 1, {"value": "expected"}),)
    actual_records = (record("a", 1, {"value": "changed"}),)
    result = reconcile_projection(
        build_manifest(SCOPE, SPEC, 1, expected_records),
        build_manifest(SCOPE, SPEC, 1, actual_records),
        expected_records=expected_records,
        actual_records=actual_records,
    )
    assert result.status == "mismatch"
    assert result.changed_ids == ("a",)


def test_reconciliation_reports_missing_extra_and_coverage_changes():
    expected_records = (record("a", 1), record("b", 2))
    actual_records = (
        record("a", 1).model_copy(update={"revision_id": "different"}),
        record("c", 2),
    )
    result = reconcile_projection(
        build_manifest(SCOPE, SPEC, 2, expected_records),
        build_manifest(SCOPE, SPEC, 2, actual_records),
        expected_records=expected_records,
        actual_records=actual_records,
    )
    assert result.status == "mismatch"
    assert result.missing_ids == ("b",)
    assert result.extra_ids == ("c",)
    assert result.changed_ids == ("a",)
