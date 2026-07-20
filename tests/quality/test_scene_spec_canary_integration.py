from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PROVIDER = ROOT / "app" / "writing" / "scene_spec_provider.py"
WRITER = ROOT / "app" / "agents" / "writer.py"
CONFIG = ROOT / "app" / "config.py"


def test_production_scene_spec_code_does_not_import_tests_or_evaluation_answers():
    source = PROVIDER.read_text(encoding="utf-8")
    forbidden = (
        "tests.", "must_recall_facts", "gold_sections", "human_relevant",
        "supports_which_fact", "user_review", "arm_mapping", ".phase4r_final_trial_runtime",
        "BoundaryValidator", "StateFrame", "ContextBroker",
    )
    assert all(value not in source for value in forbidden)


def test_default_off_configuration_and_allowlist_are_explicit():
    source = CONFIG.read_text(encoding="utf-8")
    assert 'os.getenv("WRITER_SCENE_SPEC_MODE", "off")' in source
    assert '"WRITER_SCENE_SPEC_CANARY_TASK_IDS", ""' in source
    assert '{"off", "shadow", "canary"}' in source


def test_writer_applies_scene_spec_after_prompt_build_and_before_generation():
    source = WRITER.read_text(encoding="utf-8")
    build_at = source.index("prompt_artifact = PromptBuilder().build")
    apply_at = source.index("scene_spec_canary.apply", build_at)
    generate_at = source.index("self._generate_with_retry", apply_at)
    assert build_at < apply_at < generate_at
    assert "if scene_spec_canary.enabled:" in source[build_at:apply_at]


def test_scene_spec_observability_contract_excludes_private_payloads():
    source = PROVIDER.read_text(encoding="utf-8")
    for field in (
        "task_id_hash", "scene_spec_hash", "estimated_tokens", "source_ids",
        "fallback_reason", "compile_elapsed_ms", "production_effect",
    ):
        assert f'"{field}"' in source
    record_block = source[source.index("def _record("):source.index("def _log(")]
    assert '"messages"' not in record_block
    assert '"prompt"' not in record_block.lower()
    assert '"text"' not in record_block
