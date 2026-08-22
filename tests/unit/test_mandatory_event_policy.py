import hashlib
import json
from unittest.mock import MagicMock

from app.config import settings
from app.writing.generation_controller import GenerationController
from app.writing.mandatory_event_policy import MandatoryEventPolicy


TASK_ID = "11111111-1111-4111-8111-111111111111"
OTHER_TASK_ID = "22222222-2222-4222-8222-222222222222"
MANDATORY = "1. 【必须】林晚删帖"


class FakeLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def chat_completion(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        return self.responses.pop(0)


def controller(llm, policy, character_checker=lambda *_: []):
    return GenerationController(
        llm,
        character_violation_checker=character_checker,
        fallback_splitter=lambda text: [text],
        mandatory_event_policy=policy,
    )


def generate(instance, *, task_id=TASK_ID, characters=None):
    return instance.generate(
        messages=[{"role": "user", "content": "写作"}],
        call_max_tokens=900,
        stream_callback=None,
        section_num=2,
        sub_num=1,
        mandatory_events_text=MANDATORY,
        task_id=task_id,
        characters=characters,
    )


def test_default_mode_is_warn_and_invalid_mode_falls_back_to_warn(monkeypatch):
    assert settings.WRITER_MANDATORY_EVENT_MODE == "warn"
    policy = MandatoryEventPolicy("invalid-mode")
    assert policy.mode_requested == "invalid-mode"
    assert policy.effective_mode(TASK_ID) == "warn"

    monkeypatch.setattr(settings, "WRITER_MANDATORY_EVENT_MODE", "invalid-mode")
    assert any("按 warn 处理" in warning for warning in settings.validate())


def test_retry_requires_an_exact_canonical_uuid_allowlist_match():
    assert MandatoryEventPolicy("retry", "").effective_mode(TASK_ID) == "warn"
    assert MandatoryEventPolicy("retry", TASK_ID[:8]).effective_mode(TASK_ID) == "warn"
    assert MandatoryEventPolicy("retry", "*").effective_mode(TASK_ID) == "warn"
    assert MandatoryEventPolicy("retry", f"{TASK_ID}*").effective_mode(TASK_ID) == "warn"
    policy = MandatoryEventPolicy("retry", f"{OTHER_TASK_ID}, {TASK_ID}")
    assert policy.effective_mode(TASK_ID) == "retry"
    assert policy.effective_mode(OTHER_TASK_ID) == "retry"
    assert policy.effective_mode("") == "warn"


def test_warn_records_missing_event_without_an_extra_writer_call():
    llm = FakeLLM(["没有目标事件。"])
    instance = controller(llm, MandatoryEventPolicy("warn"))
    artifact = generate(instance)

    assert artifact.draft == "没有目标事件。"
    assert len(llm.calls) == 1
    assert artifact.generation_attempts == [
        {"reason": "initial", "temperature": 0.5, "output_chars": 7}
    ]
    observation = instance.last_mandatory_observation
    assert observation["would_have_retried"] is True
    assert observation["actual_retry_count"] == 0
    assert observation["production_effect"] is False
    assert observation["candidate_output_sha256"] == hashlib.sha256(
        artifact.draft.encode("utf-8")
    ).hexdigest()


def test_warn_does_not_append_force_rewrite_messages_or_change_parameters():
    original_messages = [{"role": "user", "content": "写作"}]
    llm = FakeLLM(["没有目标事件。"])
    instance = controller(llm, MandatoryEventPolicy("warn"))
    instance.generate(
        messages=original_messages,
        call_max_tokens=900,
        stream_callback=None,
        section_num=2,
        sub_num=1,
        mandatory_events_text=MANDATORY,
        task_id=TASK_ID,
    )
    assert llm.calls == [(
        original_messages,
        {"temperature": 0.5, "max_tokens": 900, "top_p": 0.9},
    )]
    assert all("强制重写" not in message["content"] for message in llm.calls[0][0])


def test_off_skips_detector_and_observation():
    policy = MagicMock()
    policy.effective_mode.return_value = "off"
    llm = FakeLLM(["正文。"])
    instance = controller(llm, policy)
    artifact = generate(instance)
    assert artifact.draft == "正文。"
    assert len(llm.calls) == 1
    policy.detect.assert_not_called()
    assert instance.last_mandatory_observation is None


def test_retry_non_allowlisted_task_degrades_to_warn():
    llm = FakeLLM(["没有目标事件。"])
    policy = MandatoryEventPolicy("retry", OTHER_TASK_ID)
    instance = controller(llm, policy)
    artifact = generate(instance, task_id=TASK_ID)
    assert artifact.draft == "没有目标事件。"
    assert len(llm.calls) == 1
    assert instance.last_mandatory_observation["mode_effective"] == "warn"
    assert instance.last_mandatory_observation["actual_retry_count"] == 0


def test_retry_allowlisted_task_preserves_legacy_two_retry_limit():
    llm = FakeLLM(["缺失一。", "缺失二。", "缺失三。"])
    policy = MandatoryEventPolicy("retry", TASK_ID)
    instance = controller(llm, policy)
    artifact = generate(instance)
    assert artifact.draft == "缺失三。"
    assert len(llm.calls) == 3
    assert [attempt["reason"] for attempt in artifact.generation_attempts] == [
        "initial", "mandatory_events", "mandatory_events",
    ]
    assert instance.last_mandatory_observation["legacy_retry_behavior"] is True
    assert instance.last_mandatory_observation["actual_retry_count"] == 2
    assert instance.last_mandatory_observation["production_effect"] is True


def test_warn_observes_the_final_candidate_after_other_rewrites():
    llm = FakeLLM(["初稿。", "角色修正版。"])
    instance = controller(
        llm,
        MandatoryEventPolicy("warn"),
        character_checker=lambda *_: ["角色违规"],
    )
    artifact = generate(instance, characters=[{"name": "林晚"}])
    assert artifact.draft == "角色修正版。"
    assert [attempt["reason"] for attempt in artifact.generation_attempts] == [
        "initial", "character_violation",
    ]
    assert instance.last_mandatory_observation["candidate_output_sha256"] == hashlib.sha256(
        "角色修正版。".encode("utf-8")
    ).hexdigest()
    assert instance.last_mandatory_observation["actual_retry_count"] == 0


def test_detector_exception_is_fail_open_and_redacted(monkeypatch):
    llm = FakeLLM(["私有正文。"])
    policy = MandatoryEventPolicy("warn")
    monkeypatch.setattr(policy, "detect", MagicMock(side_effect=RuntimeError("私有异常正文")))
    instance = controller(llm, policy)
    artifact = generate(instance)
    assert artifact.draft == "私有正文。"
    assert len(llm.calls) == 1
    observation = instance.last_mandatory_observation
    assert observation["error_type"] == "RuntimeError"
    serialized = json.dumps(observation, ensure_ascii=False)
    assert "私有正文" not in serialized
    assert "私有异常正文" not in serialized


def test_observation_contains_hashes_but_no_private_payloads():
    candidate = "没有目标事件。"
    event = "林晚删帖"
    policy = MandatoryEventPolicy("warn")
    detection = policy.detect(
        candidate=candidate,
        mandatory_events_text=MANDATORY,
        task_id=TASK_ID,
        section=2,
        subsection=1,
        actual_retry_count=0,
    )
    observation = detection.observation
    serialized = json.dumps(observation, ensure_ascii=False)
    assert candidate not in serialized
    assert event not in serialized
    assert "写作" not in serialized
    assert observation["violated_event_hashes"] == [
        hashlib.sha256(event.encode("utf-8")).hexdigest()
    ]
    assert observation["selected_keyword_hashes"][0]["keyword_hashes"]
    assert observation["threshold"] == 0.5
