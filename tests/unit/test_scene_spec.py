import ast
from pathlib import Path

import pytest

from app.writing.scene_compiler import SceneCompiler
from app.writing.story_state_view import StoryStateView


def source(text="current Saturday"):
    return [{
        "evidence_id": "ev:1", "source_id": "outline:1", "source_type": "current_outline",
        "text": text, "section": 2, "subsection": 1, "span_start": 0, "span_end": len(text),
    }]


def assertion(status="unknown", predicate="location_operation", value="shop status unknown"):
    return [{
        "assertion_id": "a:1", "subject": "shop", "predicate": predicate,
        "value": value, "status": status, "evidence_ids": ["ev:1"],
    }]


def test_unknown_is_not_promoted_and_is_traceable():
    snapshot = StoryStateView(task_id="t", section=2, subsection=1).project(source(), assertion())
    spec = SceneCompiler().compile(snapshot)
    assert spec.confirmed_state == []
    assert spec.unknowns_and_conflicts[0].status == "unknown"
    assert spec.unknowns_and_conflicts[0].evidence_ids == ["ev:1"]
    assert spec.evidence[0].text_hash


def test_conflicts_remain_conflicts():
    snapshot = StoryStateView(task_id="t", section=2, subsection=1).project(
        source(), assertion(status="conflicted", predicate="current_time_anchor", value="Friday vs Saturday")
    )
    spec = SceneCompiler().compile(snapshot)
    assert spec.unknowns_and_conflicts[0].status == "conflicted"


def test_absence_generates_operation_inference_guard():
    items = assertion(status="confirmed", predicate="character_absence", value="Lin is absent")
    items += [{
        "assertion_id": "a:2", "subject": "shop", "predicate": "location_operation",
        "value": "unknown", "status": "unknown", "evidence_ids": ["ev:1"],
    }]
    snapshot = StoryStateView(task_id="t", section=2, subsection=1).project(source(), items)
    spec = SceneCompiler().compile(snapshot)
    assert any(item.assertion_id == "forbid:character_absence:location_operation" for item in spec.forbidden_inferences)


def test_projection_rejects_missing_evidence_and_bad_spans():
    with pytest.raises(ValueError, match="existing evidence"):
        StoryStateView(task_id="t", section=1, subsection=1).project(source(), [{
            **assertion()[0], "evidence_ids": ["missing"]
        }])
    bad = source("abc")
    bad[0]["span_end"] = 9
    with pytest.raises(ValueError, match="outside"):
        StoryStateView(task_id="t", section=1, subsection=1).project(bad, [])


def test_scene_compilation_is_deterministic():
    view = StoryStateView(task_id="t", section=2, subsection=1)
    first = SceneCompiler().compile(view.project(source(), assertion()))
    second = SceneCompiler().compile(view.project(source(), assertion()))
    assert first == second


def test_production_writer_uses_canary_boundary_not_r2_low_level_modules():
    path = Path("app/agents/writer.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports = [node for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))]
    rendered = "\n".join(ast.unparse(node) for node in imports)
    imported_names = {
        alias.name
        for node in imports
        for alias in node.names
    }
    assert "scene_compiler" not in rendered
    assert "story_state_view" not in rendered
    assert "SceneSpec" not in imported_names
    assert "SceneSpecCanaryController" in imported_names


def test_runtime_projection_uses_only_read_apis_and_keeps_unverified_unknown():
    class World:
        def __init__(self):
            self.reads = 0

        def get_all_facts(self):
            self.reads += 1
            return [
                {"fact_id": "known", "fact": "the shop exists", "verified": True},
                {"fact_id": "maybe", "fact": "the shop is open", "verified": False},
            ]

        def get_contradictions(self):
            self.reads += 1
            return [{"old_fact": "Friday", "new_fact": "Saturday"}]

        def consume_warnings(self):
            raise AssertionError("mutating API must not be called")

        def add_fact(self, *_args):
            raise AssertionError("write API must not be called")

    world = World()
    snapshot = StoryStateView(task_id="t", section=2, subsection=1).project_runtime(
        current_outline={"source_id": "outline:2.1", "planned_events": ["write the scene"]},
        world_state=world,
        handover={"source_id": "handover:1", "confirmed_state": ["Saturday"], "open_loops": ["reply pending"]},
    )
    by_id = {item.assertion_id: item for item in snapshot.assertions}
    assert by_id["state:world_state:known"].status == "confirmed"
    assert by_id["state:world_state:maybe"].status == "unknown"
    assert any(item.status == "conflicted" for item in snapshot.assertions)
    assert any(item.predicate == "open_loop" for item in snapshot.open_loops)
    assert all(item.span_start == 0 and item.span_end is not None for item in snapshot.evidence)
    assert world.reads == 2
