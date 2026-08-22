import hashlib
from dataclasses import replace

from app.agents.writer import Writer
from app.config import settings
from app.writing.contracts import PromptArtifact, StateAssertion
from app.writing.prompt_builder import messages_hash
from app.writing.scene_spec_provider import (
    OutlineSceneSpecProvider,
    SceneSpecBuildResult,
)
from app.writing.writer_execution_contract import (
    EXECUTION_CONTRACT_TOKEN_CAP,
    EXECUTION_CONTRACT_HEADER,
    WriterExecutionContractController,
    WriterExecutionContractProvider,
)


def prompt_artifact():
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
        {
            "subsection": 1,
            "title": "Current",
            "description": "Complete the current action",
            "key_points": ["Approach", "Reply"],
        },
        {
            "subsection": 2,
            "title": "Next",
            "description": "Leave the shop",
            "key_points": ["Leave"],
        },
    )


def required_events(count=2):
    return [
        {
            "source_id": f"outline:event:{index}",
            "text": f"event {index}",
            "text_hash": hashlib.sha256(f"event {index}".encode()).hexdigest(),
        }
        for index in range(1, count + 1)
    ]


class SpyProvider(WriterExecutionContractProvider):
    def __init__(self, error=None):
        super().__init__()
        self.calls = 0
        self.error = error

    def build(self, **kwargs):
        self.calls += 1
        if self.error:
            raise self.error
        return super().build(**kwargs)


class UnknownSceneProvider(OutlineSceneSpecProvider):
    def build(self, **kwargs):
        built = super().build(**kwargs)
        unknown = StateAssertion(
            assertion_id="unknown:relative",
            subject="character",
            predicate="relative_status",
            value="The relative status is unknown and must not be invented",
            status="unknown",
            evidence_ids=[built.spec.evidence[0].evidence_id],
        )
        spec = built.spec.model_copy(
            update={
                "unknowns_and_conflicts": [
                    *built.spec.unknowns_and_conflicts,
                    unknown,
                ]
            }
        )
        return SceneSpecBuildResult(
            spec=spec,
            rendered=built.rendered,
            source_manifest=built.source_manifest,
        )


def apply(controller, *, events=None, target=1000, current=None, next_sub=None):
    current_default, next_default = outlines()
    return controller.apply(
        prompt_artifact(),
        task_id="task-canary",
        section=2,
        current_subsection=current if current is not None else current_default,
        next_subsection=next_sub if next_sub is not None else next_default,
        is_last_subsection=False,
        required_events=events if events is not None else required_events(),
        target_characters=target,
    )


def test_default_configuration_is_off_and_existing_modes_are_unchanged():
    assert settings.WRITER_EXECUTION_CONTRACT_MODE == "off"
    assert settings.WRITER_MANDATORY_EVENT_MODE == "warn"
    assert settings.WRITER_CONDENSE_MODE == "warn"
    assert settings.WRITER_INCREMENTAL_SECTION_REVIEW is False


def test_off_does_not_build_or_record():
    provider = SpyProvider()
    controller = WriterExecutionContractController(mode="off", provider=provider)
    original = prompt_artifact()
    result = apply(controller)

    assert provider.calls == 0
    assert result.prompt is original or result.prompt == original
    assert result.record is None
    assert result.contract is None


def test_invalid_mode_is_off_and_config_warns(monkeypatch):
    provider = SpyProvider()
    controller = WriterExecutionContractController(
        mode="unexpected", provider=provider
    )
    assert controller.mode == "off"
    assert apply(controller).record is None
    assert provider.calls == 0

    monkeypatch.setattr(
        settings, "WRITER_EXECUTION_CONTRACT_MODE_RAW", "unexpected"
    )
    assert any(
        "WRITER_EXECUTION_CONTRACT_MODE=unexpected" in warning
        for warning in settings.validate()
    )


def test_shadow_builds_without_changing_messages():
    provider = SpyProvider()
    controller = WriterExecutionContractController(mode="shadow", provider=provider)
    original = prompt_artifact()
    result = controller.apply(
        original,
        task_id="task-canary",
        section=2,
        current_subsection=outlines()[0],
        next_subsection=outlines()[1],
        is_last_subsection=False,
        required_events=required_events(),
        target_characters=1000,
    )

    assert provider.calls == 1
    assert result.prompt is original
    assert result.prompt.messages_hash == original.messages_hash
    assert result.record["compiled"] is True
    assert result.record["injected"] is False


