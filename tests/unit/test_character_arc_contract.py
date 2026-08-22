import copy
import json
from unittest.mock import MagicMock

from app.character_arc_contract import (
    HARD_ARC_TRANSITION,
    OBSERVATIONAL_TEXTURE,
    SOFT_ARC_PROGRESS,
    UNSUPPORTED_PLANNING_INFERENCE,
    build_v2_edge_plan,
    count_legacy_link_operations,
    interpret_legacy_milestone,
    iter_v2_event_milestones,
    normalize_v2_arcs,
    resolve_contract_version,
)


def _outline():
    return [{
        "section": 1,
        "subsections": [
            {"subsection": 1, "title": "起点", "description": "发生变化", "key_points": ["选择"]},
            {"subsection": 2, "title": "推进", "description": "继续变化", "key_points": ["行动"]},
            {"subsection": 3, "title": "结果", "description": "完成变化", "key_points": ["结果"]},
        ],
    }]


def _hard(milestone_id, subsection, before, after):
    return {
        "milestone_id": milestone_id,
        "section": 1,
        "subsection": subsection,
        "event": f"事件{milestone_id}",
        "classification": HARD_ARC_TRANSITION,
        "before_state": before,
        "trigger": "明确触发",
        "after_state": after,
        "observable_evidence": "可观察行为",
        "rationale": "状态发生显著变化",
    }


def test_contract_version_defaults_invalid_values_to_v1():
    assert resolve_contract_version(None) == "v1"
    assert resolve_contract_version("invalid") == "v1"
    assert resolve_contract_version("V2") == "v2"


def test_legacy_compatibility_view_is_soft_without_mutating_stored_data():
    original = {"section": 1, "subsection": 1, "event": "旧事件"}
    viewed = interpret_legacy_milestone(original)
    assert viewed["classification"] == SOFT_ARC_PROGRESS
    assert viewed["requiredness"] == "soft"
    assert viewed["legacy_unclassified"] is True
    assert "classification" not in original


def test_v2_missing_classification_is_unresolved_not_hard():
    arcs = [{"character_id": "c1", "key_milestones": [{
        "section": 1, "subsection": 1, "event": "普通动作"
    }]}]
    normalized = normalize_v2_arcs(arcs, _outline())
    milestone = normalized[0]["key_milestones"][0]
    assert milestone["classification"] == "unresolved"
    assert milestone["requiredness"] == "unresolved"


def test_old_checkpoint_can_be_viewed_as_soft_without_rewriting_input():
    arcs = [{"character_id": "c1", "key_milestones": [{
        "section": 1, "subsection": 1, "event": "旧事件"
    }]}]
    original = copy.deepcopy(arcs)
    normalized = normalize_v2_arcs(arcs, _outline(), legacy_unclassified_as_soft=True)
    milestone = normalized[0]["key_milestones"][0]
    assert milestone["classification"] == SOFT_ARC_PROGRESS
    assert milestone["legacy_unclassified"] is True
    assert arcs == original


def test_incomplete_hard_transition_is_downgraded_with_provenance():
    arcs = [{"character_id": "c1", "key_milestones": [{
        "section": 1, "subsection": 1, "event": "改变", "classification": HARD_ARC_TRANSITION,
        "before_state": "犹豫", "after_state": "坚定", "rationale": "发生变化",
    }]}]
    milestone = normalize_v2_arcs(arcs, _outline())[0]["key_milestones"][0]
    assert milestone["classification"] == SOFT_ARC_PROGRESS
    assert milestone["downgrade_reason"] == "incomplete_hard_transition"
    assert milestone["source_id"] == "outline:S1.1"
    assert len(milestone["source_hash"]) == 64


def test_hard_limit_downgrades_third_transition_per_character_section():
    arcs = [{"character_id": "c1", "key_milestones": [
        _hard("m1", 1, "A", "B"),
        _hard("m2", 2, "B", "C"),
        _hard("m3", 3, "C", "D"),
    ]}]
    items = normalize_v2_arcs(arcs, _outline())[0]["key_milestones"]
    assert [item["classification"] for item in items] == [
        HARD_ARC_TRANSITION, HARD_ARC_TRANSITION, SOFT_ARC_PROGRESS,
    ]
    assert items[2]["downgrade_reason"] == "hard_arc_limit"


