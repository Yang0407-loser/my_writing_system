import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from app.config import settings
from app.writing.contracts import PromptArtifact
from app.writing.prompt_builder import messages_hash
from app.writing.scene_spec_provider import (
    SCENE_SPEC_HEADER,
    OutlineSceneSpecProvider,
    SceneSpecCanaryController,
)


ROOT = Path(__file__).resolve().parents[2]
RUNTIME_SOURCE = ROOT / ".phase4r_final_trial_runtime" / "source.private.json"
GOLDEN_HASHES = (
    "8f59121b834a7587b3e42eb75cc91a976d580d159ef5cce3c828c075cb46a2d8",
    "3d144ed009b624331321c5c40daa519c4bfe4ca92c0b5626e03a07a64a0ba7d2",
    "5150786e3fcd504e42e4bc56282cb4cc8ce15a43d29afebc2098515363249f9b",
    "6e4b3567c391777b8f2736ce5fd5518fbfd95cc6e7f39879c06fc3927e460459",
)


def prompt_artifact() -> PromptArtifact:
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "legacy"},
    ]
    return PromptArtifact(
        messages=messages,
        messages_hash=messages_hash(messages),
        content_hash=hashlib.sha256("system\nlegacy".encode()).hexdigest(),
        estimated_tokens=3,
        token_by_source={"legacy": 3},
        source_manifest=[{"source_id": "legacy", "text_hash": "a" * 64}],
        prompt_version="prompt-v1",
    )


def outlines():
    return (
        {"subsection": 1, "title": "当前动作", "description": "当前动作描述", "key_points": ["靠近", "回应"]},
        {"subsection": 2, "title": "后续动作", "description": "后续动作描述", "key_points": ["离开"]},
    )


class SpyProvider(OutlineSceneSpecProvider):
    def __init__(self, error=None):
        self.calls = 0
        self.error = error
        self.last_result = None

    def build(self, **kwargs):
        self.calls += 1
        if self.error:
            raise self.error
        self.last_result = super().build(**kwargs)
        return self.last_result


class UntraceableProvider(OutlineSceneSpecProvider):
    def build(self, **kwargs):
        return replace(super().build(**kwargs), source_manifest=())


def apply(controller, *, current=None, next_sub=None, is_last=False):
    current_default, next_default = outlines()
    return controller.apply(
        prompt_artifact(),
        task_id="task-canary",
        section=2,
        current_subsection=current if current is not None else current_default,
        next_subsection=next_sub if next_sub is not None else next_default,
        is_last_subsection=is_last,
    )


def test_default_configuration_is_off():
    assert settings.WRITER_SCENE_SPEC_MODE == "off"
    assert settings.WRITER_SCENE_SPEC_CANARY_TASK_IDS == ""


def test_off_does_not_call_provider_or_write_record():
    provider = SpyProvider()
    controller = SceneSpecCanaryController(mode="off", provider=provider)
    original = prompt_artifact()
    result = controller.apply(
        original, task_id="task-canary", section=2,
        current_subsection=outlines()[0], next_subsection=outlines()[1],
        is_last_subsection=False,
    )
    assert provider.calls == 0
    assert result.prompt is original
    assert result.record is None
    assert result.spec is None
    assert result.source_manifest == ()


def test_invalid_mode_is_effectively_off():
    provider = SpyProvider()
    controller = SceneSpecCanaryController(mode="unexpected", provider=provider)
    result = apply(controller)
    assert controller.mode == "off"
    assert provider.calls == 0
    assert result.record is None


def test_invalid_config_value_produces_warning(monkeypatch):
    monkeypatch.setattr(settings, "WRITER_SCENE_SPEC_MODE", "unexpected")
    assert any("WRITER_SCENE_SPEC_MODE=unexpected" in item for item in settings.validate())


def test_shadow_compiles_without_changing_legacy_messages():
    provider = SpyProvider()
    controller = SceneSpecCanaryController(mode="shadow", provider=provider)
    original = prompt_artifact()
    result = controller.apply(
        original, task_id="task-canary", section=2,
        current_subsection=outlines()[0], next_subsection=outlines()[1],
        is_last_subsection=False,
    )
    assert provider.calls == 1
    assert result.prompt is original
    assert result.prompt.messages_hash == original.messages_hash
    assert result.record["injected"] is False
    assert result.record["production_effect"] is False
    assert result.spec is not None
    assert result.spec is provider.last_result.spec
    assert result.spec.spec_hash == result.record["scene_spec_hash"]
    assert result.source_manifest == tuple(
        {"source_id": item.source_id, "text_hash": item.text_hash}
        for item in result.spec.evidence
    )


