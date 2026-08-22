import hashlib
import json

from app.config import settings
from app.writing.post_write_extraction import SharedPostWriteExtractor
from app.writing.shadow_post_write_extraction import (
    BlackboardPostWriteExtractionSink,
    InMemoryPostWriteExtractionSink,
    ShadowPostWriteExtractionRunner,
)


class FakeLLM:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def chat_completion(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        return json.dumps(self.payload, ensure_ascii=False)


class FakeBlackboard:
    def __init__(self):
        self.values = {}

    def get(self, task_id, key):
        return self.values.get((task_id, key))

    def set(self, task_id, key, value):
        self.values[(task_id, key)] = value


def _hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_post_write_extraction_mode_defaults_off():
    assert settings.WRITER_POST_WRITE_EXTRACTION_MODE_RAW == "off"
    assert settings.WRITER_POST_WRITE_EXTRACTION_MODE == "off"


def test_invalid_mode_falls_back_off_with_warning(monkeypatch):
    monkeypatch.setattr(settings, "WRITER_POST_WRITE_EXTRACTION_MODE_RAW", "replace")
    monkeypatch.setattr(settings, "WRITER_POST_WRITE_EXTRACTION_MODE", "off")
    assert any("WRITER_POST_WRITE_EXTRACTION_MODE=replace" in item for item in settings.validate())
    assert settings.WRITER_POST_WRITE_EXTRACTION_MODE == "off"


def test_extractor_accepts_exact_evidence_and_rejects_hallucinated_evidence():
    text = "周野把钥匙交给林晚。林晚答应明天归还。"
    llm = FakeLLM({"changes": [
        {
            "category": "relationship", "subject": "林晚与周野",
            "predicate": "trust_exchange", "value": "周野交出钥匙",
            "status": "confirmed", "confidence": 0.9,
            "evidence_text": "周野把钥匙交给林晚。",
        },
        {
            "category": "event", "subject": "林晚",
            "predicate": "received_money", "value": "收到一万元",
            "status": "confirmed", "confidence": 0.8,
            "evidence_text": "林晚收到一万元。",
        },
    ]})
    bundle = SharedPostWriteExtractor(llm).extract(
        task_id="task", section=1, subsection=1, text=text,
        output_hash=_hash(text), source_manifest=[{"source_id": "outline:1", "text_hash": "abc", "text": "private"}],
        known_context={"characters": [{"character_id": "c1", "name": "周野"}]},
    )

    assert len(llm.calls) == 1
    assert llm.calls[0][1] == {
        "temperature": 0.2, "max_tokens": 1800,
        "json_mode": True, "prompt_name": "post_write_state_extraction",
    }
    assert len(bundle.changes) == 1
    change = bundle.changes[0]
    assert change.category == "relationship"
    assert change.evidence[0].span_start == 0
    assert text[change.evidence[0].span_start:change.evidence[0].span_end] == "周野把钥匙交给林晚。"
    assert bundle.extraction_warnings == ["change_1:evidence_not_found"]
    assert bundle.source_manifest[0] == {"source_id": "outline:1", "text_hash": "abc"}
    assert bundle.source_manifest[1]["source_id"].startswith("post-write-known-context:0ebb429f")
    assert "task" not in bundle.source_manifest[1]["source_id"]
    assert "周野" in llm.calls[0][0][1]["content"]


def test_bundle_hash_and_change_ids_are_deterministic():
    text = "林晚仍不知道信是谁寄来的。"
    payload = {"changes": [{
        "category": "handover", "subject": "匿名信来源",
        "predicate": "sender_identity", "value": "未知",
        "status": "unknown", "confidence": 0.95,
        "evidence_text": text,
    }]}
    kwargs = dict(
        task_id="task", section=2, subsection=3, text=text,
        output_hash=_hash(text), source_manifest=[],
    )
    first = SharedPostWriteExtractor(FakeLLM(payload)).extract(**kwargs)
    second = SharedPostWriteExtractor(FakeLLM(payload)).extract(**kwargs)
    assert first.bundle_hash == second.bundle_hash
    assert first.changes[0].change_id == second.changes[0].change_id
    assert first.changes[0].status == "unknown"


def test_disabled_runner_never_calls_extractor_or_sink():
    class ForbiddenExtractor:
        def extract(self, **kwargs):
            raise AssertionError("extractor must not run")

    sink = InMemoryPostWriteExtractionSink()
    runner = ShadowPostWriteExtractionRunner(
        enabled=False, extractor=ForbiddenExtractor(), sink=sink,
    )
    assert runner.observe_committed(
        task_id="task", section=1, subsection=1,
        text="正文", output_hash=_hash("正文"), source_manifest=[],
    ) is None
    assert sink.records == []


def test_blackboard_sink_keeps_bundle_in_task_scoped_shadow_field():
    text = "周野离开了面包店。"
    llm = FakeLLM({"changes": [{
        "category": "character_state", "subject": "周野",
        "predicate": "location", "value": "已离开面包店",
        "status": "confirmed", "confidence": 1,
        "evidence_text": text,
    }]})
    blackboard = FakeBlackboard()
    runner = ShadowPostWriteExtractionRunner(
        enabled=True,
        extractor=SharedPostWriteExtractor(llm),
        sink=BlackboardPostWriteExtractionSink(blackboard, "task"),
    )
    record = runner.observe_committed(
        task_id="task", section=1, subsection=1,
        text=text, output_hash=_hash(text), source_manifest=[],
    )
    stored = blackboard.get("task", "post_write_extraction_shadow")
    assert record["status"] == "completed"
    assert record["production_effect"] is False
    assert stored[0]["bundle"]["changes"][0]["value"] == "已离开面包店"
    serialized_record = json.dumps(record, ensure_ascii=False)
    assert text not in serialized_record
    assert "你是一位严谨" not in serialized_record


def test_shadow_error_is_failure_isolated_and_deduplicated():
    class FailingExtractor:
        def extract(self, **kwargs):
            raise RuntimeError("private text must not leak")

    sink = InMemoryPostWriteExtractionSink()
    runner = ShadowPostWriteExtractionRunner(
        enabled=True, extractor=FailingExtractor(), sink=sink,
    )
    kwargs = dict(
        task_id="task", section=1, subsection=1,
        text="private text", output_hash=_hash("private text"), source_manifest=[],
    )
    record = runner.observe_committed(**kwargs)
    assert record["status"] == "shadow_error"
    assert record["error_type"] == "RuntimeError"
    assert "private text" not in json.dumps(record)
    assert runner.observe_committed(**kwargs) is None
    assert len(sink.records) == 1
