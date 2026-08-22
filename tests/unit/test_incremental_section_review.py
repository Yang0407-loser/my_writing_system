import json

from app.agents.writer import Writer
from app.config import settings


class Blackboard:
    def __init__(self):
        self.values = {}

    def get(self, _task_id, key):
        return self.values.get(key)

    def set(self, _task_id, key, value):
        self.values[key] = value


def invoke(writer, blackboard=None):
    return writer._maybe_start_incremental_section_review(
        task_id="task-private-123",
        section_num=2,
        sub_num=3,
        topic="PRIVATE_TOPIC",
        style={"private": "PRIVATE_STYLE"},
        full_draft="【一】已有正文【二】已有正文【三】已有正文",
        section_text="PRIVATE_SECTION_TEXT",
        sub_title="PRIVATE_TITLE",
        sub_text="PRIVATE_SUBSECTION_TEXT",
        blackboard=blackboard,
    )


def test_default_configuration_disables_incremental_review():
    assert settings.WRITER_INCREMENTAL_SECTION_REVIEW_RAW == "false"
    assert settings.WRITER_INCREMENTAL_SECTION_REVIEW is False


def test_invalid_configuration_warns_and_is_effectively_false(monkeypatch):
    monkeypatch.setattr(settings, "WRITER_INCREMENTAL_SECTION_REVIEW_RAW", "unexpected")
    monkeypatch.setattr(settings, "WRITER_INCREMENTAL_SECTION_REVIEW", False)
    assert any(
        "WRITER_INCREMENTAL_SECTION_REVIEW=unexpected" in warning
        and "按 false 处理" in warning
        for warning in settings.validate()
    )


def test_disabled_path_does_not_construct_reviewer_or_thread(monkeypatch):
    monkeypatch.setattr(settings, "WRITER_INCREMENTAL_SECTION_REVIEW", False)
    reviewer_calls = []
    thread_calls = []

    class ForbiddenReviewer:
        def __init__(self):
            reviewer_calls.append("constructed")

    class ForbiddenThread:
        def __init__(self, *args, **kwargs):
            thread_calls.append((args, kwargs))

    monkeypatch.setattr("app.agents.reviewer.Reviewer", ForbiddenReviewer)
    monkeypatch.setattr("app.agents.writer.threading.Thread", ForbiddenThread)

    record = invoke(Writer.__new__(Writer), Blackboard())
    assert reviewer_calls == []
    assert thread_calls == []
    assert record["incremental_review_started"] is False
    assert record["skip_reason"] == "disabled_by_config"
    assert record["production_effect"] is False


def test_enabled_path_preserves_legacy_reviewer_and_pending_status(monkeypatch):
    monkeypatch.setattr(settings, "WRITER_INCREMENTAL_SECTION_REVIEW", True)
    monkeypatch.setattr(settings, "WRITER_REVIEW_TRIGGER_SUBS", 3)
    monkeypatch.setattr(settings, "WRITER_REVIEW_TRIGGER_CHARS", 10**9)
    reviewer_instances = []
    threads = []

    class FakeReviewer:
        def __init__(self):
            reviewer_instances.append(self)

        def review_section(self, *_args, **_kwargs):
            return {"score": 8}

    class DeferredThread:
        def __init__(self, *, target, daemon):
            self.target = target
            self.daemon = daemon
            self.started = False
            threads.append(self)

        def start(self):
            self.started = True

    monkeypatch.setattr("app.agents.reviewer.Reviewer", FakeReviewer)
    monkeypatch.setattr("app.agents.writer.threading.Thread", DeferredThread)
    blackboard = Blackboard()

    record = invoke(Writer.__new__(Writer), blackboard)
    assert len(reviewer_instances) == 1
    assert len(threads) == 1 and threads[0].started is True and threads[0].daemon is True
    assert blackboard.values["section_reviews"] == [{
        "section": 2,
        "subsection": 3,
        "chars": len("PRIVATE_SECTION_TEXT") + len("PRIVATE_SUBSECTION_TEXT"),
        "status": "pending",
    }]
    assert record["incremental_review_started"] is True
    assert record["skip_reason"] is None

    threads[0].target()
    assert blackboard.values["section_reviews"][0]["status"] == "done"
    assert blackboard.values["section_reviews"][0]["score"] == 8


def test_enabled_but_untriggered_does_not_construct_reviewer(monkeypatch):
    monkeypatch.setattr(settings, "WRITER_INCREMENTAL_SECTION_REVIEW", True)
    monkeypatch.setattr(settings, "WRITER_REVIEW_TRIGGER_SUBS", 999)
    monkeypatch.setattr(settings, "WRITER_REVIEW_TRIGGER_CHARS", 10**9)

    class ForbiddenReviewer:
        def __init__(self):
            raise AssertionError("reviewer must not be constructed")

    monkeypatch.setattr("app.agents.reviewer.Reviewer", ForbiddenReviewer)
    record = invoke(Writer.__new__(Writer), Blackboard())
    assert record["incremental_review_started"] is False
    assert record["skip_reason"] == "trigger_not_reached"


def test_observation_is_redacted_and_has_only_bounded_fields(monkeypatch, caplog):
    monkeypatch.setattr(settings, "WRITER_INCREMENTAL_SECTION_REVIEW", False)
    with caplog.at_level("INFO", logger="writing_system.writer"):
        record = invoke(Writer.__new__(Writer), Blackboard())

    assert set(record) == {
        "task_id_hash",
        "section",
        "subsection",
        "incremental_review_enabled",
        "incremental_review_started",
        "skip_reason",
        "production_effect",
    }
    assert len(record["task_id_hash"]) == 64
    serialized = json.dumps(record)
    combined = serialized + caplog.text
    for private_value in (
        "task-private-123",
        "PRIVATE_TOPIC",
        "PRIVATE_STYLE",
        "PRIVATE_SECTION_TEXT",
        "PRIVATE_TITLE",
        "PRIVATE_SUBSECTION_TEXT",
    ):
        assert private_value not in combined
