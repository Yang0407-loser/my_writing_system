import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PROVIDER = ROOT / "app" / "writing" / "writer_execution_contract.py"
WRITER = ROOT / "app" / "agents" / "writer.py"
CONFIG = ROOT / "app" / "config.py"
REPORT = ROOT / "reports" / "writer-first-draft-execution-contract.json"


def test_production_contract_does_not_import_tests_or_evaluation_answers():
    source = PROVIDER.read_text(encoding="utf-8")
    forbidden = (
        "tests.",
        "must_recall_facts",
        "gold_sections",
        "human_relevant",
        "supports_which_fact",
        "user_review",
        "arm_mapping",
        "BoundaryValidator",
        "ContextBroker",
    )
    assert all(value not in source for value in forbidden)


def test_default_off_configuration_is_explicit_without_allowlist():
    source = CONFIG.read_text(encoding="utf-8")
    assert '"WRITER_EXECUTION_CONTRACT_MODE", "off"' in source
    assert '{"off", "shadow", "canary"}' in source
    assert "WRITER_EXECUTION_CONTRACT_CANARY_TASK_IDS" not in source


def test_writer_applies_contract_after_prompt_build_before_generation():
    source = WRITER.read_text(encoding="utf-8")
    build_at = source.index("prompt_artifact = PromptBuilder().build")
    apply_at = source.index("execution_contract_controller.apply", build_at)
    generate_at = source.index("self._generate_with_retry", apply_at)
    observe_at = source.index("execution_contract_controller.observe_output", generate_at)
    commit_at = source.index("state_committer.commit_subsection", observe_at)
    assert build_at < apply_at < generate_at < observe_at < commit_at


def test_contract_observability_excludes_private_payload_fields():
    source = PROVIDER.read_text(encoding="utf-8")
    for field in (
        "task_id_hash",
        "contract_hash",
        "scene_spec_hash",
        "required_event_count",
        "overplanned_contract",
        "target_characters",
        "estimated_tokens",
        "fallback_reason",
        "production_effect",
        "output_sha256",
        "output_to_target_ratio",
    ):
        assert f'"{field}"' in source

    record_block = source[source.index("def _record("):source.index("def _log(")]
    assert '"messages"' not in record_block
    assert '"prompt"' not in record_block.lower()
    assert '"text"' not in record_block


def test_app_does_not_import_tests():
    for path in (PROVIDER, WRITER):
        assert "from tests" not in path.read_text(encoding="utf-8")


def test_public_report_freezes_scope_and_zero_runtime_calls():
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    assert report["status"] == "engineering_canary_ready_default_off"
    assert report["default_mode"] == "off"
    assert report["verification"]["writer_calls"] == 0
    assert report["verification"]["llm_calls"] == 0
    assert report["verification"]["real_canary_samples"] == 0
    assert report["stop_rule"]["real_four_subsection_canaries_allowed"] == 1
    assert report["stop_rule"]["additional_batches_authorized"] is False
