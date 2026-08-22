import json
from pathlib import Path

import pytest

from tests.support.test_suite_selection import (
    ManifestError,
    load_manifest,
    missing_node_ids,
)


def _entry(
    node_id: str = "tests/unit/test_sample.py::test_case",
    *,
    category: str = "missing_generated_artifact",
    reason: str = "missing sealed output",
) -> dict[str, str]:
    return {
        "node_id": node_id,
        "category": category,
        "reason": reason,
    }


def _write(path: Path, payload: object) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_manifest_rejects_unsupported_schema_version(tmp_path):
    path = _write(
        tmp_path / "manifest.json",
        {"schema_version": 2, "entries": [_entry()]},
    )

    with pytest.raises(ManifestError, match="unsupported schema_version"):
        load_manifest(path)


def test_manifest_rejects_duplicate_node_ids(tmp_path):
    entry = _entry()
    path = _write(
        tmp_path / "manifest.json",
        {"schema_version": 1, "entries": [entry, entry]},
    )

    with pytest.raises(ManifestError, match="duplicate node_id"):
        load_manifest(path)


def test_manifest_rejects_unknown_category(tmp_path):
    path = _write(
        tmp_path / "manifest.json",
        {
            "schema_version": 1,
            "entries": [_entry(category="legacy")],
        },
    )

    with pytest.raises(ManifestError, match="unknown category"):
        load_manifest(path)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("node_id", "", "node_id"),
        ("reason", "", "reason"),
    ],
)
def test_manifest_rejects_empty_required_strings(tmp_path, field, value, message):
    entry = _entry()
    entry[field] = value
    path = _write(
        tmp_path / "manifest.json",
        {"schema_version": 1, "entries": [entry]},
    )

    with pytest.raises(ManifestError, match=message):
        load_manifest(path)


def test_missing_node_ids_returns_sorted_manifest_drift(tmp_path):
    path = _write(
        tmp_path / "manifest.json",
        {
            "schema_version": 1,
            "entries": [
                _entry(
                    "tests/unit/test_b.py::test_b",
                    category="frozen_hash_drift",
                    reason="stale lock",
                ),
                _entry("tests/unit/test_a.py::test_a"),
            ],
        },
    )
    manifest = load_manifest(path)

    assert missing_node_ids(
        manifest,
        {"tests/unit/test_b.py::test_b"},
    ) == ("tests/unit/test_a.py::test_a",)


def test_checked_in_manifest_preserves_verified_baseline_partition():
    manifest = load_manifest(Path("tests/test_suite_manifest.json"))
    counts = {
        "missing_generated_artifact": 0,
        "frozen_hash_drift": 0,
    }
    for entry in manifest.entries:
        counts[entry.category] += 1

    assert len(manifest.entries) == 124
    assert counts == {
        "missing_generated_artifact": 98,
        "frozen_hash_drift": 26,
    }
