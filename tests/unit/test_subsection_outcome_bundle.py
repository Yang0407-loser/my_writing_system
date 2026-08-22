import pytest
from pydantic import ValidationError

from app.writing.subsection_outcome_bundle import (
    OutcomeComponent,
    OutcomeSourceRef,
    SubsectionOutcomeBundleAdapter,
)


TASK_ID = "task"


def _record(subsection=1):
    return {
        "record_id": f"record:{subsection}",
        "output_sha256": f"output-{subsection}",
        "prompt_messages_hash": f"prompt-{subsection}",
        "commit_idempotency_key": f"task:1:{subsection}",
        "finalized_at": "2026-07-25T00:00:00+00:00",
    }


def _build(subsection=1, **sources):
    return SubsectionOutcomeBundleAdapter().build(
        task_id=TASK_ID,
        section=1,
        subsection=subsection,
        state_frame_record=_record(subsection),
        is_last_subsection=subsection == 4,
        **sources,
    )


def _component(bundle, component_type):
    return next(
        item for item in bundle.components
        if item.component_type == component_type
    )


def test_contract_rejects_missing_source_identity_and_fake_unavailable_source():
    with pytest.raises(ValidationError):
        OutcomeSourceRef(
            source_type="handover",
            source_id="",
            source_hash="hash",
            producer="producer",
            storage_location="checkpoint",
            granularity="section_aggregate",
            authority="committed_checkpoint",
            provenance="test",
        )
    source = OutcomeSourceRef(
        source_type="handover",
        source_id="handover:1",
        source_hash="hash",
        producer="producer",
        storage_location="checkpoint",
        section=1,
        granularity="section_aggregate",
        authority="committed_checkpoint",
        provenance="test",
    )
    with pytest.raises(ValidationError):
        OutcomeComponent(
            component_type="handover_delta",
            availability="unavailable",
            granularity=None,
            summary_hash="hash",
            source_refs=(source,),
            item_count=1,
            unavailable_reason="missing",
            producer_status="unavailable",
        )


def test_bundle_is_deterministic_frozen_and_traceable():
    first = _build()
    second = _build()
    assert first.bundle_id == second.bundle_id
    assert first.bundle_hash == second.bundle_hash
    assert first.source_traceability_rate == 1.0
    assert first.production_effect is False
    with pytest.raises(ValidationError):
        first.subsection = 2


def test_exact_handover_is_available_only_with_explicit_subsection():
    bundle = _build(
        handover_entries=[
            {
                "source_id": "handover:1:1",
                "from_section": 1,
                "subsection": 1,
                "open_threads": "private value",
            }
        ]
    )
    component = _component(bundle, "handover_delta")
    assert component.availability == "available"
    assert component.granularity == "subsection_exact"
    assert component.source_refs[0].subsection == 1
    assert "private value" not in component.model_dump_json()


def test_section_handover_is_not_copied_to_every_subsection():
    entry = {"from_section": 1, "open_threads": "private value"}
    early = _build(handover_entries=[entry])
    last = _build(subsection=4, handover_entries=[entry])
    early_component = _component(early, "handover_delta")
    last_component = _component(last, "handover_delta")
    assert early_component.availability == "unavailable"
    assert early_component.source_refs == ()
    assert last_component.availability == "partial"
    assert last_component.granularity == "section_aggregate"


def test_task_final_character_state_does_not_backfill_early_subsections():
    records = [{"name": "private character", "state": "private state"}]
    early = _build(character_state_records=records)
    last = _build(subsection=4, character_state_records=records)
    assert _component(early, "character_state_delta").availability == "unavailable"
    component = _component(last, "character_state_delta")
    assert component.availability == "partial"
    assert component.granularity == "task_final_snapshot"
    assert "private character" not in component.model_dump_json()


def test_current_relationship_snapshot_is_not_promoted_to_delta():
    records = [
        {
            "id": "relationship:1",
            "source_section": 1,
            "description": "private relationship",
        }
    ]
    component = _component(
        _build(subsection=4, relationship_records=records),
        "relationship_delta",
    )
    assert component.availability == "partial"
    assert component.granularity == "current_store_snapshot"
    assert "private relationship" not in component.model_dump_json()


def test_current_foreshadow_snapshot_is_not_promoted_to_lifecycle_delta():
    records = [
        {
            "id": "foreshadow:1",
            "plant_chapter": 1,
            "status": "planted",
            "description": "private foreshadow",
        }
    ]
    component = _component(
        _build(subsection=4, foreshadow_records=records),
        "foreshadow_delta",
    )
    assert component.availability == "partial"
    assert component.granularity == "current_store_snapshot"
    assert "private foreshadow" not in component.model_dump_json()


def test_section_experience_is_partial_and_exact_experience_is_available():
    aggregate = [{"id": "event:aggregate", "chapter": 1, "subsection": 0}]
    partial = _component(
        _build(subsection=4, experience_records=aggregate),
        "experience_delta",
    )
    assert partial.availability == "partial"
    assert partial.granularity == "section_aggregate"

    exact = [{"id": "event:exact", "chapter": 1, "subsection": 2}]
    available = _component(
        _build(subsection=2, experience_records=exact),
        "experience_delta",
    )
    assert available.availability == "available"
    assert available.granularity == "subsection_exact"


def test_same_source_id_with_different_hash_is_conflicted():
    records = [
        {
            "id": "event:1",
            "chapter": 1,
            "subsection": 2,
            "description": "first private value",
        },
        {
            "id": "event:1",
            "chapter": 1,
            "subsection": 2,
            "description": "second private value",
        },
    ]
    component = _component(
        _build(subsection=2, experience_records=records),
        "experience_delta",
    )
    assert component.availability == "conflicted"
    assert component.conflict_reason.startswith(
        "same_source_id_with_different_hash"
    )


def test_unavailable_components_have_no_fabricated_sources():
    bundle = _build()
    assert bundle.available_component_count == 0
    assert bundle.partial_component_count == 0
    assert bundle.unavailable_component_count == 5
    assert bundle.exact_subsection_component_count == 0
    assert bundle.temporal_integrity_status == "verified"
    assert all(
        component.source_refs == ()
        for component in bundle.components
        if component.availability == "unavailable"
    )
