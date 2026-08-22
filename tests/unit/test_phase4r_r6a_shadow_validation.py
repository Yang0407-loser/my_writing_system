import hashlib
import inspect

from app.config import settings
from app.writing.boundary_validator import BoundaryValidator, ValidationContract
from app.writing.contracts import SceneSpec, SourceEvidence, StateAssertion
from app.writing.shadow_validation import (
    InMemoryShadowValidationSink,
    ShadowBoundaryValidationRunner,
)


def scene_spec(*, profile="relative_unknown"):
    evidence = SourceEvidence(
        evidence_id="e1", source_id="state:1", source_type="story_state",
        text_hash="b" * 64, excerpt="短证据",
    )
    if profile == "relative_unknown":
        assertion = StateAssertion(
            assertion_id="a1", subject="周野", predicate="unverified_character_fact",
            value="亲属经历未知", status="unknown", evidence_ids=["e1"],
        )
        kwargs = {"unknowns_and_conflicts": [assertion]}
    else:
        assertion = StateAssertion(
            assertion_id="a1", subject="场景", predicate="description",
            value="普通场景", status="confirmed", evidence_ids=["e1"],
        )
        kwargs = {"confirmed_state": [assertion]}
    return SceneSpec(
        scene_id="scene-1", task_id="task-1", section=2, subsection=1,
        evidence=[evidence], source_hash="c" * 64, spec_hash="d" * 64,
        estimated_tokens=10, **kwargs,
    )


def observe(runner, text="周野父亲留下了一本旧书。", *, explicit_scene_spec=None):
    output_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return runner.observe_committed(
        task_id="task-1", section=2, subsection=1, text=text,
        output_hash=output_hash,
        source_manifest=[{"source_id": "writer-field:goal", "text_hash": "e" * 64, "private": text}],
        scene_spec=explicit_scene_spec,
    )


def test_shadow_flag_defaults_false_and_disabled_runner_is_true_noop():
    calls = []
    sink = InMemoryShadowValidationSink()
    runner = ShadowBoundaryValidationRunner(
        enabled=False, sink=sink,
        scene_spec_provider=lambda *args: calls.append(args),
    )
    assert settings.WRITER_BOUNDARY_VALIDATOR_SHADOW is False
    assert observe(runner) is None
    assert calls == []
    assert sink.records == []


def test_enabled_runner_writes_sanitized_trace_without_changing_output_hash():
    sink = InMemoryShadowValidationSink()
    runner = ShadowBoundaryValidationRunner(
        enabled=True, sink=sink, scene_spec_provider=lambda *_: scene_spec(),
    )
    text = "周野父亲留下了一本旧书。"
    record = observe(runner, text)
    assert len(sink.records) == 1
    assert record["output_sha256"] == hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert record["validation_status"] == "warn"
    assert record["production_effect"] is False
    assert record["scene_spec_hash"] == "d" * 64
    assert record["scene_spec_delivery"] == "compatible_provider"
    assert record["contract_hash"]
    assert record["source_manifest"] == [{"source_id": "writer-field:goal", "text_hash": "e" * 64}]
    rendered = repr(record)
    assert "private" not in rendered
    assert all(
        len(span["excerpt"]) <= 140
        for item in record["unsupported_fact_warnings"]
        for span in item["evidence_spans"]
    )


def test_missing_or_unsupported_scene_spec_skips_safely():
    missing = InMemoryShadowValidationSink()
    observe(ShadowBoundaryValidationRunner(enabled=True, sink=missing, scene_spec_provider=lambda *_: None))
    assert missing.records[0]["skip_reason"] == "scene_spec_unavailable"
    assert missing.records[0]["scene_spec_delivery"] == "unavailable"
    assert "scene_spec_provider_unavailable" not in repr(missing.records[0])
    unsupported = InMemoryShadowValidationSink()
    observe(ShadowBoundaryValidationRunner(enabled=True, sink=unsupported, scene_spec_provider=lambda *_: scene_spec(profile="none")))
    assert unsupported.records[0]["skip_reason"] == "no_executable_deterministic_rules"


def test_explicit_scene_spec_wins_without_calling_compatible_provider():
    calls = []
    sink = InMemoryShadowValidationSink()
    runner = ShadowBoundaryValidationRunner(
        enabled=True,
        sink=sink,
        scene_spec_provider=lambda *args: calls.append(args) or scene_spec(profile="none"),
    )
    record = observe(runner, explicit_scene_spec=scene_spec())
    assert calls == []
    assert record["scene_spec_delivery"] == "explicit_artifact"
    assert record["scene_spec_hash"] == "d" * 64
    assert record["contract_hash"]
    assert record["validation_status"] == "warn"


def test_validator_failure_is_shadow_error_and_duplicate_is_not_recorded():
    class BrokenValidator:
        def validate(self, *args, **kwargs):
            raise RuntimeError("private body must never enter error output")

    sink = InMemoryShadowValidationSink()
    runner = ShadowBoundaryValidationRunner(
        enabled=True, sink=sink, scene_spec_provider=lambda *_: scene_spec(),
        validator=BrokenValidator(),
    )
    first = observe(runner)
    second = observe(runner)
    assert first["validation_status"] == "shadow_error"
    assert first["error_type"] == "RuntimeError"
    assert "private body" not in repr(first)
    assert second is None
    assert len(sink.records) == 1


def test_scene_spec_adapter_preserves_unknown_status_and_sources():
    spec = scene_spec()
    contract = ValidationContract.from_scene_spec(spec)
    assert contract.assertions[0].status == "unknown"
    assert contract.rule_profile == "relative_unknown"
    assert contract.source_refs == ({"source_id": "state:1", "text_hash": "b" * 64, "role": "unknown"},)


def test_runner_api_cannot_receive_messages_prompt_or_repair_controls():
    parameters = inspect.signature(ShadowBoundaryValidationRunner.observe_committed).parameters
    assert "messages" not in parameters
    assert "prompt" not in parameters
    assert "retry" not in parameters
    assert "repair" not in parameters
