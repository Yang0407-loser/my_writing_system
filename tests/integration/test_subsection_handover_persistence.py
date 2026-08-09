import hashlib
import json
import sqlite3
from pathlib import Path

from app.blackboard import Blackboard
from app.task_store import TaskStore
from app.writing.subsection_handover_history import (
    SUBSECTION_HANDOVER_HISTORY_KEY,
    observation_from_note,
)
from app.writing.subsection_handover_persistence import (
    SubsectionHandoverHistoryRecorder,
    legacy_checkpoint_projection,
    load_task_history_read_only,
    merge_history_into_analysis,
    normalize_history,
)


class FakeBlackboard:
    def __init__(self):
        self.data = {}
        self.checkpoint = {"checkpoint_version": "test-v1"}

    def get(self, task_id, key):
        return self.data.get(key)

    def set(self, task_id, key, value):
        self.data[key] = value

    def load_checkpoint(self, task_id):
        return dict(self.checkpoint)


def _capture_four():
    board = FakeBlackboard()
    recorder = SubsectionHandoverHistoryRecorder(board, "task")
    before_commit_counts = []
    for subsection in range(1, 5):
        before_commit_counts.append(len(normalize_history(
            board.data.get(SUBSECTION_HANDOVER_HISTORY_KEY)
        ).records))
        note = {
            "foreshadowing": "" if subsection > 1 else "private",
            "character_state": "",
            "open_threads": [],
            "new_facts": [],
            "found_contradictions": [],
            "arc_progress": {},
        }
        output_hash = hashlib.sha256(f"output-{subsection}".encode()).hexdigest()
        recorder.capture_committed(
            section=1,
            subsection=subsection,
            output_sha256=output_hash,
            prompt_messages_hash=hashlib.sha256(
                f"prompt-{subsection}".encode()
            ).hexdigest(),
            commit_idempotency_key=f"task:1:{subsection}",
            handover_note=note,
            observation=observation_from_note(note),
        )
    return board, before_commit_counts, normalize_history(
        board.data[SUBSECTION_HANDOVER_HISTORY_KEY]
    )


def test_synthetic_four_subsections_only_persist_after_commit_boundary():
    _, before_counts, history = _capture_four()
    assert before_counts == [0, 1, 2, 3]
    assert len(history.records) == 4
    assert len(history.pending) == 0
    assert len({item.record_id for item in history.records.values()}) == 4
    assert {
        item.execution_status for item in history.records.values()
    } == {"completed_with_changes", "completed_no_change"}
    assert all(item.production_effect is False for item in history.records.values())


def test_checkpoint_mirror_preserves_legacy_projection():
    import fakeredis

    _, _, history = _capture_four()
    board = Blackboard()
    board._redis = fakeredis.FakeRedis()
    board.set(
        "task",
        SUBSECTION_HANDOVER_HISTORY_KEY,
        history.model_dump(mode="json"),
    )
    legacy = {
        "phase": "writing",
        "handover_chain": [{"from_section": 1}],
        "character_arcs": [],
        "section_texts": {"1": "private"},
    }
    board.save_checkpoint("task", legacy)
    restored = board.load_checkpoint("task")
    assert len(
        restored[SUBSECTION_HANDOVER_HISTORY_KEY]["records"]
    ) == 4
    assert legacy_checkpoint_projection(restored) == legacy


def test_task_store_analysis_merge_read_only_restart_and_additive_schema(
    tmp_path,
):
    _, _, history = _capture_four()
    merged = merge_history_into_analysis(
        {
            "existing_metric": {"value": 1},
            "state_frame_history_v1": {"records": {"kept": {}}},
        },
        history.model_dump(mode="json"),
    )
    db_path = tmp_path / "tasks.db"
    store = TaskStore(str(db_path))
    legacy_handover = [{"from_section": 1, "to_section": 2}]
    store.save("task", {
        "analysis": merged,
        "handover_notes": legacy_handover,
    })
    store.close()

    connection = sqlite3.connect(db_path)
    schema_before_read = {
        row[0]: tuple(
            connection.execute(f"PRAGMA table_info({row[0]})").fetchall()
        )
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    connection.close()

    recovered = load_task_history_read_only(str(db_path), "task")
    assert recovered is not None
    assert len(recovered.records) == 4
    reopened = TaskStore(str(db_path))
    task = reopened.get("task")
    assert task["analysis_json"]["existing_metric"] == {"value": 1}
    assert "state_frame_history_v1" in task["analysis_json"]
    assert task["handover_json"] == legacy_handover
    reopened.close()

    connection = sqlite3.connect(db_path)
    columns = {
        row[1]
        for row in connection.execute("PRAGMA table_info(task_history)").fetchall()
    }
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    schema_after_read = {
        row[0]: tuple(
            connection.execute(f"PRAGMA table_info({row[0]})").fetchall()
        )
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    connection.close()
    assert SUBSECTION_HANDOVER_HISTORY_KEY not in columns
    assert tables == {"task_history", "task_store_schema_migrations"}
    assert schema_after_read == schema_before_read


def test_writer_hook_order_and_legacy_consumers_remain_separate():
    source = Path("app/agents/writer.py").read_text(encoding="utf-8")
    commit = source.index("state_committer.commit_subsection(")
    observers = source.index("shadow_boundary_validator.observe_committed(")
    state_after = source.index("state_frame_history.capture_after(")
    handover_capture = source.index(
        "subsection_handover_history.capture_committed("
    )
    section_aggregate = source.index("state_committer.commit_section_handover(")
    assert commit < observers < state_after < handover_capture < section_aggregate
    assert 'idempotency_key=f"{task_id}:{section_num}:{sub_num}"' in source
    assert "handover_notes=list(handover_notes)" in source


def test_old_extract_return_and_call_contract_are_compatible(mock_llm):
    response = (
        '{"foreshadowing":"","character_state":"","open_threads":"",'
        '"found_contradictions":[],"new_facts":[],"arc_progress":{}}'
    )
    client = mock_llm(response)
    from app.agents.writer import Writer

    writer = Writer()
    legacy = writer._extract_handover("text", 1, 1)
    assert legacy == json.loads(response)
    assert client.chat_completion.call_count == 1
    _, kwargs = client.chat_completion.call_args
    assert kwargs == {
        "temperature": 0.2,
        "max_tokens": 600,
        "json_mode": True,
    }
    messages = client.chat_completion.call_args.args[0]
    prompt_hash = hashlib.sha256(
        json.dumps(
            messages,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    assert prompt_hash == (
        "da016b90db958eaaeeadbad820fe64072"
        "b8b45aa824f86087240a09ca1046b97"
    )


def test_parse_failure_is_error_but_legacy_fallback_remains_none(mock_llm):
    client = mock_llm("not-json")
    from app.agents.writer import Writer

    writer = Writer()
    note, observation = writer._extract_handover_with_observation("text", 1, 1)
    assert note is None
    assert observation.execution_status == "error"
    assert observation.error_type
    assert client.chat_completion.call_count == 1
