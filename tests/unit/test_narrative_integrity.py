from app.realization_policy import compile_realization_policy, render_realization_policy
from app.writing.commercial_narrative_harness import (
    compile_commercial_narrative_harness,
    render_commercial_narrative_harness,
)
from app.writing.narrative_integrity import (
    NARRATIVE_INTEGRITY_VERSION,
    compile_narrative_integrity,
    compile_world_pressure_contract,
    compose_narrative_control_context,
    narrative_integrity_hash,
    render_narrative_integrity,
    render_world_pressure_contract,
    world_pressure_hash,
)


def test_integrity_has_exactly_five_genre_agnostic_rules():
    policy = compile_narrative_integrity()
    rendered = render_narrative_integrity(policy)

    assert policy.version == NARRATIVE_INTEGRITY_VERSION
    assert len(policy.rules) == 5
    assert "人物欲望" in rendered
    assert "段尾金句" in rendered
    assert "商业" not in rendered
    assert "句长" not in rendered


def test_control_ownership_is_separated():
    integrity = render_narrative_integrity(compile_narrative_integrity())
    genre = render_commercial_narrative_harness(
        compile_commercial_narrative_harness(scene_text="追兵冲来")
    )
    style = render_realization_policy(compile_realization_policy({}))

    assert "基础降 AI 味" not in genre
    assert "配角可以拒绝" not in genre
    assert "任务清单" not in style
    assert "总结式结尾" not in style
    assert "追读" not in integrity


def test_all_shadow_modes_keep_existing_style_context_byte_identical():
    base = "原有风格上下文\n保持原样"

    result = compose_narrative_control_context(
        integrity_context="可信度",
        integrity_mode="shadow",
        genre_context="商业类型",
        genre_mode="shadow",
        style_context=base,
    )

    assert result == base


def test_active_controls_have_fixed_integrity_genre_style_order():
    result = compose_narrative_control_context(
        integrity_context="可信度层",
        integrity_mode="canary",
        genre_context="类型层",
        genre_mode="canary",
        style_context="风格层",
    )

    assert result == "可信度层\n\n类型层\n\n风格层"


def test_integrity_observation_keeps_hashes_without_event_text():
    private_event = "父亲其实是失踪的国王"
    policy = compile_narrative_integrity(
        required_events=[
            {"source_id": "outline:S1.1:event:1", "text_hash": "abc", "text": private_event}
        ]
    )

    assert policy.source_refs == (
        {"source_id": "outline:S1.1:event:1", "text_hash": "abc"},
    )
    assert private_event not in render_narrative_integrity(policy)
    assert narrative_integrity_hash(policy) == narrative_integrity_hash(policy)


def test_world_pressure_requires_an_explicit_supported_preset():
    assert compile_world_pressure_contract("none") is None
    assert compile_world_pressure_contract("unknown") is None


def test_modern_urban_pressure_turns_setting_into_causal_constraints():
    contract = compile_world_pressure_contract("modern_urban_realism")
    assert contract is not None
    rendered = render_world_pressure_contract(contract)

    assert "送达正确对象或系统" in rendered
    assert "营业日、生产时间" in rendered
    assert "时间只能向前推进" in rendered
    assert "沉默不能自动视为同意" in rendered
    assert "位置与归属持续有效" in rendered
    assert "林晚" not in rendered
    assert "野面包" not in rendered
    assert "商业推进" not in rendered
    assert world_pressure_hash(contract) == world_pressure_hash(contract)


def test_world_pressure_stays_inside_integrity_before_genre_and_style():
    base_integrity = "可信度底线"
    pressure = "世界因果压力"
    result = compose_narrative_control_context(
        integrity_context=f"{base_integrity}\n\n{pressure}",
        integrity_mode="canary",
        genre_context="商业类型",
        genre_mode="canary",
        style_context="表达风格",
    )

    assert result == "可信度底线\n\n世界因果压力\n\n商业类型\n\n表达风格"
