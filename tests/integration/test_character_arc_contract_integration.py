from app.agents.character_formatter import CharacterFormatter
from app.narrative_event import EventGraph
from app.rule_checks import pre_check


class MemoryBoard:
    def __init__(self):
        self.data = {}

    def set(self, task_id, key, value):
        self.data[(task_id, key)] = value

    def get(self, task_id, key):
        return self.data.get((task_id, key))


def test_v1_event_serialization_and_prompt_semantics_stay_legacy():
    board = MemoryBoard()
    graph = EventGraph(board, "task-v1")
    first = graph.add_arc_milestone("旧硬事件一", 1, 1, "c1", 5)
    second = graph.add_arc_milestone("旧硬事件二", 1, 1, "c2", 5)
    graph.link_events(first, second)

    stored = board.data[("task-v1", "event_graph")]
    assert "classification" not in stored[0]
    assert pre_check(graph, 1, 1)["required"] == ["旧硬事件一", "旧硬事件二"]
    assert len(graph.expand_causal([graph._events[first]])) == 2

    arcs = [{"character_id": "c1", "key_milestones": [{
        "section": 1, "subsection": 1, "event": "旧硬事件一", "location": "店内",
        "time": "凌晨", "emotional_shift": "A→B",
    }]}]
    characters = [{"id": "c1", "name": "甲"}]
    rendered = CharacterFormatter.build_arc_context(characters, arcs, section=1, subsection=1)
    assert "【本小节关键事件】旧硬事件一" in rendered
    assert "非强制弧线参考" not in rendered


def test_v2_soft_context_is_not_mandatory_and_same_section_is_not_implicit_edge():
    board = MemoryBoard()
    graph = EventGraph(board, "task-v2")
    hard = graph.add_arc_milestone(
        "状态转变", 1, 1, "c1", 9,
        classification="hard_arc_transition", requiredness="hard", contract_version="v2",
        source_id="outline:S1.1", source_hash="a" * 64, rationale="显著变化",
    )
    soft = graph.add_arc_milestone(
        "可替代推进", 1, 1, "c1", 3,
        classification="soft_arc_progress", requiredness="soft", contract_version="v2",
        source_id="outline:S1.1", source_hash="a" * 64, rationale="允许替代",
    )

    assert pre_check(graph, 1, 1)["required"] == ["状态转变"]
    assert [event.event_id for event in graph.expand_causal([graph._events[hard]])] == [hard]

    graph.link_events(hard, soft, metadata={
        "edge_type": "explicit_dependency", "rationale": "明确依赖",
        "source_ids": ["outline:S1.1"], "source_hashes": ["a" * 64],
        "construction_rule": "test", "contract_version": "v2",
    })
    assert {event.event_id for event in graph.expand_causal([graph._events[hard]])} == {hard, soft}

    arcs = [{"character_id": "c1", "key_milestones": [
        {"section": 1, "subsection": 1, "event": "状态转变", "classification": "hard_arc_transition", "requiredness": "hard", "contract_version": "v2"},
        {"section": 1, "subsection": 1, "event": "可替代推进", "classification": "soft_arc_progress", "requiredness": "soft", "contract_version": "v2"},
        {"section": 1, "subsection": 1, "event": "环境动作", "classification": "observational_texture", "requiredness": "non_injectable", "contract_version": "v2"},
    ]}]
    rendered = CharacterFormatter.build_arc_context(
        [{"id": "c1", "name": "甲"}], arcs, section=1, subsection=1,
    )
    assert "【本小节硬弧线转变】状态转变" in rendered
    assert "【非强制弧线参考】可替代推进" in rendered
    assert "环境动作" not in rendered