def test_canary_injects_once_with_order_length_and_boundary():
    result = apply(WriterExecutionContractController(mode="canary"))
    contract = result.contract

    assert result.prompt.messages[-1]["content"].count(EXECUTION_CONTRACT_HEADER) == 1
    assert "SceneSpec" not in result.prompt.messages[-1]["content"]
    assert contract.ordered_required_events == ("event 1", "event 2")
    assert contract.target_characters == 1000
    assert contract.soft_min_characters == 850
    assert contract.soft_max_characters == 1300
    assert "Leave" in contract.stop_boundary
    assert contract.estimated_tokens <= 450
    assert result.record["production_effect"] is True


def test_duplicate_source_and_hash_is_deduplicated_without_reordering():
    events = required_events()
    result = apply(
        WriterExecutionContractController(mode="canary"),
        events=[events[0], events[0], events[1]],
    )
    assert result.contract.ordered_required_events == ("event 1", "event 2")


def test_unknown_remains_a_prohibited_invention_not_confirmed():
    provider = WriterExecutionContractProvider(
        scene_spec_provider=UnknownSceneProvider()
    )
    result = apply(
        WriterExecutionContractController(mode="canary", provider=provider)
    )

    assert result.contract.confirmed_continuity == ()
    assert "The relative status is unknown and must not be invented" in (
        result.contract.prohibited_inventions
    )


def test_more_than_five_events_are_preserved_and_marked_overplanned():
    result = apply(
        WriterExecutionContractController(mode="canary"),
        events=required_events(6),
    )
    assert len(result.contract.ordered_required_events) == 6
    assert result.contract.overplanned_contract is True


def test_last_subsection_uses_current_objective_as_boundary():
    current = {
        "subsection": 4,
        "title": "End",
        "description": "Finish the current scene",
        "key_points": ["Finish"],
    }
    controller = WriterExecutionContractController(mode="canary")
    result = controller.apply(
        prompt_artifact(),
        task_id="task-canary",
        section=2,
        current_subsection=current,
        next_subsection=None,
        is_last_subsection=True,
        required_events=required_events(1),
        target_characters=1000,
    )
    assert "Finish the current scene" in result.contract.stop_boundary


def test_budget_provider_error_and_missing_outline_fall_back_without_leak(caplog):
    original = prompt_artifact()
    budget = apply(
        WriterExecutionContractController(mode="canary", token_cap=1)
    )
    assert budget.prompt == original
    assert budget.record["fallback_reason"] == "contract_over_budget"
    assert budget.record["estimated_tokens"] > 0
    assert budget.record["attempted_estimated_tokens"] > 0
    assert budget.record["component_token_breakdown"]["total"] > 0
    assert budget.record["required_event_count"] == 2
    assert budget.record["characters_per_required_event"] == 500.0

    private_error = "private prose must not be logged"
    failed = apply(
        WriterExecutionContractController(
            mode="canary", provider=SpyProvider(ValueError(private_error))
        )
    )
    assert failed.prompt == original
    assert failed.record["fallback_reason"] == "ValueError"
    assert private_error not in caplog.text

    missing = apply(
        WriterExecutionContractController(mode="canary"), current={}
    )
    assert missing.prompt == original
    assert missing.record["fallback_reason"] == "current_target_missing"


def test_contract_hash_is_deterministic_and_source_manifest_is_traceable():
    controller = WriterExecutionContractController(mode="canary")
    first = apply(controller)
    second = apply(controller)

    assert first.contract.contract_hash == second.contract.contract_hash
    assert first.contract.scene_spec_hash == second.contract.scene_spec_hash
    assert all(
        item.source_id and item.text_hash
        for item in first.contract.source_manifest
    )


def test_output_observation_is_redacted_and_uses_mandatory_result(caplog):
    controller = WriterExecutionContractController(mode="canary")
    application = apply(controller)
    private_output = "private generated prose"
    record = controller.observe_output(
        application,
        output=private_output,
        mandatory_observation={
            "would_have_retried": True,
            "actual_retry_count": 0,
        },
    )

    assert record["output_sha256"] == hashlib.sha256(
        private_output.encode()
    ).hexdigest()
    assert record["mandatory_would_have_retried"] is True
    assert record["actual_retry_count"] == 0
    assert private_output not in caplog.text


