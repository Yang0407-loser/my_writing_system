from pathlib import Path

from app.writing.world_runtime_bakery_gold import (
    build_saturday_bakery_gold_fixture,
)
from app.writing.world_runtime_compiler import WorldRuntimeCompiler
from app.writing.world_runtime_consumption import build_wr1_consumer_registry
from app.writing.world_runtime_kernel import build_minimal_universal_kernel
from app.writing.world_runtime_pack_modern_urban import (
    build_modern_urban_cn_2020s_candidate_pack,
)
from app.writing.world_runtime_prompt import (
    WORLD_RUNTIME_PROMPT_HEADER,
    RuntimePromptObservation,
    RuntimePromptProjection,
    WorldRuntimePromptController,
    compile_runtime_prompt_projection,
    render_runtime_prompt_projection,
)
from app.writing.world_runtime_resolver import WorldRuntimeResolver


ROOT = Path(__file__).resolve().parents[2]


def _runtime():
    fixture = build_saturday_bakery_gold_fixture()
    resolved = WorldRuntimeResolver().resolve(
        constitution=fixture.constitution,
        candidate_packs=(build_modern_urban_cn_2020s_candidate_pack(),),
        kernel=build_minimal_universal_kernel(),
    )
    frame = WorldRuntimeCompiler().compile(
        resolved=resolved,
        state_before=fixture.state_before,
        event_contract=fixture.event_contract,
    )
    return resolved, frame


def _messages():
    return [
        {"role": "system", "content": "Write fiction only."},
        {"role": "user", "content": "Write the frozen scene."},
    ]


def test_projection_is_deterministic_compact_and_writer_relevant():
    resolved, frame = _runtime()
    first = compile_runtime_prompt_projection(frame=frame, resolved=resolved)
    second = compile_runtime_prompt_projection(frame=frame, resolved=resolved)
    rendered = render_runtime_prompt_projection(first)

    assert first == second
    assert first.projection_hash == second.projection_hash
    assert first.estimated_tokens <= 1100
    assert "world_clock: time=04:20" in rendered
    assert "draft→submitted→published" in rendered
    assert "company:lin-wan.resignation_acknowledged" in rendered
    assert "meta.commit.revision" not in rendered
    assert "meta.delta.idempotency" not in rendered


def test_projection_excludes_debug_provenance_and_inactive_candidates():
    resolved, frame = _runtime()
    projection = compile_runtime_prompt_projection(frame=frame, resolved=resolved)
    payload = projection.model_dump_json()
    rendered = render_runtime_prompt_projection(projection)

    forbidden = (
        "source_hash",
        "source_id",
        "excluded_artifacts",
        "conflict_report",
        "inactive_candidate",
        "event_contract_hash",
        "relevant_source_hash",
    )
    assert all(item not in payload for item in forbidden)
    assert all(item not in rendered for item in forbidden)
    assert not any(
        rule.semantic_key.startswith("finance.") for rule in projection.rules
    )


def test_off_and_shadow_are_byte_preserving_and_never_inject():
    resolved, frame = _runtime()
    baseline = _messages()
    for mode in ("off", "shadow"):
        result = WorldRuntimePromptController(mode=mode).apply(
            baseline,
            task_id="task:wr1:allowed",
            frame=frame,
            resolved=resolved,
        )
        assert list(result.messages) == baseline
        assert result.observation.injected is False
    assert WorldRuntimePromptController(mode="off").apply(
        baseline,
        task_id="task:wr1:allowed",
        frame=frame,
        resolved=resolved,
    ).observation.compiled is False
    assert WorldRuntimePromptController(mode="shadow").apply(
        baseline,
        task_id="task:wr1:allowed",
        frame=frame,
        resolved=resolved,
    ).observation.compiled is True


def test_canary_requires_exact_allowlist_and_only_changes_last_user_message():
    resolved, frame = _runtime()
    baseline = _messages()
    controller = WorldRuntimePromptController(
        mode="canary", canary_task_ids={"task:wr1:allowed"}
    )
    denied = controller.apply(
        baseline,
        task_id="task:wr1:allowed-suffix",
        frame=frame,
        resolved=resolved,
    )
    allowed = controller.apply(
        baseline,
        task_id="task:wr1:allowed",
        frame=frame,
        resolved=resolved,
    )

    assert list(denied.messages) == baseline
    assert denied.observation.fallback_code == "task_not_allowlisted"
    assert allowed.observation.injected is True
    assert allowed.messages[0] == baseline[0]
    assert allowed.messages[1]["content"].startswith(baseline[1]["content"])
    assert WORLD_RUNTIME_PROMPT_HEADER in allowed.messages[1]["content"]
    assert baseline == _messages()


def test_token_cap_failure_falls_back_without_mutating_messages():
    resolved, frame = _runtime()
    baseline = _messages()
    result = WorldRuntimePromptController(
        mode="canary",
        canary_task_ids={"task:wr1:allowed"},
        token_cap=1,
    ).apply(
        baseline,
        task_id="task:wr1:allowed",
        frame=frame,
        resolved=resolved,
    )

    assert list(result.messages) == baseline
    assert result.observation.injected is False
    assert result.observation.fallback_code == "runtime_prompt_over_token_cap"


def test_wr1_consumption_registry_has_no_orphan_fields():
    registry = build_wr1_consumer_registry()
    contracts = {item.artifact_type: item for item in registry.contracts}

    assert set(contracts["RuntimePromptProjection"].stable_fields) == set(
        RuntimePromptProjection.model_fields
    )
    assert set(contracts["RuntimePromptObservation"].stable_fields) == set(
        RuntimePromptObservation.model_fields
    )
    assert contracts["RuntimePromptProjection"].retention == "transient"
    assert contracts["RuntimePromptObservation"].retention == "permanent_audit"
    assert registry.orphaned_stable_fields == ()


def test_wr1_does_not_wire_world_runtime_into_production_writer():
    writer = (ROOT / "app" / "agents" / "writer.py").read_text(encoding="utf-8")
    writing_init = (ROOT / "app" / "writing" / "__init__.py").read_text(
        encoding="utf-8"
    )

    assert "world_runtime_prompt" not in writer
    assert "WorldRuntimePromptController" not in writing_init
