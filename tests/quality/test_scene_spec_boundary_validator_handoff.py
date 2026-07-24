import ast
from pathlib import Path

from app.config import settings


ROOT = Path(__file__).resolve().parents[2]
WRITER = ROOT / "app" / "agents" / "writer.py"
RUNNER = ROOT / "app" / "writing" / "shadow_validation.py"


def test_handoff_defaults_remain_off_and_execution_contract_stays_off():
    assert settings.WRITER_SCENE_SPEC_MODE == "off"
    assert settings.WRITER_BOUNDARY_VALIDATOR_SHADOW is False
    assert settings.WRITER_EXECUTION_CONTRACT_MODE == "off"


def test_runner_has_no_obsolete_missing_provider_reason_or_recompile_path():
    source = RUNNER.read_text(encoding="utf-8")
    assert "scene_spec_provider_unavailable" not in source
    assert '"scene_spec_unavailable"' in source
    assert '"explicit_artifact"' in source
    assert '"compatible_provider"' in source
    assert "OutlineSceneSpecProvider" not in source
    assert ".build(" not in source


def test_writer_passes_only_typed_artifact_without_private_shadow_payloads():
    source = WRITER.read_text(encoding="utf-8")
    hook_start = source.index("shadow_boundary_validator.observe_committed(")
    hook_end = source.index("\n                )", hook_start)
    hook = source[hook_start:hook_end]
    assert "scene_spec_application.spec" in hook
    assert "rendered" not in hook
    assert "messages=" not in hook
    assert "prompt=" not in hook

    runner_source = RUNNER.read_text(encoding="utf-8")
    forbidden_record_keys = (
        '"text": text',
        '"messages"',
        '"prompt"',
        '"rendered"',
        '"api_key"',
    )
    assert all(value not in runner_source for value in forbidden_record_keys)


def test_production_app_does_not_import_tests():
    for path in (ROOT / "app").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = [
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
        ]
        assert all("tests" not in ast.unparse(node) for node in imports), path
