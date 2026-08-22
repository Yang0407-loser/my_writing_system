import hashlib
import json

from app.agents.writer import Writer
from app.config import settings
from app.writing.handover_contract_v2 import build_handover_sources
from app.writing.subsection_handover_history import observation_from_note
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


def _evidence(source, excerpt):
    start = source.text.index(excerpt)
    return {
        "source_type": source.source_type,
        "source_id": source.source_id,
        "source_hash": source.source_hash,
        "start": start,
        "end": start + len(excerpt),
        "excerpt": excerpt,
    }


def test_v2_uses_one_existing_extractor_call_and_persists_optional_metadata(
    monkeypatch, mock_llm
):
    text = "林晚回到家。"
    current = {
        "subsection": 1,
        "title": "回家",
        "description": "林晚回家。",
        "key_points": ["林晚回家"],
    }
    following = {
        "subsection": 2,
        "title": "整理",
        "description": "林晚整理照片。",
        "key_points": ["林晚整理照片"],
    }
    sources = build_handover_sources(
        section=1,
        subsection=1,
        generated_text=text,
        current_outline=current | {"_section": 1},
        next_outline=following | {"_section": 1},
    )
    generated = sources["generated-subsection:S1.1"]
    response = {
        "claims": [
            {
                "claim_id": "location",
                "category": "location_state",
                "subject": "林晚",
                "predicate": "回到",
                "object": "家",
                "temporal_status": "current",
                "certainty": "confirmed",
                "evidence": [_evidence(generated, text)],
                "claim_hash": "",
                "provenance": "handover_extractor_v2",
            }
        ],
        "open_events": [],
        "arc_progress": [],
    }
    client = mock_llm(json.dumps(response, ensure_ascii=False))
    monkeypatch.setattr(settings, "WRITER_HANDOVER_CONTRACT_VERSION", "v2")
    writer = Writer()
    note, observation = writer._extract_handover_with_observation(
        text,
        1,
        1,
        current_subsection=current,
        next_subsection=following,
    )
    assert client.chat_completion.call_count == 1
    assert note["new_facts"] == ["林晚回到家"]
    assert observation.contract_version == "v2"
    assert observation.typed_contract_hash
    assert observation.accepted_claim_count == 1
    assert observation.rejected_claim_count == 0

    board = FakeBlackboard()
    recorder = SubsectionHandoverHistoryRecorder(board, "task")
    output_hash = hashlib.sha256(text.encode()).hexdigest()
    recorder.capture_committed(
        section=1,
        subsection=1,
        output_sha256=output_hash,
        prompt_messages_hash="p" * 64,
        commit_idempotency_key="task:1:1",
        handover_note=note,
        observation=observation,
    )
    record = next(iter(normalize_history(
        board.data["subsection_handover_history_v1"]
    ).records.values()))
    assert record.contract_version == "v2"
    assert record.typed_contract_hash == observation.typed_contract_hash
    assert record.accepted_claim_count == 1
    assert record.source_manifest == observation.source_manifest
    assert record.production_effect is False


def test_v1_behavior_and_call_count_remain_compatible(monkeypatch, mock_llm):
    response = (
        '{"foreshadowing":"","character_state":"","open_threads":"",'
        '"found_contradictions":[],"new_facts":[],"arc_progress":{}}'
    )
    client = mock_llm(response)
    monkeypatch.setattr(settings, "WRITER_HANDOVER_CONTRACT_VERSION", "v1")
    writer = Writer()
    note, observation = writer._extract_handover_with_observation(
        "text",
        1,
        1,
        current_subsection={"title": "ignored in v1"},
        next_subsection={"title": "ignored in v1"},
    )
    assert note == json.loads(response)
    assert observation.contract_version is None
    assert client.chat_completion.call_count == 1
    _, kwargs = client.chat_completion.call_args
    assert kwargs == {
        "temperature": 0.2,
        "max_tokens": 600,
        "json_mode": True,
    }


def test_old_record_without_v2_fields_still_loads():
    note = {
        "foreshadowing": "",
        "character_state": "",
        "open_threads": "",
        "new_facts": [],
        "found_contradictions": [],
        "arc_progress": {},
    }
    observation = observation_from_note(note)
    board = FakeBlackboard()
    recorder = SubsectionHandoverHistoryRecorder(board, "legacy")
    recorder.capture_committed(
        section=1,
        subsection=1,
        output_sha256="o" * 64,
        prompt_messages_hash="p" * 64,
        commit_idempotency_key="legacy:1:1",
        handover_note=note,
        observation=observation,
    )
    payload = board.data["subsection_handover_history_v1"]
    record = next(iter(normalize_history(payload).records.values()))
    assert record.contract_version is None
    assert record.typed_contract_hash is None
    assert record.accepted_claim_count is None
