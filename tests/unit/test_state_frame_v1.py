from pydantic import ValidationError
import pytest

from app.writing.state_frame_builder import (
    StateFrameBuilder,
    fact_from_mapping,
    facts_from_foreshadows,
    facts_from_handover,
    facts_from_post_write_bundle,
)
from app.writing.state_frame_quality import StateFrameQualityEvaluator
from app.writing.state_frame_v1 import StateExpectation, StateFact
from app import character_relation_store, foreshadowing_store


def _fact(value="before", **overrides):
    payload = {
        "fact_type": "character_state",
        "subject": "character-1",
        "predicate": "location",
        "value": value,
        "status": "confirmed",
        "durability": "subsection",
        "source_type": "fixture",
        "source_id": f"source-{value}",
        "source_hash": f"hash-{value}",
        "producer": "fixture",
        "confidence": 1.0,
        "provenance": "authoritative_state_delta",
    }
    payload.update(overrides)
    return fact_from_mapping(payload)


def _expectation():
    return StateExpectation(
        expectation_id="expectation-1",
        expectation_type="state_transition",
        subject="character-1",
        expected_transition="changes",
        requiredness="hard",
        section=1,
        subsection=1,
        source_id="outline-1",
        source_hash="outline-hash",
        confidence=1.0,
        provenance="author_confirmed_outline_event",
    )


def test_state_fact_validates_enums_and_excerpt_length():
    with pytest.raises(ValidationError):
        StateFact(
            fact_id="f",
            fact_type="character_state",
            subject="c",
            predicate="p",
            value="v",
            status="planned",
            durability="subsection",
            source_type="fixture",
            source_id="s",
            source_hash="h",
            producer="fixture",
            confidence=1.0,
            provenance="fixture",
        )
    with pytest.raises(ValidationError):
        StateFact(
            fact_id="f",
            fact_type="character_state",
            subject="c",
            predicate="p",
            value="v",
            status="confirmed",
            durability="subsection",
            source_type="fixture",
            source_id="s",
            source_hash="h",
            evidence_excerpt="x" * 141,
            producer="fixture",
            confidence=1.0,
            provenance="fixture",
        )


def test_planned_expectation_is_not_a_confirmed_fact():
    builder = StateFrameBuilder()
    frame = builder.build(
        task_id="task",
        section=1,
        subsection=1,
        frame_phase="before_generation",
        facts=[],
        expectations=[_expectation()],
    )
    assert frame.facts == ()
    assert frame.expectations[0].status == "planned"


def test_post_write_bundle_maps_traceable_evidence():
    facts = facts_from_post_write_bundle({
        "section": 1,
        "subsection": 2,
        "changes": [{
            "change_id": "change-1",
            "category": "character_state",
            "subject": "character-1",
            "predicate": "location",
            "value": "bakery",
            "status": "confirmed",
            "confidence": 0.9,
            "evidence": [{
                "source_id": "writer-output:task:1:2",
                "text_hash": "output-hash",
                "span_start": 2,
                "span_end": 8,
                "excerpt": "short",
            }],
        }],
    })
    assert len(facts) == 1
    assert facts[0].section == 1
    assert facts[0].subsection == 2
    assert facts[0].source_hash == "output-hash"
    assert facts[0].evidence_excerpt == "short"


def test_handover_continuity_and_open_chain_remain_non_confirmed():
    facts = facts_from_handover([{
        "from_section": 1,
        "to_section": 2,
        "character_state": "state",
        "open_threads": "unfinished",
        "foreshadowing": "seed",
    }], section=2)
    assert {item.fact_type for item in facts} == {
        "continuity_state", "open_event_chain", "foreshadow_state"
    }
    assert all(item.status in {"unknown", "pending"} for item in facts)


def test_foreshadow_lifecycle_mapping_keeps_normalized_null():
    facts = facts_from_foreshadows([{
        "id": "f1", "status": "pending", "plant_chapter": 1,
        "resolve_chapter": None,
    }])
    assert facts[0].value["resolve_chapter"] is None
    assert facts[0].predicate == "foreshadow_lifecycle"


def test_frames_and_delta_are_deterministic_and_idempotent():
    builder = StateFrameBuilder()
    before = builder.build(
        task_id="task",
        section=1,
        subsection=1,
        frame_phase="before_generation",
        facts=[_fact()],
        expectations=[_expectation()],
        unavailable_source_types=["history"],
    )
    repeated = builder.build(
        task_id="task",
        section=1,
        subsection=1,
        frame_phase="before_generation",
        facts=[_fact()],
        expectations=[_expectation()],
        unavailable_source_types=["history"],
    )
    after = builder.build(
        task_id="task",
        section=1,
        subsection=1,
        frame_phase="after_commit",
        facts=[_fact("after")],
        expectations=[_expectation()],
        unavailable_source_types=["history"],
    )
    assert before.frame_hash == repeated.frame_hash
    assert before.frame_status == "partial"
    first = builder.delta(before, after)
    second = builder.delta(before, after)
    assert first == second
    assert len(first.changed_facts) == 1


def test_pending_source_is_explicit_and_finalize_does_not_mutate_input():
    builder = StateFrameBuilder()
    frame = builder.build(
        task_id="task",
        section=1,
        subsection=1,
        frame_phase="after_commit",
        facts=[],
        pending_source_types=["post_write"],
    )
    assert frame.frame_status == "pending_sources"
    assert frame.finalized_at is None


def test_unassessable_items_do_not_enter_quality_denominator():
    builder = StateFrameBuilder()
    before = builder.build(
        task_id="task",
        section=1,
        subsection=1,
        frame_phase="before_generation",
        facts=[],
    )
    after = builder.build(
        task_id="task",
        section=1,
        subsection=1,
        frame_phase="after_commit",
        facts=[],
        expectations=[_expectation().model_copy(update={
            "subject": "unassigned", "confidence": 0.3
        })],
    )
    delta = builder.delta(before, after)
    quality = StateFrameQualityEvaluator().evaluate(before, after, delta)
    transition = next(
        item for item in quality.metrics
        if item.dimension == "character_state_transition"
    )
    assert transition.counts["expected_state_transitions"] == 1
    assert transition.counts["assessable_state_transitions"] == 0
    assert transition.counts["character_state_consistency"] is None


def test_read_only_store_helpers_do_not_initialize_missing_databases(
    monkeypatch, tmp_path
):
    relation_path = tmp_path / "missing-relations.db"
    foreshadow_path = tmp_path / "missing-foreshadows.db"
    monkeypatch.setattr(character_relation_store, "DB_PATH", str(relation_path))
    monkeypatch.setattr(
        foreshadowing_store, "FORESHADOWING_DB_PATH", str(foreshadow_path)
    )
    assert character_relation_store.list_relations_read_only("task") == []
    assert foreshadowing_store.list_foreshadowings_read_only("task") == []
    assert not relation_path.exists()
    assert not foreshadow_path.exists()
