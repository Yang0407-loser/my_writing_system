import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PROVIDER = ROOT / "app" / "writing" / "writer_execution_contract.py"
WRITER = ROOT / "app" / "agents" / "writer.py"
CONFIG = ROOT / "app" / "config.py"
REPORT = ROOT / "reports" / "writer-first-draft-execution-contract.json"
ATTRIBUTION_REPORT = (
    ROOT / "reports" / "writer-execution-contract-canary-attribution.json"
)


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


def test_v11_attribution_report_closes_demo_without_changing_cap_or_semantics():
    report = json.loads(ATTRIBUTION_REPORT.read_text(encoding="utf-8"))
    assert report["status"] == "v11_engineering_complete_not_eligible_for_demo"
    assert report["default_mode"] == "off"
    assert report["token_cap"] == 450
    assert [
        item["attempted_estimated_tokens"]
        for item in report["v11_reconstruction"]
    ] == [888, 579, 496, 339]
    assert [item["injected"] for item in report["v11_reconstruction"]] == [
        False,
        False,
        False,
        True,
    ]
    assert all(
        item["source_traceable"] and item["semantic_hash_stable"]
        for item in report["v11_reconstruction"]
    )
    assert report["semantic_hash_regression"] == {
        "S1.3": "f3a74abfeaf7c33d34de21c74cec80a6f4cf13476f77133ceab5b93f9b4c77cc",
        "S1.4": "b737888054bf25b37bd58052e867cc2740302de6cedc303a8ca0079e21fc4e9b",
        "preserved": True,
    }
    assert report["promotion_gate"]["token_cap_raised"] is False
    assert report["promotion_gate"]["required_events_deleted"] is False
    assert report["promotion_gate"]["allow_one_more_real_demo"] is False
    assert report["runtime_calls"] == {
        "writer": 0,
        "llm": 0,
        "new_generation": 0,
    }


def test_v11_observability_is_redacted_and_keeps_failed_attempt_budget():
    source = PROVIDER.read_text(encoding="utf-8")
    assert '"attempted_estimated_tokens"' in source
    assert '"component_token_breakdown"' in source
    assert '"characters_per_required_event"' in source
    assert "EXECUTION_CONTRACT_TOKEN_CAP = 450" in source

    report_text = ATTRIBUTION_REPORT.read_text(encoding="utf-8")
    forbidden_private_text = (
        "一个只肯把时间分给面包的人",
        "老板从沙发上跳下来",
        "完整 Writer messages",
        "api.deepseek.com",
        "sk-",
    )
    assert all(value not in report_text for value in forbidden_private_text)
