"""Arc milestones must not accumulate when the writing phase is re-entered.

Background: a real run (2026-07-26) hit an Ollama outage, Celery retried the
task 5 times, and each retry regenerated character_arcs and appended another
paraphrased copy of every milestone. pre_check() then handed Writer 20-28
"required events" that were ~6 rewordings of 3-4 real beats, inflating the
prompt and pushing every subsection 2.0-2.7x over its word target.

The acceptance criterion is deterministic and needs no judge:
    re-entering the rebuild N times must not change the required-event count.
"""

import pytest

from app.narrative_event import EventGraph
from app.rule_checks import pre_check


class FakeBlackboard:
    """In-memory stand-in that round-trips through the same set/get contract."""

    def __init__(self):
        self.store: dict[str, dict] = {}

    def set(self, task_id, key, value):
        self.store.setdefault(task_id, {})[key] = value

    def get(self, task_id, key):
        return self.store.get(task_id, {}).get(key)


# Two planner runs over the same outline: same beats, different wording.
ARCS_RUN_1 = [
    {
        "character_id": "linwan",
        "key_milestones": [
            {"event": "林晚在凌晨收到第20版文案的驳回，麻木地写完辞职信", "section": 1, "subsection": 1},
            {"event": "林晚连续三个周六蹲守，终于拍到周野揉面的背影", "section": 1, "subsection": 2},
        ],
    },
    {
        "character_id": "jiqing",
        "key_milestones": [
            {"event": "季晴在深夜加班时收到林晚的辞职消息，立刻打电话过去", "section": 1, "subsection": 1},
        ],
    },
]

ARCS_RUN_2 = [
    {
        "character_id": "linwan",
        "key_milestones": [
            # same beat, reworded — a content hash would NOT catch this
            {"event": "林晚凌晨收到第20版驳回邮件后写完辞职信，闻到面包香", "section": 1, "subsection": 1},
            {"event": "林晚连续三个周六蹲守，终于拍到周野揉面的背影", "section": 1, "subsection": 2},
        ],
    },
    {
        "character_id": "jiqing",
        "key_milestones": [
            {"event": "季晴在午休时收到林晚的辞职消息，用HR口吻分析裸辞风险", "section": 1, "subsection": 1},
        ],
    },
]


def rebuild(graph, arcs):
    """The legacy-path rebuild performed by _phase_writing, in miniature."""
    reset = graph.reset_arc_milestones()
    for arc in arcs:
        for milestone in arc["key_milestones"]:
            graph.add_arc_milestone(
                description=milestone["event"],
                section=milestone["section"],
                subsection=milestone["subsection"],
                character_id=arc["character_id"],
                weight=5,
            )
    restored = graph.restore_milestone_status(reset["carried_status"])
    return reset, restored


def milestone_count(bb, task_id):
    return len(EventGraph(bb, task_id)._events)


@pytest.fixture
def bb():
    return FakeBlackboard()


# ---------------------------------------------------------------------------
# the regression this fix exists for
# ---------------------------------------------------------------------------


def test_six_attempts_do_not_inflate_required_events(bb):
    """The exact shape of the 2026-07-26 incident: 6 attempts, same outline."""
    counts = []
    for attempt in range(6):
        graph = EventGraph(bb, "t1")
        # planner output alternates wording, as a real LLM at temp=0.4 does
        rebuild(graph, ARCS_RUN_1 if attempt % 2 == 0 else ARCS_RUN_2)
        counts.append(len(pre_check(EventGraph(bb, "t1"), 1, 0)["required"]))

    assert counts == [3, 3, 3, 3, 3, 3], f"required-event count drifted: {counts}"


def test_without_reset_the_graph_would_grow(bb):
    """Pin the old behaviour so the test proves the fix is what stops it."""
    for _ in range(3):
        graph = EventGraph(bb, "t2")
        for arc in ARCS_RUN_1:  # no reset_arc_milestones() call
            for milestone in arc["key_milestones"]:
                graph.add_arc_milestone(
                    description=milestone["event"],
                    section=milestone["section"],
                    subsection=milestone["subsection"],
                    character_id=arc["character_id"],
                )
    assert milestone_count(bb, "t2") == 9  # 3 arcs x 3 passes — the bug


def test_rebuild_is_idempotent_for_identical_arcs(bb):
    for _ in range(4):
        rebuild(EventGraph(bb, "t3"), ARCS_RUN_1)
    assert milestone_count(bb, "t3") == 3


