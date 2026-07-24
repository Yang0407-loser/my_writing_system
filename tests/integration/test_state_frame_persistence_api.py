from fastapi.testclient import TestClient

from app.main import app
from app.routers import state_frames
from app.task_store import TaskStore
from app.writing.state_frame_persistence import (
    StateFrameHistoryRecorder,
    merge_history_into_analysis,
    normalize_history,
)

from tests.unit.test_state_frame_persistence import (
    FakeBlackboard,
    _bundle,
)


def _persisted_history():
    blackboard = FakeBlackboard()
    recorder = StateFrameHistoryRecorder(blackboard, "task")
    recorder.capture_before(
        section=1,
        subsection=1,
        prompt_messages_hash="prompt-hash",
        checkpoint_version="v1",
    )
    blackboard.data["post_write_extraction_shadow"].append(
        _bundle(1, 1, "output-hash")
    )
    recorder.capture_after(
        section=1,
        subsection=1,
        prompt_messages_hash="prompt-hash",
        output_sha256="output-hash",
        checkpoint_version="v1",
        commit_idempotency_key="task:1:1",
    )
    return normalize_history(blackboard.data["state_frame_history_v1"])


class EmptyBlackboard:
    def get_all(self, task_id):
        return {}

    def load_checkpoint(self, task_id):
        return {}


def test_api_falls_back_to_task_store_when_redis_is_empty(monkeypatch, tmp_path):
    history = _persisted_history()
    db_path = tmp_path / "tasks.db"
    store = TaskStore(str(db_path))
    store.save("task", {
        "analysis": merge_history_into_analysis(
            {"kept": True}, history.model_dump(mode="json")
        )
    })
    store._conn.close()
    monkeypatch.setattr(state_frames, "bb", EmptyBlackboard())
    monkeypatch.setattr(state_frames.settings, "TASK_DB_PATH", str(db_path))
    response = TestClient(app).get("/tasks/task/state-frame/1/1")
    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "task_history"
    assert payload["reconstructed"] is False
    assert payload["before"] is not None
    assert payload["after"] is not None
    assert payload["delta"] is not None


def test_old_task_returns_explicit_historical_unavailable(monkeypatch, tmp_path):
    missing = tmp_path / "missing.db"
    monkeypatch.setattr(state_frames, "bb", EmptyBlackboard())
    monkeypatch.setattr(state_frames.settings, "TASK_DB_PATH", str(missing))
    response = TestClient(app).get("/tasks/old-task/state-frame/1/1")
    assert response.status_code == 404
    assert response.json()["detail"] == "historical_state_frame_unavailable"
    assert not missing.exists()


def test_capture_hooks_surround_writer_and_follow_commit_observers():
    source = (
        __import__("pathlib").Path("app/agents/writer.py").read_text(encoding="utf-8")
    )
    before = source.index("state_frame_history.capture_before")
    prompt_build = source.index("prompt_artifact = PromptBuilder().build")
    writer_call = source.index("raw_output = self._generate_with_retry")
    commit = source.index("commit_artifact = state_committer.commit_subsection")
    observer = source.index("shadow_post_write_extractor.observe_committed")
    after = source.index("state_frame_history.capture_after")
    assert before < prompt_build < writer_call < commit < observer < after
    assert 'idempotency_key=f"{task_id}:{section_num}:{sub_num}"' in source
