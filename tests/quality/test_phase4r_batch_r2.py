import json
from pathlib import Path


REPORT = Path("reports/phase4r-batch-r2-scene-spec-shadow.json")


def load_report():
    return json.loads(REPORT.read_text(encoding="utf-8"))


def test_r2_is_shadow_only_and_does_not_generate():
    report = load_report()
    assert report["mode"] == "shadow_only"
    assert report["writer_generation_calls"] == 0
    assert report["llm_calls"] == 0
    assert report["runtime_evaluation_fields_used"] == []
    assert report["production_messages_hash_unchanged"] is True
    assert report["production_hash_baseline_count"] == 10


def test_four_risk_scenes_are_traceable_and_preserve_unknowns():
    report = load_report()
    assert [item["query_index"] for item in report["scenes"]] == [4, 6, 7, 8]
    assert report["summary"]["all_sources_traceable"] is True
    assert report["summary"]["all_unknowns_preserved"] is True
    assert all(item["traceability_rate"] == 1.0 for item in report["scenes"])
    assert all(not item["contains_story_text"] for item in report["scenes"])


def test_scene_spec_token_target_and_risk_guards():
    report = load_report()
    assert 200 <= report["summary"]["mean_estimated_tokens"] <= 500
    by_query = {item["query_index"]: item for item in report["scenes"]}
    assert "forbid:unverified_character_fact:unverified_character_fact" in by_query[4]["forbidden_inference_ids"]
    assert "forbid:character_absence:location_operation" in by_query[6]["forbidden_inference_ids"]
    assert "forbid:current_time_anchor:future_time_relation" in by_query[7]["forbidden_inference_ids"]
    assert "forbid:planned_event:future_event_status" in by_query[8]["forbidden_inference_ids"]
