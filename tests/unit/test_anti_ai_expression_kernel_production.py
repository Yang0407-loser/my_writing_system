from pathlib import Path

from app.config import settings
from app.utils.prompt_templates import WRITING_PROMPT, WRITING_SECTION1_PROMPT
from app.writing.anti_ai_expression_kernel import (
    KERNEL_VERSION,
    AntiAIExpressionController,
    expression_kernel_hash,
    render_expression_kernel,
)
from experiments.anti_ai_expression_kernel_v0.kernel import (
    expression_kernel_hash as experiment_kernel_hash,
)


ROOT = Path(__file__).resolve().parents[2]


def test_production_kernel_is_byte_identical_to_validated_experiment():
    assert KERNEL_VERSION == "anti-ai-expression-kernel-v0"
    assert expression_kernel_hash() == (
        "67b62b5c4ae59669efe50aeb299d692a0bac91a11c7857652add65ff51dbe1f4"
    )
    assert expression_kernel_hash() == experiment_kernel_hash()
    assert render_expression_kernel().count("\n") >= 6


def test_off_and_shadow_do_not_change_writer_style_context():
    base = "原有风格上下文"

    assert AntiAIExpressionController("off").compose(base) == base
    assert AntiAIExpressionController("shadow").compose(base) == base


def test_canary_appends_frozen_kernel_once():
    base = "原有风格上下文"
    rendered = AntiAIExpressionController("canary").compose(base)

    assert rendered.startswith(base + "\n\n")
    assert rendered.count("## 表达实现约束（anti-ai-expression-kernel-v0）") == 1
    assert "不允许改变给定事件、事实、人物关系和结束状态" in rendered


def test_final_constraints_are_empty_unless_canary_and_end_with_spacing():
    assert AntiAIExpressionController("off").final_prompt_constraints() == ""
    assert AntiAIExpressionController("shadow").final_prompt_constraints() == ""

    constraints = AntiAIExpressionController("canary").final_prompt_constraints()
    assert constraints == render_expression_kernel() + "\n\n"


def test_kernel_slot_is_unique_and_immediately_precedes_final_output_instruction():
    for template in (WRITING_PROMPT, WRITING_SECTION1_PROMPT):
        assert template.count("{anti_ai_expression_constraints}") == 1
        assert template.index("{anti_ai_expression_constraints}") > template.index(
            "{retrieved_context}"
        )
        assert "{anti_ai_expression_constraints}请输出本小节的纯正文" in template


def test_invalid_mode_fails_closed_to_off():
    controller = AntiAIExpressionController("unexpected")

    assert controller.mode == "off"
    assert controller.compose("原文") == "原文"


def test_observation_is_non_rewriting_and_non_gating():
    observation = AntiAIExpressionController("canary").observation(
        section=2,
        subsection=3,
    )

    assert observation["injected"] is True
    assert observation["revision_enabled"] is False
    assert observation["production_gate"] is False
    assert observation["kernel_hash"] == expression_kernel_hash()


def test_default_is_off_and_only_canary_injects_in_writer():
    config_source = (ROOT / "app" / "config.py").read_text(encoding="utf-8")
    writer_source = (ROOT / "app" / "agents" / "writer.py").read_text(encoding="utf-8")

    assert '"WRITER_ANTI_AI_EXPRESSION_MODE", "off"' in config_source
    assert "anti_ai_expression_controller.final_prompt_constraints(" in writer_source
    assert '"anti_ai_expression_kernel_v0"' in writer_source
    assert settings.WRITER_ANTI_AI_EXPRESSION_MODE in {"off", "shadow", "canary"}


def test_invalid_config_mode_warns_and_falls_back_to_off(monkeypatch):
    monkeypatch.setattr(
        settings,
        "WRITER_ANTI_AI_EXPRESSION_MODE_RAW",
        "unexpected",
    )

    warnings = settings.validate()

    assert any(
        "WRITER_ANTI_AI_EXPRESSION_MODE=unexpected" in warning
        for warning in warnings
    )
