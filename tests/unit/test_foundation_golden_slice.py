from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts.foundation.build_golden_slice import (
    EXPECTED_SOURCE_SHA256,
    build_golden_slice,
    canonical_json_bytes,
)


ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "foundation_golden_slice_v1.json"
REGISTRY_PATH = ROOT / "tests" / "quality" / "style_baseline_registry.v1.json"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_golden_fixture_contract_is_authorized_deterministic_and_secret_free():
    fixture = _load(FIXTURE_PATH)

    assert fixture["schema_version"] == "foundation-golden-slice-v1"
    assert fixture["ids"] == {
        "tenant_id": "tenant-foundation-golden",
        "project_id": "project-foundation-golden",
        "document_id": "document-foundation-golden",
        "subsection_id": "subsection-foundation-golden-0001",
        "task_id": "task-foundation-golden",
    }
    assert fixture["source"]["path"] == "output/周六面包店与凌晨三点半_20260715_143857.md"
    assert fixture["source"]["sha256"] == EXPECTED_SOURCE_SHA256
    assert fixture["source"]["authorization"] == "internal_generated_regression_artifact"

    excerpt = fixture["subsection"]
    assert excerpt["ordinal"] == 1
    assert excerpt["heading"] == "第1节：第一卷"
    assert excerpt["body"].strip()
    assert hashlib.sha256(excerpt["body"].encode("utf-8")).hexdigest() == excerpt["body_sha256"]
    assert excerpt["character_count"] == len(excerpt["body"])

    assert fixture["outline"]["sections"][0]["ordinal"] == 1
    assert fixture["style_profile"]["language"] == "zh-CN"
    assert fixture["initial_canonical_state"]["schema_version"] == "canonical-state-v0"
    assert fixture["handover"]["new_facts"]
    assert fixture["handover_expected_shape"]["required_fields"] == [
        "summary",
        "new_facts",
        "arc_progress",
    ]
    assert fixture["secret_scan"]["contains_secret"] is False
    assert fixture["secret_scan"]["findings"] == []


def test_builder_output_matches_checked_in_fixture_and_is_stable(tmp_path):
    fixture = _load(FIXTURE_PATH)
    excerpt = fixture["subsection"]
    source_text = (
        "# deterministic replay\n\n"
        f"{excerpt['heading']}\n\n{excerpt['body']}\n\n"
        "第2节：sentinel\n\nThis content is outside the extraction boundary.\n"
    )

    first = build_golden_slice(source_text, fixture["source"]["sha256"])
    second = build_golden_slice(source_text, fixture["source"]["sha256"])

    assert canonical_json_bytes(first) == canonical_json_bytes(second)
    assert first == fixture


def test_style_registry_links_the_golden_slice_without_promoting_it_to_human_gold():
    registry = _load(REGISTRY_PATH)
    baseline = next(
        item
        for item in registry["baselines"]
        if item["baseline_id"] == "regression-saturday-bakery-v1"
    )

    assert baseline["baseline_type"] == "regression"
    assert baseline["golden_slice_fixture"] == "tests/fixtures/foundation_golden_slice_v1.json"
    assert baseline["source_sha256"] == EXPECTED_SOURCE_SHA256
