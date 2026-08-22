import copy
import json

import pytest

import tests.benchmarks.phase4r_final_field_trial as final_trial
from tests.benchmarks.phase4r_final_field_trial import (
    ARMS,
    _compile_real_scene_spec,
    _inject_scene_spec,
    build_review_template,
    evaluate_trial,
)


def private_source():
    return {
        "task_id": "task-real",
        "task": {},
        "checkpoint": {"characters": [], "_prev_handover": []},
        "outline": [{
            "section": 2,
            "title": "真实续写",
            "subsections": [
                {"subsection": 1, "title": "动作一", "description": "真实详细描述", "key_points": []},
                {"subsection": 2, "title": "动作二", "description": "描述二", "key_points": ["回应", "离开"]},
                {"subsection": 3, "title": "动作三", "description": "描述三", "key_points": ["发现"]},
                {"subsection": 4, "title": "动作四", "description": "描述四", "key_points": ["收束"]},
            ],
        }],
    }


def test_real_scene_specs_use_structured_outline_and_stay_under_cap():
    source = private_source()
    specs = [_compile_real_scene_spec(source, item)[0] for item in source["outline"][0]["subsections"]]
    assert all(spec.estimated_tokens <= 400 for spec in specs)
    assert all(spec.planned_events for spec in specs)
    assert all(item.status == "unknown" for spec in specs[:-1] for item in spec.unknowns_and_conflicts)
    assert specs[-1].unknowns_and_conflicts == []


def test_scene_spec_injection_is_the_only_message_change():
    messages = [{"role": "system", "content": "system"}, {"role": "user", "content": "legacy"}]
    original = copy.deepcopy(messages)
    injected = _inject_scene_spec(messages, "SCENE 2.1")
    assert messages == original
    assert injected[0] == original[0]
    assert injected[1]["content"].startswith("legacy")
    assert injected[1]["content"].endswith("SCENE 2.1")


def _candidate(candidate_id, *, edits=None, minutes=None, hard=False, goal=True):
    return {
        "candidate_id": candidate_id,
        "goal_complete": goal,
        "hard_violation": hard,
        "relationship_violation": False,
        "continuity_error": False,
        "fact_error": False,
        "event_order_error": False,
        "crossed_stop_boundary": False,
        "edit_characters": edits,
        "edit_minutes": minutes,
        "review_note": "",
    }


def trial_files(tmp_path, *, b_hard=False, legacy_edits=100, legacy_minutes=10,
                scene_spec_edits=50, scene_spec_minutes=8):
    mapping = {}
    scenes = []
    for index in range(1, 5):
        mapping[str(index)] = {
            "candidate_01": {"arm": "legacy_full"},
            "candidate_02": {"arm": "legacy_full_scene_spec"},
        }
        scenes.append({
            "trial_index": index,
            "preference": "candidate_02",
            "better_continuation_candidate": "candidate_02",
            "positive_effect_note": "事件更清楚" if index <= 2 else "",
            "candidates": [
                _candidate("candidate_01", edits=legacy_edits, minutes=legacy_minutes),
                _candidate(
                    "candidate_02", edits=scene_spec_edits, minutes=scene_spec_minutes,
                    hard=b_hard,
                ),
            ],
        })
    (tmp_path / "arm_mapping.private.json").write_text(json.dumps(mapping), encoding="utf-8")
    review = {
        "schema_version": "phase4r-final-field-trial-user-review-v1",
        "review_provenance": "user_real_writing_acceptance",
        "scenes": scenes,
    }
    path = tmp_path / "review.json"
    path.write_text(json.dumps(review, ensure_ascii=False), encoding="utf-8")
    return path


def test_evaluation_keeps_route_only_when_all_real_use_gates_pass(tmp_path):
    result = evaluate_trial(tmp_path, trial_files(tmp_path))
    assert result["keep_scene_spec_route"] is True
    assert result["b_not_worse_count"] == 4
    assert result["positive_effect_scene_count"] == 2
    assert "b_lower_average_edit_characters" not in result["gates"]
    assert "b_no_more_edit_time" not in result["gates"]


def test_new_hard_error_cannot_be_offset_by_other_metrics(tmp_path):
    result = evaluate_trial(tmp_path, trial_files(tmp_path, b_hard=True))
    assert result["gates"]["b_no_new_hard_violations"] is False
    assert result["decision"] == "close_phase4r_no_further_batches"


def test_unmeasured_edit_cost_is_valid_and_not_coerced_to_zero(tmp_path):
    review_path = trial_files(
        tmp_path, legacy_edits=None, legacy_minutes=None,
        scene_spec_edits=None, scene_spec_minutes=None,
    )
    result = evaluate_trial(tmp_path, review_path)
    for arm in ARMS:
        assert result["summary"][arm]["edit_cost_status"] == "not_measured"
        assert result["summary"][arm]["measured_edit_samples"] == 0
        assert result["summary"][arm]["average_edit_characters"] is None
        assert result["summary"][arm]["average_edit_minutes"] is None
    assert result["keep_scene_spec_route"] is True


