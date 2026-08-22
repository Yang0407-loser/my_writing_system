import ast
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / "reports" / "mandatory-event-warn-only-integration.json"
CONFIG = ROOT / "app" / "config.py"
POLICY = ROOT / "app" / "writing" / "mandatory_event_policy.py"
CONTROLLER = ROOT / "app" / "writing" / "generation_controller.py"
WRITER = ROOT / "app" / "agents" / "writer.py"


def _report():
    return json.loads(REPORT.read_text(encoding="utf-8"))


def test_default_is_warn_and_retry_requires_an_explicit_allowlist():
    config = CONFIG.read_text(encoding="utf-8")
    assert '"WRITER_MANDATORY_EVENT_MODE", "warn"' in config
    assert '"WRITER_MANDATORY_EVENT_RETRY_TASK_IDS", ""' in config
    assert '{"off", "warn", "retry"}' in config

    report = _report()
    assert report["default_mode"] == "warn"
    assert report["retry_scope"] == "exact_canonical_uuid_allowlist_only"
    assert report["default_mandatory_retry_calls"] == 0


def test_detector_semantics_are_frozen_and_observation_is_final_candidate_only():
    policy = POLICY.read_text(encoding="utf-8")
    controller = CONTROLLER.read_text(encoding="utf-8")
    assert "_extract_lock_keywords" in policy
    assert "THRESHOLD = 0.5" in policy
    assert "selected_keyword_hashes" in policy
    assert "_record_final_mandatory_observation" in controller
    assert controller.index("character_violation") < controller.index(
        "_record_final_mandatory_observation"
    )
    assert controller.index("repetition") < controller.index(
        "_record_final_mandatory_observation"
    )


def test_observation_contract_excludes_private_payloads():
    source = POLICY.read_text(encoding="utf-8")
    tree = ast.parse(source)
    string_literals = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    required = {
        "task_id_hash",
        "candidate_output_sha256",
        "contract_hash",
        "violated_event_hashes",
        "selected_keyword_hashes",
        "would_have_retried",
        "actual_retry_count",
        "production_effect",
    }
    assert required <= string_literals
    forbidden_record_keys = {
        "candidate",
        "mandatory_events_text",
        "messages",
        "prompt",
        "scene_spec",
        "api_key",
    }
    assert not forbidden_record_keys.intersection(string_literals)


def test_report_keeps_mandatory_and_arc_chains_separate_and_scoped():
    report = _report()
    assert report["scope"]["writer_llm_calls"] == 0
    assert report["scope"]["new_generation_runs"] == 0
    assert report["unchanged"]["character_arc_planning"] is True
    assert report["unchanged"]["arc_post_check"] is True
    assert report["unchanged"]["scene_spec"] is True
    assert report["observability"]["arc_post_check_fields_included"] is False
    assert report["real_warn_only_samples"] == 0


def test_character_arc_post_check_remains_warning_only():
    source = WRITER.read_text(encoding="utf-8")
    start = source.index("pc = post_check(sub_text, required_events)")
    end = source.index("self._adjust_generated_length(", start)
    post_check_block = source[start:end]
    assert "logger.warning" in post_check_block
    assert '"event": "rule_warning"' in post_check_block
    assert "_generate_with_retry" not in post_check_block
    assert "chat_completion" not in post_check_block


def test_app_code_does_not_import_tests_or_benchmarks():
    for path in (POLICY, CONTROLLER):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = [
            ast.unparse(node)
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
        ]
        assert all("tests" not in item and "benchmarks" not in item for item in imports)
