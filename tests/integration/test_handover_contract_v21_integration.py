import hashlib
import json
from unittest.mock import patch

from app.agents.writer import Writer
from app.config import settings
from app.writing.subsection_handover_persistence import (
    SubsectionHandoverHistoryRecorder,
    normalize_history,
)


class FakeBlackboard:
    def __init__(self):
        self.data = {}

    def get(self, task_id, key):
        return self.data.get(key)

    def set(self, task_id, key, value):
        self.data[key] = value

    def load_checkpoint(self, task_id):
        return {}


def _payload(text):
    excerpt = "林晚回到家"
    start = text.index(excerpt)
    return {
        "v": "2.1",
        "s": [[0, start, start + len(excerpt), "ls", "c", "c", "林晚|回到|家"]],
        "o": [],
        "f": [],
        "a": [],
    }


def test_v21_uses_one_call_restores_contract_and_persists_optional_metadata(
    monkeypatch, mock_llm
):
    text = "林晚回到家。"
    payload = _payload(text)
    client = mock_llm()

    def complete(*args, completion_metadata_sink=None, **kwargs):
        completion_metadata_sink(
            {
                "finish_reason": "stop",
                "input_tokens": 100,
                "output_tokens": 45,
                "latency_seconds": 0.1,
            }
        )
        return json.dumps(payload, ensure_ascii=False)

    client.chat_completion.side_effect = complete
    monkeypatch.setattr(settings, "WRITER_HANDOVER_CONTRACT_VERSION", "v2.1")
    writer = Writer()
    note, observation = writer._extract_handover_with_observation(
        text,
        1,
        1,
        current_subsection={"subsection": 1, "title": "回家"},
        next_subsection={"subsection": 2, "title": "整理"},
        task_id="task-v21",
    )

    assert client.chat_completion.call_count == 1
    assert note["new_facts"] == ["林晚回到家"]
    assert observation.contract_version == "v2.1"
    assert observation.payload_version == "2.1"
    assert observation.finish_reason == "stop"
    assert observation.raw_output_tokens == 45
    assert observation.typed_contract_hash
    assert observation.source_registry_hash
    assert observation.compact_payload_hash
    assert observation.restored_claim_count == 1
    assert observation.locally_rejected_claim_count == 0

    board = FakeBlackboard()
    recorder = SubsectionHandoverHistoryRecorder(board, "task-v21")
    recorder.capture_committed(
        section=1,
        subsection=1,
        output_sha256=hashlib.sha256(text.encode()).hexdigest(),
        prompt_messages_hash="p" * 64,
        commit_idempotency_key="task-v21:1:1",
        handover_note=note,
        observation=observation,
    )
    record = next(
        iter(normalize_history(board.data["subsection_handover_history_v1"]).records.values())
    )
    assert record.payload_version == "2.1"
    assert record.source_registry_hash == observation.source_registry_hash
    assert record.compact_payload_hash == observation.compact_payload_hash
    assert record.raw_output_tokens == 45
    assert record.finish_reason == "stop"
    assert record.restored_claim_count == 1


def test_finish_length_fails_open_without_parsing_or_retry(monkeypatch, mock_llm):
    client = mock_llm()

    def truncated(*args, completion_metadata_sink=None, **kwargs):
        completion_metadata_sink(
            {
                "finish_reason": "length",
                "input_tokens": 100,
                "output_tokens": 600,
                "latency_seconds": 0.1,
            }
        )
        return '{"v":"2.1","s":['

    client.chat_completion.side_effect = truncated
    monkeypatch.setattr(settings, "WRITER_HANDOVER_CONTRACT_VERSION", "v2.1")
    writer = Writer()
    with patch("app.agents.writer.parse_json") as parser:
        note, observation = writer._extract_handover_with_observation(
            "林晚回到家。",
            1,
            1,
            current_subsection={"subsection": 1, "title": "回家"},
            next_subsection=None,
            task_id="task-v21",
        )

    assert client.chat_completion.call_count == 1
    parser.assert_not_called()
    assert note is None
    assert observation.execution_status == "error"
    assert observation.finish_reason == "length"
    assert observation.raw_output_tokens == 600
    assert observation.truncation_status == "output_truncated"
    assert observation.typed_contract_hash is None
    assert observation.compact_payload_hash is None


def test_v1_call_signature_and_result_remain_unchanged(monkeypatch, mock_llm):
    response = (
        '{"foreshadowing":"","character_state":"","open_threads":"",'
        '"found_contradictions":[],"new_facts":[],"arc_progress":{}}'
    )
    client = mock_llm(response)
    monkeypatch.setattr(settings, "WRITER_HANDOVER_CONTRACT_VERSION", "v1")
    note, observation = Writer()._extract_handover_with_observation("text", 1, 1)

    assert note == json.loads(response)
    assert observation.contract_version is None
    assert client.chat_completion.call_count == 1
    _, kwargs = client.chat_completion.call_args
    assert kwargs == {"temperature": 0.2, "max_tokens": 600, "json_mode": True}
