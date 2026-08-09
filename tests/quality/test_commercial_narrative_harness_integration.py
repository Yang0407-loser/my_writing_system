from pathlib import Path

from app.config import settings
from app.utils.prompt_templates import WRITING_PROMPT, WRITING_SECTION1_PROMPT


ROOT = Path(__file__).resolve().parents[2]


def test_default_is_shadow_and_canary_is_the_only_injection_mode():
    assert settings.WRITER_COMMERCIAL_HARNESS_MODE == "shadow"

    source = (ROOT / "app" / "agents" / "writer.py").read_text(encoding="utf-8")
    assert "effective_style_context = compose_narrative_control_context(" in source
    assert '"injected": commercial_harness_mode == "canary"' in source
    assert '"commercial_narrative_harness_v0"' in source


def test_narrative_integrity_defaults_to_shadow_and_is_observable():
    config_source = (ROOT / "app" / "config.py").read_text(encoding="utf-8")
    assert '"WRITER_NARRATIVE_INTEGRITY_MODE", "shadow"' in config_source
    source = (ROOT / "app" / "agents" / "writer.py").read_text(encoding="utf-8")
    assert '"narrative_integrity_observations_v0"' in source
    assert '"injected": narrative_integrity_mode == "canary"' in source


def test_world_pressure_defaults_to_shadow_requires_explicit_preset_and_is_observable():
    config_source = (ROOT / "app" / "config.py").read_text(encoding="utf-8")
    assert '"WRITER_WORLD_PRESSURE_MODE", "shadow"' in config_source
    assert '"WRITER_WORLD_PRESSURE_PRESET", "none"' in config_source

    source = (ROOT / "app" / "agents" / "writer.py").read_text(encoding="utf-8")
    assert '"world_pressure_observations_v0"' in source
    assert '"injected": world_pressure_mode == "canary"' in source


def test_integrity_and_world_pressure_are_delivered_before_soft_writing_guidance():
    for template in (WRITING_PROMPT, WRITING_SECTION1_PROMPT):
        integrity_position = template.index("{narrative_integrity_constraints}")
        soft_guidance_position = template.index("========== 写作指引（请尽量参考）==========")
        style_position = template.index("{style_examples}")

        assert integrity_position < soft_guidance_position < style_position


def test_writer_keeps_integrity_out_of_style_examples():
    source = (ROOT / "app" / "agents" / "writer.py").read_text(encoding="utf-8")

    assert '"narrative_integrity_constraints": effective_integrity_constraints' in source
    assert 'integrity_context=""' in source
    assert '"delivery": (' in source


def test_invalid_integrity_mode_warns_and_falls_back_to_shadow(monkeypatch):
    monkeypatch.setattr(
        settings, "WRITER_NARRATIVE_INTEGRITY_MODE_RAW", "unexpected"
    )

    assert any(
        "WRITER_NARRATIVE_INTEGRITY_MODE=unexpected" in item
        for item in settings.validate()
    )


def test_config_validation_warns_and_falls_back_to_shadow(monkeypatch):
    monkeypatch.setattr(
        settings, "WRITER_COMMERCIAL_HARNESS_MODE_RAW", "unexpected"
    )

    warnings = settings.validate()

    assert any("WRITER_COMMERCIAL_HARNESS_MODE=unexpected" in item for item in warnings)
