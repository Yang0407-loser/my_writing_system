import hashlib

from app.config import settings
from app.writing.generation_controller import GenerationController


class FakeLLM:
    def __init__(self, responses=()):
        self.responses = list(responses)
        self.calls = []

    def chat_completion(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        return self.responses.pop(0)


def make_controller(llm):
    return GenerationController(
        llm,
        character_violation_checker=lambda _text, _characters: [],
        fallback_splitter=lambda text: [text],
    )


def adjust(controller, draft, *, target_words=10, task_id="private-task"):
    return controller.adjust_length(
        draft,
        target_words=target_words,
        call_max_tokens=800,
        stream_callback=None,
        section_num=2,
        sub_num=1,
        task_id=task_id,
    )


def test_condense_mode_defaults_to_legacy():
    assert settings.WRITER_CONDENSE_MODE_RAW == "legacy"
    assert settings.WRITER_CONDENSE_MODE == "legacy"


def test_invalid_condense_mode_warns_and_effective_value_is_legacy(monkeypatch):
    monkeypatch.setattr(settings, "WRITER_CONDENSE_MODE_RAW", "unexpected")
    monkeypatch.setattr(settings, "WRITER_CONDENSE_MODE", "legacy")

    assert any("WRITER_CONDENSE_MODE=unexpected" in item for item in settings.validate())
    assert settings.WRITER_CONDENSE_MODE == "legacy"


def test_legacy_mode_preserves_condense_call(monkeypatch):
    monkeypatch.setattr(settings, "WRITER_EXPAND_THRESHOLD", 0.0)
    monkeypatch.setattr(settings, "WRITER_CONDENSE_MODE", "legacy")
    llm = FakeLLM(["condensed"])

    artifact = adjust(make_controller(llm), "\u957f\u6587\u3002" * 20)

    assert artifact.draft == "condensed"
    assert len(llm.calls) == 1
    assert llm.calls[0][1] == {"temperature": 0.3, "max_tokens": 800}
    assert artifact.generation_attempts == [
        {"reason": "condense", "temperature": 0.3, "output_chars": 9}
    ]


def test_warn_mode_retains_overflow_without_llm(monkeypatch):
    monkeypatch.setattr(settings, "WRITER_EXPAND_THRESHOLD", 0.0)
    monkeypatch.setattr(settings, "WRITER_CONDENSE_MODE", "warn")
    draft = "\u957f\u6587\u3002" * 20
    llm = FakeLLM()

    subject = make_controller(llm)
    artifact = adjust(subject, draft)

    assert artifact.draft == draft
    assert llm.calls == []
    record = subject.last_condense_observation
    assert record["mode"] == "warn"
    assert record["original_characters"] == 40
    assert record["overflow_ratio"] == 4.0
    assert record["threshold"] == 1.3
    assert record["would_have_condensed"] is True
    assert record["condense_started"] is False
    assert record["retained_original"] is True
    assert record["output_sha256"] == hashlib.sha256(draft.encode("utf-8")).hexdigest()
    assert record["production_effect"] is True
    assert set(record).isdisjoint({"text", "prompt", "messages", "api_key"})


def test_below_threshold_output_is_mode_independent(monkeypatch):
    monkeypatch.setattr(settings, "WRITER_EXPAND_THRESHOLD", 0.0)
    draft = "\u77ed\u6587\u3002"
    outputs = []

    for mode in ("legacy", "warn"):
        monkeypatch.setattr(settings, "WRITER_CONDENSE_MODE", mode)
        llm = FakeLLM()
        outputs.append(adjust(make_controller(llm), draft).draft)
        assert llm.calls == []

    assert outputs == [draft, draft]


def test_warn_mode_keeps_existing_expand_behavior(monkeypatch):
    monkeypatch.setattr(settings, "WRITER_CONDENSE_MODE", "warn")
    monkeypatch.setattr(settings, "WRITER_EXPAND_THRESHOLD", 0.7)
    monkeypatch.setattr(settings, "WRITER_MAX_EXPAND_ATTEMPTS", 1)
    llm = FakeLLM(["\u7ee7\u7eed\u5185\u5bb9\u3002"])

    artifact = adjust(make_controller(llm), "\u77ed\u3002", target_words=10)

    assert len(llm.calls) == 1
    assert artifact.generation_attempts[0]["reason"] == "expand"
    assert artifact.draft.endswith("\u7ee7\u7eed\u5185\u5bb9\u3002")
