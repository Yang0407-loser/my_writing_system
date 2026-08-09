from __future__ import annotations

import inspect
import json
from pathlib import Path

from app.agents.writer import Writer
from app.main import app
from app.models import FinalResult, TaskStatus
from scripts.foundation.snapshot_contracts import (
    build_openapi_snapshot,
    build_writer_snapshot,
    canonical_json_bytes,
)


ROOT = Path(__file__).resolve().parents[2]
OPENAPI_SNAPSHOT = ROOT / "tests" / "contracts" / "openapi-pre-foundation-v0.json"
WRITER_SNAPSHOT = ROOT / "tests" / "contracts" / "writer-pre-foundation-v0.json"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _assert_compatible_subset(expected, actual, path="$()"):
    if isinstance(expected, dict):
        assert isinstance(actual, dict), f"{path} changed type"
        for key, value in expected.items():
            assert key in actual, f"{path}.{key} was removed or renamed"
            _assert_compatible_subset(value, actual[key], f"{path}.{key}")
    elif isinstance(expected, list):
        assert expected == actual, f"{path} changed ordered contract"
    else:
        assert expected == actual, f"{path} changed from {expected!r} to {actual!r}"


def test_normalized_openapi_preserves_the_pre_foundation_contract():
    frozen = _load(OPENAPI_SNAPSHOT)
    current = build_openapi_snapshot(app)

    _assert_compatible_subset(frozen, current)
    assert canonical_json_bytes(current) == canonical_json_bytes(
        build_openapi_snapshot(app)
    )


def test_writer_signature_order_defaults_and_annotations_are_frozen():
    frozen = _load(WRITER_SNAPSHOT)
    current = build_writer_snapshot(Writer, TaskStatus, FinalResult)

    assert frozen["writer_run"] == current["writer_run"]
    assert [parameter.name for parameter in inspect.signature(Writer.run).parameters.values()][0] == "self"


def test_task_response_required_fields_and_types_remain_compatible():
    frozen = _load(WRITER_SNAPSHOT)
    current = build_writer_snapshot(Writer, TaskStatus, FinalResult)

    _assert_compatible_subset(
        frozen["task_response_contracts"],
        current["task_response_contracts"],
        "$.task_response_contracts",
    )


def test_snapshots_contain_no_environment_secrets_or_absolute_output_paths():
    serialized = OPENAPI_SNAPSHOT.read_text(encoding="utf-8") + WRITER_SNAPSHOT.read_text(
        encoding="utf-8"
    )
    lowered = serialized.casefold()

    assert "bearer " not in lowered
    assert '"api_key"' not in lowered
    assert "e:\\writer" not in lowered
    assert "c:\\users" not in lowered
