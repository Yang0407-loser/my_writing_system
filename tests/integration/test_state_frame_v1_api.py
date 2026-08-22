from fastapi.testclient import TestClient

from app.main import app
from app.routers import state_frames
from app.writing.state_frame_service import build_state_frame_artifacts


def _task_data():
    return {
        "outline": [{
            "section": 1,
            "subsections": [{
                "subsection": 1,
                "title": "one",
                "description": "character acts",
                "key_points": ["character acts"],
                "target_words": 1000,
            }],
        }],
        "characters": [{"id": "c1", "name": "character"}],
        "handover_notes": [],
        "post_write_extraction_shadow": [{
            "record": {"status": "completed"},
            "bundle": {
                "section": 1,
                "subsection": 1,
                "changes": [{
                    "change_id": "change-1",
                    "category": "character_state",
                    "subject": "character",
                    "predicate": "location",
                    "value": "shop",
                    "status": "confirmed",
                    "confidence": 0.9,
                    "evidence": [{
                        "source_id": "writer-output:task:1:1",
                        "text_hash": "output-hash",
                        "span_start": 0,
                        "span_end": 4,
                        "excerpt": "short",
                    }],
                }],
            },
        }],
    }


def test_service_builds_read_only_before_after_delta_and_quality():
    result = build_state_frame_artifacts(
        task_id="task",
        section=1,
        subsection=1,
        task_data=_task_data(),
        checkpoint={},
        relations=[],
        foreshadows=[],
    )
    assert set(result) == {
        "before", "after", "delta", "quality",
        "production_effect", "writer_llm_calls",
    }
    assert result["production_effect"] is False
    assert result["writer_llm_calls"] == 0
    assert len(result["delta"]["added_facts"]) == 1
    assert len(result["quality"]["metrics"]) == 3


def test_api_is_read_only_and_artifact_routes_do_not_return_story_text(monkeypatch):
    calls = {"set": 0}

    class FakeBlackboard:
        def get_all(self, task_id):
            return _task_data()

        def load_checkpoint(self, task_id):
            return {}

        def set(self, *args, **kwargs):
            calls["set"] += 1

    monkeypatch.setattr(state_frames, "bb", FakeBlackboard())
    monkeypatch.setattr(
        "app.character_relation_store.list_relations_read_only", lambda task_id: []
    )
    monkeypatch.setattr(
        "app.foreshadowing_store.list_foreshadowings_read_only", lambda task_id: []
    )
    client = TestClient(app)
    response = client.get("/tasks/task/state-frame/1/1")
    assert response.status_code == 200
    payload = response.json()
    assert calls["set"] == 0
    assert payload["production_effect"] is False
    rendered = response.text.lower()
    assert "prompt" not in rendered
    assert "messages" not in rendered
    assert "complete story text" not in rendered


def test_unknown_artifact_is_rejected(monkeypatch):
    class FakeBlackboard:
        def get_all(self, task_id):
            return _task_data()

        def load_checkpoint(self, task_id):
            return {}

    monkeypatch.setattr(state_frames, "bb", FakeBlackboard())
    client = TestClient(app)
    assert client.get("/tasks/task/state-frame/1/1/unknown").status_code == 404
