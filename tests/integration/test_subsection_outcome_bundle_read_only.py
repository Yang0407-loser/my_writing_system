import json
from pathlib import Path

from app.writing.subsection_outcome_bundle import (
    SubsectionOutcomeBundleAdapter,
)


ROOT = Path(__file__).resolve().parents[2]
ACCEPTANCE = (
    ROOT
    / "reports"
    / "state-frame-real-four-subsection-acceptance-2026-07-25.json"
)


def test_persisted_state_frame_records_build_four_read_only_bundles():
    payload = json.loads(ACCEPTANCE.read_text(encoding="utf-8"))
    records = payload["history"]["records"]
    adapter = SubsectionOutcomeBundleAdapter()
    bundles = [
        adapter.build(
            task_id=payload["task"]["task_id"],
            section=record["section"],
            subsection=record["subsection"],
            state_frame_record=record,
        )
        for record in records
    ]
    assert len(bundles) == 4
    assert len({bundle.bundle_id for bundle in bundles}) == 4
    assert all(bundle.production_effect is False for bundle in bundles)
    assert all(bundle.source_traceability_rate == 1.0 for bundle in bundles)
    assert all(bundle.available_component_count == 0 for bundle in bundles)
    assert all(bundle.unavailable_component_count == 5 for bundle in bundles)


def test_adapter_does_not_mutate_persisted_input():
    payload = json.loads(ACCEPTANCE.read_text(encoding="utf-8"))
    record = payload["history"]["records"][0]
    before = json.dumps(record, ensure_ascii=False, sort_keys=True)
    SubsectionOutcomeBundleAdapter().build(
        task_id=payload["task"]["task_id"],
        section=record["section"],
        subsection=record["subsection"],
        state_frame_record=record,
    )
    assert json.dumps(record, ensure_ascii=False, sort_keys=True) == before
