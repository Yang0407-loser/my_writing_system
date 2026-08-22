import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tests" / "benchmarks" / "phase4r_final_field_trial.py"
RUNTIME = ROOT / ".phase4r_final_trial_runtime"
PUBLIC_PACKAGE = ROOT / "reports" / "phase4r-final-real-writing-trial-package.json"


def test_package_uses_real_four_scene_ab_contract_without_old_fixture_answers():
    source = SCRIPT.read_text(encoding="utf-8")
    assert 'ARMS = ("legacy_full", "legacy_full_scene_spec")' in source
    assert "EXPECTED_SUBSECTIONS = 4" in source
    assert '"main_writer_calls": 8' in source
    forbidden = (
        "Q4", "Q6", "Q7", "Q8", "must_recall_facts", "gold_sections",
        "human_relevant", "supports_which_fact", "blind_review.completed.json",
        "phase4r_r4_attribution", "phase4r_r5_boundary_validator",
    )
    assert all(value not in source for value in forbidden)


def test_private_runtime_is_gitignored_and_snapshot_code_removes_api_key():
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert ".phase4r_final_trial_runtime/" in gitignore
    source = SCRIPT.read_text(encoding="utf-8")
    assert 'decoded_checkpoint.pop("api_key", None)' in source


def test_public_package_contains_hashes_and_counts_but_no_story_text():
    public = json.loads(PUBLIC_PACKAGE.read_text(encoding="utf-8"))
    assert public["status"] == "prepared_not_generated"
    assert public["scene_count"] == 4
    assert public["main_writer_calls"] == 8
    assert public["arms"] == ["legacy_full", "legacy_full_scene_spec"]
    assert public["contains_story_text"] is False
    assert public["edit_cost_role"] == "optional_diagnostic"
    assert all(item["scene_spec_tokens"] <= 400 for item in public["scenes"])
    rendered = json.dumps(public, ensure_ascii=False)
    assert "messages" not in rendered
    assert "description" not in rendered


def test_production_writer_and_validator_are_not_modified_by_trial_script():
    source = SCRIPT.read_text(encoding="utf-8")
    assert "patch(\"app.agents.writer.rule_store" in source
    assert "from app.writing.boundary_validator" not in source
    assert "WRITER_BOUNDARY_VALIDATOR_SHADOW" in source
    assert "False" in source


def test_edit_cost_is_optional_and_absent_from_release_gates():
    source = SCRIPT.read_text(encoding="utf-8")
    assert '"edit_cost_status": edit_cost_status' in source
    assert '"b_lower_average_edit_characters"' not in source
    assert '"b_no_more_edit_time"' not in source
    assert '"b_goal_completion_not_lower"' in source
    assert '"b_no_increase_in_continuity_event_order_or_boundary_errors"' in source
