import ast
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.writing.state_frame import StateFrameCompiler
from app.writing.story_state_view import StoryStateView


def _snapshot(assertions):
    values = [item["value"] for item in assertions]
    text = "\n".join(values)
    sources = []
    offset = 0
    normalized = []
    for index, item in enumerate(assertions, 1):
        value = item["value"]
        evidence_id = f"ev:{index}"
        sources.append({
            "evidence_id": evidence_id,
            "source_id": f"source:{index}",
            "source_type": "test_state",
            "text": text,
            "section": 2,
            "subsection": 3,
            "span_start": offset,
            "span_end": offset + len(value),
        })
        normalized.append({
            "assertion_id": f"a:{index}",
            "subject": item.get("subject", "scene"),
            "predicate": item["predicate"],
            "value": value,
            "status": item["status"],
            "evidence_ids": [evidence_id],
        })
        offset += len(value) + 1
    return StoryStateView(task_id="task", section=2, subsection=3).project(sources, normalized)


def test_state_frame_classifies_current_state_and_preserves_epistemic_status():
    snapshot = _snapshot([
        {"predicate": "current_time_anchor", "value": "Saturday evening", "status": "confirmed"},
        {"predicate": "current_location", "value": "bakery", "status": "confirmed"},
        {"predicate": "character_presence", "value": "Lin and Zhou are present", "status": "confirmed"},
        {"predicate": "character_state", "value": "Lin has resigned", "status": "confirmed"},
        {"predicate": "relationship_stage", "value": "trusted partners", "status": "confirmed"},
        {"predicate": "open_loop", "value": "invitation unanswered", "status": "planned"},
        {"predicate": "unverified_character_fact", "value": "father status unknown", "status": "unknown"},
    ])
    frame = StateFrameCompiler().compile(snapshot)
    assert [item.value for item in frame.temporal_state] == ["Saturday evening"]
    assert [item.value for item in frame.location_state] == ["bakery"]
    assert [item.value for item in frame.character_presence] == ["Lin and Zhou are present"]
    assert [item.value for item in frame.persistent_state] == ["Lin has resigned"]
    assert [item.value for item in frame.relationship_state] == ["trusted partners"]
    assert [item.value for item in frame.open_loops] == ["invitation unanswered"]
    assert frame.unknowns_and_conflicts[0].status == "unknown"
    assert len(frame.evidence) == 7


def test_state_frame_excludes_scene_plans_hard_rules_and_unclassified_history():
    snapshot = _snapshot([
        {"predicate": "planned_event", "value": "ask the question", "status": "planned"},
        {"predicate": "hard_constraint", "value": "stay in character", "status": "confirmed"},
        {"predicate": "arc_milestone", "value": "old event", "status": "confirmed"},
        {"predicate": "continuity_state", "value": "debt remains unpaid", "status": "confirmed"},
    ])
    frame = StateFrameCompiler().compile(snapshot)
    assert [item.value for item in frame.persistent_state] == ["debt remains unpaid"]
    assert frame.excluded_assertion_ids == ["a:1", "a:2", "a:3"]
    assert {item.evidence_id for item in frame.evidence} == {"ev:4"}
    rendered = StateFrameCompiler().render(frame)
    assert "ask the question" not in rendered
    assert "stay in character" not in rendered
    assert "old event" not in rendered


def test_state_frame_uses_explicit_predicates_not_keywords():
    snapshot = _snapshot([
        {"predicate": "misc_note", "value": "Saturday at the bakery with Lin", "status": "confirmed"},
    ])
    frame = StateFrameCompiler().compile(snapshot)
    assert frame.temporal_state == []
    assert frame.location_state == []
    assert frame.character_presence == []
    assert frame.excluded_assertion_ids == ["a:1"]


def test_state_frame_is_deterministic_traceable_and_frozen():
    snapshot = _snapshot([
        {"predicate": "world_fact", "value": "the bakery exists", "status": "confirmed"},
        {"predicate": "location_state", "value": "opening status conflicted", "status": "conflicted"},
    ])
    compiler = StateFrameCompiler()
    first = compiler.compile(snapshot)
    second = compiler.compile(snapshot)
    assert first == second
    assert first.frame_hash == second.frame_hash
    referenced = {
        evidence_id
        for group in (
            first.persistent_state,
            first.unknowns_and_conflicts,
        )
        for item in group
        for evidence_id in item.evidence_ids
    }
    assert referenced == {item.evidence_id for item in first.evidence}
    with pytest.raises((ValidationError, TypeError)):
        first.section = 9


def test_production_writer_does_not_import_state_frame():
    tree = ast.parse(Path("app/agents/writer.py").read_text(encoding="utf-8"))
    imports = [node for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))]
    rendered = "\n".join(ast.unparse(node) for node in imports)
    assert "state_frame" not in rendered
    assert "StateFrame" not in rendered
