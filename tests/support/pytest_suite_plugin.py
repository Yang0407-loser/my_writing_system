"""Pytest integration for CI-safe and historical suite selection."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.support.test_suite_selection import (
    ManifestError,
    load_manifest,
    missing_node_ids,
)


MANIFEST_RELATIVE_PATH = Path("tests/test_suite_manifest.json")


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Mark manifest items before pytest applies its marker expression."""

    manifest_path = Path(config.rootpath) / MANIFEST_RELATIVE_PATH
    try:
        manifest = load_manifest(manifest_path)
    except ManifestError as exc:
        raise pytest.UsageError(f"historical manifest invalid: {exc}") from exc

    entries_by_node_id = {entry.node_id: entry for entry in manifest.entries}
    for item in items:
        entry = entries_by_node_id.get(item.nodeid)
        if entry is not None:
            item.add_marker(
                pytest.mark.historical_artifact(
                    category=entry.category,
                    reason=entry.reason,
                )
            )

    if not config.option.file_or_dir:
        missing = missing_node_ids(manifest, (item.nodeid for item in items))
        if missing:
            rendered = "\n".join(f"  - {node_id}" for node_id in missing)
            raise pytest.UsageError(
                "historical manifest drift: node IDs were not collected:\n"
                f"{rendered}"
            )