def test_partially_measured_edit_cost_only_uses_present_values(tmp_path):
    review_path = trial_files(tmp_path, scene_spec_edits=None, scene_spec_minutes=None)
    review = json.loads(review_path.read_text(encoding="utf-8"))
    review["scenes"][0]["candidates"][1]["edit_characters"] = 20
    review["scenes"][0]["candidates"][1]["edit_minutes"] = 4
    review_path.write_text(json.dumps(review), encoding="utf-8")
    result = evaluate_trial(tmp_path, review_path)
    scene_spec = result["summary"]["legacy_full_scene_spec"]
    assert scene_spec["edit_cost_status"] == "partially_measured"
    assert scene_spec["measured_edit_samples"] == 1
    assert scene_spec["average_edit_characters"] == 20
    assert scene_spec["average_edit_minutes"] == 4


def test_fully_measured_edit_cost_preserves_averages(tmp_path):
    result = evaluate_trial(tmp_path, trial_files(tmp_path))
    assert result["summary"]["legacy_full"]["edit_cost_status"] == "fully_measured"
    assert result["summary"]["legacy_full"]["measured_edit_samples"] == 4
    assert result["summary"]["legacy_full"]["average_edit_characters"] == 100
    assert result["summary"]["legacy_full"]["average_edit_minutes"] == 10


def test_required_quality_field_still_cannot_be_null(tmp_path):
    review_path = trial_files(tmp_path, legacy_edits=None, legacy_minutes=None)
    review = json.loads(review_path.read_text(encoding="utf-8"))
    review["scenes"][0]["candidates"][0]["fact_error"] = None
    review_path.write_text(json.dumps(review), encoding="utf-8")
    try:
        evaluate_trial(tmp_path, review_path)
    except ValueError as exc:
        assert "incomplete candidate fields" in str(exc)
    else:
        raise AssertionError("null required quality field was accepted")


def test_template_generation_does_not_require_mapping_or_candidate_prose(tmp_path):
    manifest = {
        "queries": [{
            "trial_index": index,
            "candidates": [{"candidate_id": "candidate_01"}, {"candidate_id": "candidate_02"}],
        } for index in range(1, 5)]
    }
    (tmp_path / "run_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    template = build_review_template(tmp_path)
    assert len(template["scenes"]) == 4
    assert "may remain null" in template["instructions"]
    assert template["scenes"][0]["candidates"][0]["edit_characters"] is None


@pytest.mark.parametrize(("field", "gate"), (
    ("hard_violation", "b_no_new_hard_violations"),
    ("relationship_violation", "b_no_new_relationship_violations"),
    ("fact_error", "b_no_new_fact_errors"),
    ("continuity_error", "b_no_increase_in_continuity_event_order_or_boundary_errors"),
    ("event_order_error", "b_no_increase_in_continuity_event_order_or_boundary_errors"),
    ("crossed_stop_boundary", "b_no_increase_in_continuity_event_order_or_boundary_errors"),
))
def test_each_new_scene_spec_error_category_blocks_the_matching_gate(tmp_path, field, gate):
    review_path = trial_files(tmp_path)
    review = json.loads(review_path.read_text(encoding="utf-8"))
    review["scenes"][0]["candidates"][1][field] = True
    review_path.write_text(json.dumps(review), encoding="utf-8")
    result = evaluate_trial(tmp_path, review_path)
    assert result["gates"][gate] is False
    assert result["keep_scene_spec_route"] is False


def test_lower_scene_spec_goal_completion_blocks_route(tmp_path):
    review_path = trial_files(tmp_path)
    review = json.loads(review_path.read_text(encoding="utf-8"))
    review["scenes"][0]["candidates"][1]["goal_complete"] = False
    review_path.write_text(json.dumps(review), encoding="utf-8")
    result = evaluate_trial(tmp_path, review_path)
    assert result["gates"]["b_goal_completion_not_lower"] is False
    assert result["keep_scene_spec_route"] is False


def test_evaluator_does_not_call_llm(tmp_path, monkeypatch):
    monkeypatch.setattr(
        final_trial, "get_llm_client",
        lambda: (_ for _ in ()).throw(AssertionError("LLM client must not be constructed")),
    )
    result = evaluate_trial(
        tmp_path,
        trial_files(
            tmp_path, legacy_edits=None, legacy_minutes=None,
            scene_spec_edits=None, scene_spec_minutes=None,
        ),
    )
    assert result["scene_count"] == 4