def test_canary_injects_once_only_for_allowlisted_task():
    original = prompt_artifact()
    controller = SceneSpecCanaryController(
        mode="canary", canary_task_ids="other, task-canary"
    )
    result = controller.apply(
        original, task_id="task-canary", section=2,
        current_subsection=outlines()[0], next_subsection=outlines()[1],
        is_last_subsection=False,
    )
    assert original.messages[-1]["content"] == "legacy"
    assert result.prompt.messages[0] == original.messages[0]
    assert result.prompt.messages[-1]["content"].count(SCENE_SPEC_HEADER) == 1
    assert result.prompt.messages_hash != original.messages_hash
    assert result.prompt.prompt_version == original.prompt_version
    assert result.record["injected"] is True
    assert result.record["estimated_tokens"] <= 400
    assert result.spec is not None
    assert result.spec.spec_hash == result.record["scene_spec_hash"]


def test_non_allowlisted_canary_does_not_call_provider():
    provider = SpyProvider()
    controller = SceneSpecCanaryController(
        mode="canary", canary_task_ids="different-task", provider=provider
    )
    original = prompt_artifact()
    result = controller.apply(
        original, task_id="task-canary", section=2,
        current_subsection=outlines()[0], next_subsection=outlines()[1],
        is_last_subsection=False,
    )
    assert provider.calls == 0
    assert result.prompt is original
    assert result.record["fallback_reason"] == "task_not_allowlisted"


def test_missing_outline_provider_error_and_budget_all_fall_back(caplog):
    original = prompt_artifact()
    missing = SceneSpecCanaryController(mode="canary", canary_task_ids="task-canary")
    missing_result = missing.apply(
        original, task_id="task-canary", section=2,
        current_subsection={}, next_subsection=None, is_last_subsection=False,
    )
    assert missing_result.prompt is original
    assert missing_result.record["fallback_reason"] == "current_outline_missing"
    assert missing_result.spec is None
    assert missing_result.source_manifest == ()

    secret = "private prose must not reach logs"
    failing_provider = SpyProvider(ValueError(secret))
    failing = SceneSpecCanaryController(
        mode="canary", canary_task_ids="task-canary", provider=failing_provider
    )
    failed_result = apply(failing)
    assert failed_result.prompt.messages_hash == prompt_artifact().messages_hash
    assert failed_result.record["fallback_reason"] == "ValueError"
    assert failed_result.spec is None
    assert secret not in caplog.text

    over_budget = SceneSpecCanaryController(
        mode="canary", canary_task_ids="task-canary", token_cap=1
    )
    budget_result = apply(over_budget)
    assert budget_result.prompt.messages_hash == prompt_artifact().messages_hash
    assert budget_result.record["fallback_reason"] == "scene_spec_over_token_cap"
    assert budget_result.spec is None


def test_last_subsection_without_next_outline_is_valid():
    current = {"subsection": 4, "title": "收束", "description": "完成本节", "key_points": []}
    controller = SceneSpecCanaryController(mode="canary", canary_task_ids="task-canary")
    result = controller.apply(
        prompt_artifact(), task_id="task-canary", section=2,
        current_subsection=current, next_subsection=None, is_last_subsection=True,
    )
    assert result.record["injected"] is True
    assert result.record["fallback_reason"] is None


def test_untraceable_source_manifest_falls_back_to_legacy():
    controller = SceneSpecCanaryController(
        mode="canary", canary_task_ids="task-canary", provider=UntraceableProvider()
    )
    original = prompt_artifact()
    result = controller.apply(
        original, task_id="task-canary", section=2,
        current_subsection=outlines()[0], next_subsection=outlines()[1],
        is_last_subsection=False,
    )
    assert result.prompt is original
    assert result.record["fallback_reason"] == "source_manifest_untraceable"
    assert result.spec is None
    assert result.source_manifest == ()


@pytest.mark.skipif(not RUNTIME_SOURCE.exists(), reason="private frozen field-trial source unavailable")
def test_four_real_field_trial_scene_specs_keep_frozen_hashes():
    private = json.loads(RUNTIME_SOURCE.read_text(encoding="utf-8"))
    section = private["outline"][0]
    subsections = section["subsections"]
    provider = OutlineSceneSpecProvider()
    results = [
        provider.build(
            task_id=private["task_id"],
            section=int(section.get("section", 1)),
            current_subsection=item,
            next_subsection=subsections[index + 1] if index + 1 < len(subsections) else None,
            is_last_subsection=index + 1 == len(subsections),
        )
        for index, item in enumerate(subsections)
    ]
    assert tuple(item.spec.spec_hash for item in results) == GOLDEN_HASHES
    assert tuple(item.spec.estimated_tokens for item in results) == (230, 351, 277, 93)
    assert all(item.spec.estimated_tokens <= 400 for item in results)
