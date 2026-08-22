from app.agents.writer import Writer
from app.config import settings


class NoCallLLM:
    def __init__(self):
        self.calls = []

    def chat_completion(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        raise AssertionError("warn mode must not call the condense LLM")


def test_writer_facade_retains_overflow_in_warn_mode(monkeypatch):
    monkeypatch.setattr(settings, "WRITER_CONDENSE_MODE", "warn")
    monkeypatch.setattr(settings, "WRITER_EXPAND_THRESHOLD", 0.0)
    writer = object.__new__(Writer)
    writer.llm = NoCallLLM()
    draft = "\u957f\u6587\u3002" * 20

    artifact = writer._adjust_generated_length(
        draft,
        target_words=10,
        call_max_tokens=800,
        stream_callback=None,
        section_num=1,
        sub_num=2,
        task_id="private-task",
    )

    assert artifact.draft == draft
    assert writer.llm.calls == []
    assert artifact.generation_attempts == []
