"""Validated classification for CI-safe and historical pytest suites."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Iterable


ALLOWED_CATEGORIES = frozenset(
    {"missing_generated_artifact", "frozen_hash_drift"}
)


class ManifestError(ValueError):
    """Raised when the historical test manifest is structurally invalid."""


@dataclass(frozen=True, slots=True)
class HistoricalTestEntry:
    node_id: str
    category: str
    reason: str


@dataclass(frozen=True, slots=True)
class HistoricalTestManifest:
    schema_version: int
    entries: tuple[HistoricalTestEntry, ...]

    @property
    def node_ids(self) -> frozenset[str]:
        return frozenset(entry.node_id for entry in self.entries)


def _required_string(entry: dict[str, object], field: str, index: int) -> str:
    value = entry.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"entries[{index}].{field} must be a non-empty string")
    return value


def load_manifest(path: Path) -> HistoricalTestManifest:
    """Load a version-1 historical test manifest, failing closed on drift."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"cannot load manifest {path}: {exc}") from exc

    if not isinstance(payload, dict):
        raise ManifestError("manifest root must be an object")
    if payload.get("schema_version") != 1:
        raise ManifestError(
            f"unsupported schema_version: {payload.get('schema_version')!r}"
        )
    raw_entries = payload.get("entries")
    if not isinstance(raw_entries, list) or not raw_entries:
        raise ManifestError("entries must be a non-empty list")

    entries: list[HistoricalTestEntry] = []
    seen: set[str] = set()
    for index, raw_entry in enumerate(raw_entries):
        if not isinstance(raw_entry, dict):
            raise ManifestError(f"entries[{index}] must be an object")
        node_id = _required_string(raw_entry, "node_id", index)
        category = _required_string(raw_entry, "category", index)
        reason = _required_string(raw_entry, "reason", index)
        if category not in ALLOWED_CATEGORIES:
            raise ManifestError(
                f"entries[{index}] has unknown category: {category!r}"
            )
        if node_id in seen:
            raise ManifestError(f"duplicate node_id: {node_id}")
        seen.add(node_id)
        entries.append(
            HistoricalTestEntry(
                node_id=node_id,
                category=category,
                reason=reason,
            )
        )

    return HistoricalTestManifest(schema_version=1, entries=tuple(entries))


def missing_node_ids(
    manifest: HistoricalTestManifest,
    collected_node_ids: Iterable[str],
) -> tuple[str, ...]:
    """Return manifest IDs not present in a full pytest collection."""

    return tuple(sorted(manifest.node_ids.difference(collected_node_ids)))