def test_writer_event_sources_match_existing_mandatory_order_and_deduplication():
    sources = Writer._collect_mandatory_event_sources(
        key_points=["A", "B"],
        section_key_points=["B", "C"],
        sub_desc="A",
        section_num=2,
        sub_num=1,
    )
    assert [item["text"] for item in sources] == ["A", "B", "C"]
    assert all(item["source_id"] and item["text_hash"] for item in sources)


def _accounted_tokens(breakdown):
    return (
        breakdown["objective"]
        + sum(
            item["tokens"]
            for item in breakdown["required_events"]
            if item["rendered"]
        )
        + sum(
            item["tokens"]
            for item in breakdown["confirmed_continuity"]
            if item["rendered"]
        )
        + sum(
            item["tokens"]
            for item in breakdown["prohibited_inventions"]
            if item["rendered"]
        )
        + breakdown["length_instruction"]
        + breakdown["stop_boundary"]
        + breakdown["wrapper_and_labels"]
    )


def test_render_deduplicates_only_normalized_exact_repetitions():
    first, second = required_events()
    duplicate = {
        "source_id": "outline:event:duplicate",
        "text": "  event   1  ",
        "text_hash": hashlib.sha256(b"  event   1  ").hexdigest(),
    }
    result = apply(
        WriterExecutionContractController(mode="shadow"),
        events=[first, duplicate, second],
    )

    assert result.contract.ordered_required_events == (
        "event 1",
        "event   1",
        "event 2",
    )
    rendered = WriterExecutionContractProvider.render(result.contract)
    assert rendered.count("event 1") == 1
    assert "event 2" in rendered
    assert len(result.contract.source_manifest) >= 3


def test_continuity_and_prohibited_exact_duplicates_stay_typed_but_not_rendered():
    result = apply(WriterExecutionContractController(mode="shadow"))
    contract = result.contract.model_copy(
        update={
            "confirmed_continuity": (
                result.contract.objective,
                "continuity remains",
                "continuity remains",
            ),
            "prohibited_inventions": (
                result.contract.ordered_required_events[0],
                "do not invent a sibling",
                "do not invent a sibling",
            ),
        }
    )
    rendered, breakdown = WriterExecutionContractProvider.render_with_breakdown(
        contract
    )

    assert contract.confirmed_continuity[0] == contract.objective
    assert contract.prohibited_inventions[0] == contract.ordered_required_events[0]
    assert rendered.count("continuity remains") == 1
    assert rendered.count("do not invent a sibling") == 1
    assert [item["rendered"] for item in breakdown["confirmed_continuity"]] == [
        False,
        True,
        False,
    ]
    assert [item["rendered"] for item in breakdown["prohibited_inventions"]] == [
        False,
        True,
        False,
    ]


def test_non_exact_values_are_not_removed_from_rendering():
    result = apply(WriterExecutionContractController(mode="shadow"))
    contract = result.contract.model_copy(
        update={
            "confirmed_continuity": ("event 1 happened earlier",),
            "prohibited_inventions": ("event 1 must not happen later",),
        }
    )
    rendered = WriterExecutionContractProvider.render(contract)
    assert "event 1 happened earlier" in rendered
    assert "event 1 must not happen later" in rendered


def test_budget_breakdown_reconciles_to_rendered_total():
    result = apply(WriterExecutionContractController(mode="shadow"))
    breakdown = result.record["component_token_breakdown"]
    assert _accounted_tokens(breakdown) == breakdown["total"]
    assert breakdown["total"] == result.record["attempted_estimated_tokens"]


def test_v11_length_and_stop_wording_are_explicit_and_last():
    result = apply(WriterExecutionContractController(mode="shadow"))
    rendered = WriterExecutionContractProvider.render(result.contract)
    expected = (
        "以下范围指本小节全文总长度，不是单段长度。"
        "目标约1000字，建议保持在850～1300字；"
        "优先压缩描写完成既定事件，不增加合同外人物、场景或后续事件。"
    )
    assert expected in rendered
    assert rendered.endswith("完成上述事件后立即结束本小节。")
    assert EXECUTION_CONTRACT_TOKEN_CAP == 450


def test_render_change_does_not_change_semantic_contract_hash():
    result = apply(WriterExecutionContractController(mode="shadow"))
    original_hash = result.contract.contract_hash
    rendered = WriterExecutionContractProvider.render(result.contract)
    assert "本小节全文总长度" in rendered
    assert WriterExecutionContractProvider.contract_hash(result.contract) == original_hash
