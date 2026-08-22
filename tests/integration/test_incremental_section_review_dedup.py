from unittest.mock import MagicMock

from app import coordinator
from app.writing.character_state_propagation import character_arcs_hash


def test_final_section_and_global_review_remain_when_incremental_is_disabled(monkeypatch):
    calls = []

    class FakeReviewer:
        def review_section(self, section, topic, style, text):
            calls.append(("section", section, topic, text))
            return {"score": 8, "scores": {"pace": 8}}

        def review_global(self, **kwargs):
            calls.append(("global", kwargs["topic"], kwargs["total_words"]))
            return {"global_score": 8, "top_3_actions": []}

    monkeypatch.setattr(coordinator, "Reviewer", FakeReviewer)
    monkeypatch.setattr("app.style_stats.style_report", lambda *_: {})
    monkeypatch.setattr(
        "app.foreshadowing_store.get_foreshadowing_summary",
        lambda *_: {
            "health": "healthy", "resolved": 0, "total": 0, "broken": 0,
        },
    )
    monkeypatch.setattr(
        "app.handover_penetration.compute_handover_penetration",
        lambda *_: {"verdict": "n/a", "total_hits": 0, "total_keywords": 0,
                    "overall_penetration": 0.0},
    )
    monkeypatch.setattr("app.subplot_manager.build_subplot_context", lambda *_: "")
    monkeypatch.setattr("app.character_relation_store.build_relation_context", lambda *_: "")
    monkeypatch.setattr(coordinator, "_add_timeline", lambda *_args, **_kwargs: None)

    blackboard = MagicMock()
    state = {
        "config_topic": "topic",
        "style_profile": {},
        "section_texts": {2: "final section text"},
        "outline_v2": [{"section": 2, "title": "section", "subsections": []}],
        "handover_chain": [],
        "fix_checklist": {},
        "characters": [],
        "character_arcs": [],
    }
    result = coordinator._phase_review(blackboard, "task-id", state)

    assert [call[0] for call in calls] == ["section", "global"]
    assert result["review_result"]["section_reviews"][0]["score"] == 8
    assert result["review_result"]["global_score"] == 8
    blackboard.set.assert_any_call("task-id", "review", result["review_result"])


def test_final_reviewer_receives_checkpoint_character_state(monkeypatch):
    captured = {}

    class FakeReviewer:
        def review_section(self, section, topic, style, text):
            return {"score": 8, "scores": {}}

        def review_global(self, **kwargs):
            captured["character_arcs"] = kwargs["character_arcs"]
            return {"global_score": 8, "top_3_actions": []}

    monkeypatch.setattr(coordinator, "Reviewer", FakeReviewer)
    monkeypatch.setattr("app.style_stats.style_report", lambda *_: {})
    monkeypatch.setattr(
        "app.foreshadowing_store.get_foreshadowing_summary",
        lambda *_: {"health": "healthy", "resolved": 0, "total": 0, "broken": 0},
    )
    monkeypatch.setattr(
        "app.handover_penetration.compute_handover_penetration",
        lambda *_: {"verdict": "n/a", "total_hits": 0, "total_keywords": 0,
                    "overall_penetration": 0.0},
    )
    monkeypatch.setattr("app.subplot_manager.build_subplot_context", lambda *_: "")
    monkeypatch.setattr("app.character_relation_store.build_relation_context", lambda *_: "")
    monkeypatch.setattr(coordinator, "_add_timeline", lambda *_args, **_kwargs: None)

    arcs = [{"character_id": "c1", "current_state": "updated"}]
    state_hash = character_arcs_hash(arcs)
    state = {
        "config_topic": "topic",
        "style_profile": {},
        "section_texts": {1: "section"},
        "outline_v2": [{"section": 1, "title": "section", "subsections": []}],
        "handover_chain": [],
        "fix_checklist": {},
        "characters": [{"id": "c1", "name": "Character"}],
        "character_arcs": arcs,
        "_character_state_propagation": {
            "updated_state_hash": state_hash,
            "coordinator_state_hash": state_hash,
            "checkpoint_state_hash": state_hash,
        },
    }

    result = coordinator._phase_review(MagicMock(), "task-id", state)

    assert captured["character_arcs"] == arcs
    propagation = result["_character_state_propagation"]
    assert propagation["reviewer_state_hash"] == state_hash
    assert propagation["updated_state_hash"] == state_hash
    assert propagation["checkpoint_state_hash"] == state_hash
