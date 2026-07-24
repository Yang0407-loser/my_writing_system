import hashlib
import json
import sqlite3

from app.blackboard import Blackboard
from app.task_store import TaskStore
from app.writing.state_frame_history import STATE_FRAME_HISTORY_KEY
from app.writing.state_frame_persistence import (
    StateFrameHistoryRecorder,
    legacy_checkpoint_projection,
    load_task_history_read_only,
    merge_history_into_analysis,
    normalize_history,
)


class FakeBlackboard:
    def __init__(self):
        self.data = {
            "outline": [{
                "section": 1,
                "subsections": [
                    {
                        "subsection": index,
                        "title": f"S{index}",
                        "description": f"角色完成动作{index}",
                        "key_points": [f"角色完成动作{index}"],
                        "target_words": 1000,
                    }
                    for index in range(1, 5)
                ],
            }],
            "characters": [{"id": "c1", "name": "角色"}],
            "post_write_extraction_shadow": [],
        }
        self.checkpoint = {"checkpoint_version": "test-v1"}
        self.fail_set = False

    def get_all(self, task_id):
        return dict(self.data)

    def get(self, task_id, key):
        return self.data.get(key)

    def set(self, task_id, key, value):
        if self.fail_set:
            raise RuntimeError("redis unavailable")
        self.data[key] = value

    def load_checkpoint(self, task_id):
        return dict(self.checkpoint)


def _bundle(section, subsection, output_hash):
    return {
        "record": {"status": "completed"},
        "bundle": {
            "section": section,
            "subsection": subsection,
            "changes": [{
                "change_id": f"change-{subsection}",
                "category": "character_state",
                "subject": "角色",
                "predicate": "location",
                "value": f"地点{subsection}",
                "status": "confirmed",
                "confidence": 0.9,
                "evidence": [{
                    "source_id": f"writer-output:task:1:{subsection}",
                    "text_hash": output_hash,
                    "span_start": 0,
                    "span_end": 4,
                    "excerpt": "短证据",
                }],
            }],
        },
    }


def _capture_four():
    blackboard = FakeBlackboard()
    recorder = StateFrameHistoryRecorder(blackboard, "task")
    for subsection in range(1, 5):
        prompt_hash = hashlib.sha256(f"prompt-{subsection}".encode()).hexdigest()
        output_hash = hashlib.sha256(f"output-{subsection}".encode()).hexdigest()
        assert recorder.capture_before(
            section=1,
            subsection=subsection,
            prompt_messages_hash=prompt_hash,
            checkpoint_version="test-v1",
        )
        blackboard.data["post_write_extraction_shadow"].append(
            _bundle(1, subsection, output_hash)
        )
        assert recorder.capture_after(
            section=1,
            subsection=subsection,
            prompt_messages_hash=prompt_hash,
            output_sha256=output_hash,
            checkpoint_version="test-v1",
            commit_idempotency_key=f"task:1:{subsection}",
        )
    return blackboard, normalize_history(
        blackboard.data[STATE_FRAME_HISTORY_KEY]
    )


def test_four_subsection_fixture_persists_before_after_and_delta():
    _, history = _capture_four()
    assert len(history.pending_before) == 0
    assert len(history.records) == 4
    assert all(record.before_frame for record in history.records.values())
    assert all(record.after_frame for record in history.records.values())
    assert all(record.delta for record in history.records.values())
    assert len({record.record_id for record in history.records.values()}) == 4
    assert all(
        record.commit_idempotency_key == f"task:1:{record.subsection}"
        for record in history.records.values()
    )


def test_before_has_no_current_output_and_after_is_idempotent():
    blackboard = FakeBlackboard()
    recorder = StateFrameHistoryRecorder(blackboard, "task")
    prompt_hash = hashlib.sha256(b"prompt").hexdigest()
    output_hash = hashlib.sha256(b"output").hexdigest()
    recorder.capture_before(
        section=1,
        subsection=1,
        prompt_messages_hash=prompt_hash,
        checkpoint_version="v1",
    )
    pending = normalize_history(blackboard.data[STATE_FRAME_HISTORY_KEY])
    rendered_before = json.dumps(
        next(iter(pending.pending_before.values())).before_frame.model_dump(),
        ensure_ascii=False,
    )
    assert output_hash not in rendered_before
    blackboard.data["post_write_extraction_shadow"].append(
        _bundle(1, 1, output_hash)
    )
    first = recorder.capture_after(
        section=1,
        subsection=1,
        prompt_messages_hash=prompt_hash,
        output_sha256=output_hash,
        checkpoint_version="v1",
        commit_idempotency_key="task:1:1",
    )
    second = recorder.capture_after(
        section=1,
        subsection=1,
        prompt_messages_hash=prompt_hash,
        output_sha256=output_hash,
        checkpoint_version="v1",
        commit_idempotency_key="task:1:1",
    )
    history = normalize_history(blackboard.data[STATE_FRAME_HISTORY_KEY])
    assert first == second
    assert len(history.records) == 1