def test_reworded_milestones_replace_rather_than_join(bb):
    rebuild(EventGraph(bb, "t4"), ARCS_RUN_1)
    rebuild(EventGraph(bb, "t4"), ARCS_RUN_2)

    required = pre_check(EventGraph(bb, "t4"), 1, 0)["required"]
    assert len(required) == 3
    assert any("面包香" in item for item in required)          # run 2 wording kept
    assert not any("麻木地写完辞职信" in item for item in required)  # run 1 wording gone


def test_prompt_text_stays_bounded_across_attempts(bb):
    """The prompt block is what actually reached Writer; bound that too."""
    lengths = []
    for attempt in range(5):
        rebuild(EventGraph(bb, "t5"), ARCS_RUN_1 if attempt % 2 else ARCS_RUN_2)
        lengths.append(len(pre_check(EventGraph(bb, "t5"), 1, 0)["prompt_text"]))
    assert max(lengths) - min(lengths) < 120, f"prompt text drifted: {lengths}"


# ---------------------------------------------------------------------------
# progress must survive a rebuild that changes nothing
# ---------------------------------------------------------------------------


def test_done_status_survives_identical_rebuild(bb):
    graph = EventGraph(bb, "t6")
    rebuild(graph, ARCS_RUN_1)
    graph.update_arc_status("linwan", "done")
    assert graph.get_summary()["arc_milestones_done"] == 2

    _, restored = rebuild(EventGraph(bb, "t6"), ARCS_RUN_1)

    assert restored == 2
    assert EventGraph(bb, "t6").get_summary()["arc_milestones_done"] == 2


def test_status_is_not_carried_onto_a_different_milestone(bb):
    graph = EventGraph(bb, "t7")
    rebuild(graph, ARCS_RUN_1)
    graph.update_arc_status("jiqing", "done")

    rebuild(EventGraph(bb, "t7"), ARCS_RUN_2)  # jiqing's beat was reworded

    summary = EventGraph(bb, "t7").get_summary()
    assert summary["arc_milestones_total"] == 3
    assert summary["arc_milestones_done"] == 0  # reworded beat starts pending


def test_deviated_status_also_carries(bb):
    graph = EventGraph(bb, "t8")
    rebuild(graph, ARCS_RUN_1)
    graph.update_arc_by_section(1, "deviated")

    rebuild(EventGraph(bb, "t8"), ARCS_RUN_1)

    assert EventGraph(bb, "t8").get_summary()["arc_milestones_deviated"] == 3


# ---------------------------------------------------------------------------
# edges and empty cases
# ---------------------------------------------------------------------------


def test_reset_reports_what_it_removed(bb):
    rebuild(EventGraph(bb, "t9"), ARCS_RUN_1)
    reset = EventGraph(bb, "t9").reset_arc_milestones()
    assert reset["removed"] == 3
    assert reset["carried_status"] == {}


def test_reset_on_empty_graph_is_a_noop(bb):
    reset = EventGraph(bb, "t10").reset_arc_milestones()
    assert reset == {"removed": 0, "carried_status": {}}


def test_restore_with_empty_ledger_changes_nothing(bb):
    graph = EventGraph(bb, "t11")
    rebuild(graph, ARCS_RUN_1)
    assert graph.restore_milestone_status({}) == 0


def test_stale_edges_do_not_survive_the_rebuild(bb):
    graph = EventGraph(bb, "t12")
    rebuild(graph, ARCS_RUN_1)
    ids = list(graph._events)
    graph.link_events(ids[0], ids[1])
    assert graph._events[ids[0]].related_events

    rebuild(EventGraph(bb, "t12"), ARCS_RUN_1)

    reloaded = EventGraph(bb, "t12")
    assert len(reloaded._events) == 3
    for event in reloaded._events.values():
        for neighbour in event.related_events:
            assert neighbour in reloaded._events, "dangling edge survived rebuild"


def test_other_tasks_are_untouched(bb):
    rebuild(EventGraph(bb, "task-a"), ARCS_RUN_1)
    rebuild(EventGraph(bb, "task-b"), ARCS_RUN_1)

    EventGraph(bb, "task-a").reset_arc_milestones()

    assert milestone_count(bb, "task-a") == 0
    assert milestone_count(bb, "task-b") == 3