def test_only_hard_and_soft_milestones_are_writer_event_candidates():
    arcs = [{"character_id": "c1", "key_milestones": [
        {"milestone_id": "hard", "classification": HARD_ARC_TRANSITION},
        {"milestone_id": "soft", "classification": SOFT_ARC_PROGRESS},
        {"milestone_id": "texture", "classification": OBSERVATIONAL_TEXTURE},
        {"milestone_id": "unsupported", "classification": UNSUPPORTED_PLANNING_INFERENCE},
    ]}]
    assert [item[1]["milestone_id"] for item in iter_v2_event_milestones(arcs)] == ["hard", "soft"]


def test_explicit_and_state_backed_edges_only_no_same_section_pairwise_edges():
    first = _hard("m1", 1, "A", "B")
    second = _hard("m2", 2, "B", "C")
    second["depends_on"] = ["m1"]
    second["dependency_rationale"] = "必须先完成m1"
    arcs = normalize_v2_arcs([{"character_id": "c1", "key_milestones": [first, second]}], _outline())
    edges = build_v2_edge_plan(arcs)
    assert [edge["edge_type"] for edge in edges] == ["explicit_dependency"]
    assert all(edge["rationale"] for edge in edges)
    assert all(edge["source_ids"] and edge["source_hashes"] for edge in edges)
    assert all(edge["contract_version"] == "v2" for edge in edges)


def test_state_chain_creates_ordered_edge_when_no_stronger_explicit_edge_exists():
    arcs = normalize_v2_arcs([{"character_id": "c1", "key_milestones": [
        _hard("m1", 1, "A", "B"), _hard("m2", 2, "B", "C"),
    ]}], _outline())
    edges = build_v2_edge_plan(arcs)
    assert len(edges) == 1
    assert edges[0]["edge_type"] == "ordered_hard_transition"


def test_legacy_link_count_reproduces_pairwise_density():
    arcs = [
        {"character_id": "c1", "key_milestones": [
            {"section": 1}, {"section": 1}, {"section": 1},
        ]},
        {"character_id": "c2", "key_milestones": [
            {"section": 1}, {"section": 1},
        ]},
    ]
    assert count_legacy_link_operations(arcs) == {
        "milestones": 5,
        "same_character_consecutive_links": 3,
        "same_section_pairwise_links": 10,
        "link_operations": 13,
    }


def test_v2_normalization_is_deterministic():
    arcs = [{"character_id": "c1", "key_milestones": [_hard("", 1, "A", "B")]}]
    assert normalize_v2_arcs(arcs, _outline()) == normalize_v2_arcs(arcs, _outline())


def test_character_manager_v1_keeps_legacy_response_shape(monkeypatch):
    from app.agents.character_manager import CharacterManager, settings

    monkeypatch.setattr(settings, "CHARACTER_ARC_CONTRACT_VERSION", "v1")
    manager = CharacterManager.__new__(CharacterManager)
    manager.llm = MagicMock()
    manager.last_raw_response = ""
    legacy = [{
        "character_id": "c1", "starting_state": "A", "ending_state": "B",
        "key_milestones": [{"section": 1, "subsection": 1, "event": "旧事件"}],
    }]
    manager.llm.chat_completion.return_value = json.dumps(legacy, ensure_ascii=False)
    result = manager.plan_arcs([{"id": "c1", "name": "甲"}], _outline())
    assert result[0]["key_milestones"] == legacy[0]["key_milestones"]
    assert result[0]["current_state"] == "A"
    assert "classification" not in result[0]["key_milestones"][0]


def test_character_manager_v2_normalizes_without_extra_llm_calls(monkeypatch):
    from app.agents.character_manager import CharacterManager, settings

    monkeypatch.setattr(settings, "CHARACTER_ARC_CONTRACT_VERSION", "v2")
    manager = CharacterManager.__new__(CharacterManager)
    manager.llm = MagicMock()
    manager.last_raw_response = ""
    response = [{
        "character_id": "c1", "starting_state": "A", "ending_state": "B",
        "key_milestones": [_hard("m1", 1, "A", "B")],
    }]
    manager.llm.chat_completion.return_value = json.dumps(response, ensure_ascii=False)
    result = manager.plan_arcs([{"id": "c1", "name": "甲"}], _outline())
    milestone = result[0]["key_milestones"][0]
    assert milestone["classification"] == HARD_ARC_TRANSITION
    assert milestone["requiredness"] == "hard"
    assert milestone["source_id"] == "outline:S1.1"
    manager.llm.chat_completion.assert_called_once()