def test_post_write_off_is_unavailable_and_does_not_call_extractor():
    blackboard = FakeBlackboard()
    recorder = StateFrameHistoryRecorder(blackboard, "task")
    prompt_hash = hashlib.sha256(b"prompt").hexdigest()
    output_hash = hashlib.sha256(b"output").hexdigest()
    recorder.capture_before(
        section=1,
        subsection=1,
        prompt_messages_hash=prompt_hash,
        checkpoint_version="v1",
    )
    recorder.capture_after(
        section=1,
        subsection=1,
        prompt_messages_hash=prompt_hash,
        output_sha256=output_hash,
        checkpoint_version="v1",
        commit_idempotency_key="task:1:1",
    )
    record = next(iter(
        normalize_history(blackboard.data[STATE_FRAME_HISTORY_KEY]).records.values()
    ))
    assert "post_write_state_bundle" in record.unavailable_source_types
    assert record.source_granularity["post_write_state_bundle"] == "unavailable"


def test_persistence_failure_is_fail_open():
    blackboard = FakeBlackboard()
    blackboard.fail_set = True
    recorder = StateFrameHistoryRecorder(blackboard, "task")
    assert recorder.capture_before(
        section=1,
        subsection=1,
        prompt_messages_hash="prompt-hash",
        checkpoint_version="v1",
    ) is None


def test_checkpoint_mirror_preserves_legacy_projection(monkeypatch):
    import fakeredis

    blackboard = Blackboard()
    blackboard._redis = fakeredis.FakeRedis()
    history = {"schema_version": "state-frame-history-v1", "records": {}}
    blackboard.set("task", STATE_FRAME_HISTORY_KEY, history)
    legacy = {"phase": "writing", "draft": "not persisted in frame"}
    original_hash = hashlib.sha256(
        json.dumps(legacy, sort_keys=True).encode()
    ).hexdigest()
    blackboard.save_checkpoint("task", legacy)
    restored = blackboard.load_checkpoint("task")
    projected = legacy_checkpoint_projection(restored)
    projected_hash = hashlib.sha256(
        json.dumps(projected, sort_keys=True).encode()
    ).hexdigest()
    assert restored[STATE_FRAME_HISTORY_KEY] == history
    assert projected_hash == original_hash


def test_task_store_analysis_merge_and_read_only_recovery(tmp_path):
    _, history = _capture_four()
    merged = merge_history_into_analysis(
        {"existing_metric": {"value": 1}},
        history.model_dump(mode="json"),
    )
    db_path = tmp_path / "tasks.db"
    store = TaskStore(str(db_path))
    store.save("task", {"analysis": merged})
    store._conn.close()
    recovered = load_task_history_read_only(str(db_path), "task")
    assert recovered is not None
    assert len(recovered.records) == 4
    connection = sqlite3.connect(db_path)
    columns = {
        row[1]
        for row in connection.execute("PRAGMA table_info(task_history)").fetchall()
    }
    connection.close()
    assert "state_frame_history_v1" not in columns
    task = TaskStore(str(db_path))
    assert task.get("task")["analysis_json"]["existing_metric"]["value"] == 1
    task._conn.close()


def test_persisted_artifact_has_no_full_text_prompt_or_messages():
    _, history = _capture_four()
    rendered = json.dumps(history.model_dump(mode="json"), ensure_ascii=False)
    assert '"messages"' not in rendered
    assert '"prompt"' not in rendered
    assert "完整正文" not in rendered
    for record in history.records.values():
        for fact in record.after_frame.facts:
            assert len(fact.evidence_excerpt) <= 140
