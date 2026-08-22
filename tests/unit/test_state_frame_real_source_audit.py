import json

from tests.benchmarks.audit_state_frame_real_sources import (
    CLASSIFICATIONS,
    build_audit,
)


def _source(*, world_facts=None, include_arcs=True, include_handover=True):
    subsections = [
        {"subsection": index, "title": f"sub-{index}", "key_points": [f"plan-{index}"]}
        for index in range(1, 5)
    ]
    return {
        "task_id": "task",
        "task": {
            "world_state": {
                "facts": world_facts or [],
                "contradictions": [],
                "active_warnings": [],
            },
            "event_graph": [{
                "event_id": "event-1", "type": "arc_milestone",
                "description": "private planned event", "section": 2,
                "subsection": 1, "status": "pending",
            }],
        },
        "checkpoint": {
            "characters": [{"id": "c1", "name": "private name"}],
            "character_arcs": [{
                "character_id": "c1", "current_state": "private current state",
            }] if include_arcs else [],
            "_prev_handover": [{
                "from_section": 1, "to_section": 2,
                "character_state": "private handover state",
                "open_threads": "private open thread",
                "foreshadowing": "",
            }] if include_handover else [],
        },
        "outline": [{"section": 2, "title": "private section", "subsections": subsections}],
        "frozen_contexts": {
            "rules": "private hard rule", "relations": "", "foreshadowing": "",
            "locations": "", "subplots": "", "experience": "", "factions": "",
            "items": "",
        },
    }


def _fact(identifier, value, *, verified):
    return {
        "fact_id": identifier,
        "category": "generic",
        "fact": value,
        "source_section": 1,
        "source_subsection": 1,
        "verified": verified,
    }


def test_real_source_audit_is_deterministic_for_four_subsections():
    source = _source(world_facts=[_fact("f1", "private fact", verified=False)])
    first, _ = build_audit(source, "snapshot-hash")
    second, _ = build_audit(source, "snapshot-hash")
    assert first == second
    assert first["scene_count"] == 4
    assert len(first["scenes"]) == 4
    assert all(item["frame_hash_deterministic"] for item in first["scenes"])


def test_classifications_are_mutually_exclusive_and_status_is_preserved():
    source = _source(world_facts=[
        _fact("f1", "private confirmed fact", verified=True),
        _fact("f2", "private unknown fact", verified=False),
    ])
    public, private = build_audit(source, "snapshot-hash")
    assert public["mechanical_checks"]["unknown_conflicted_retention_100"] is True
    assert public["mechanical_checks"]["planned_hard_intrusions_zero"] is True
    for scene in private["scenes"]:
        for item in scene["assertion_ledger"]:
            assert item["classification"] in CLASSIFICATIONS
        total = sum(scene["classification_counts"].values())
        assert total == len(scene["assertion_ledger"])
        assert scene["status_preserved"] is True


def test_duplicate_values_are_not_counted_as_duplicate_assertions():
    source = _source(world_facts=[
        _fact("f1", "same private fact", verified=True),
        _fact("f2", "same private fact", verified=True),
    ])
    public, private = build_audit(source, "snapshot-hash")
    for scene in private["scenes"]:
        world = scene["source_metrics"]["world_state"]
        assert world["duplicate_value_hash_count"] == 1
        assert world["assertion_count"] == 1
    assert public["mechanical_checks"]["duplicate_classification_zero"] is True


def test_unstructured_handover_is_reported_without_text_inference():
    public, private = build_audit(_source(), "snapshot-hash")
    assert public["summary"]["non_structured_handover_entries"] == 8
    for scene in private["scenes"]:
        handover = scene["source_metrics"]["handover"]
        assert handover["unstructured_handover_entries"] == 2
        assert handover["predicates"] == {"continuity_state": 1, "open_loop": 1}


def test_public_audit_contains_no_private_values():
    source = _source(world_facts=[_fact("f1", "TOP SECRET STORY VALUE", verified=False)])
    public, _ = build_audit(source, "snapshot-hash")
    rendered = json.dumps(public, ensure_ascii=False)
    assert "TOP SECRET STORY VALUE" not in rendered
    assert "private current state" not in rendered
    assert "private handover state" not in rendered
    assert public["contains_story_text"] is False


def test_missing_real_state_is_not_hidden_by_outline_plans():
    source = _source(include_arcs=False, include_handover=False)
    public, _ = build_audit(source, "snapshot-hash")
    assert public["diagnosis"] == "insufficient_real_source_data"
