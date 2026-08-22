from tests.benchmarks.benchmark_phase8_style_observability import build_report


def test_phase8_report_contract_and_scope():
    report = build_report()

    assert report["offline_only"] is True
    assert report["llm_calls"] == 0
    assert report["production_behavior_changed"] is False
    assert report["phase_status"] == {
        "phase4": "paused_by_generation_evaluation_infrastructure",
        "phase4_architecture_failure": False,
        "production_writer_input": "legacy_full",
        "phase5": "paused",
        "phase6": "paused",
    }
    assert len(report["metrics"]["chapters"]) == 18
    assert len(report["metrics"]["subsections"]) > 18
    assert report["validation"]["unit"] == {"passed": 189, "failed": 0}
    assert report["validation"]["integration"] == {"passed": 8, "failed": 0}
    assert report["validation"]["quality"] == {"passed": 63, "failed": 0}
    assert report["validation"]["compileall"] == "passed"


def test_phase8_all_units_are_traceable_and_metrics_are_complete():
    report = build_report()
    required = {
        "dialogue_ratio", "sentence_length", "paragraph_length", "sentence_starts",
        "mechanical_start_ratio", "sensory_terms_per_1k",
        "psychological_exposition_per_1k", "consecutive_short_sentence_runs",
        "consecutive_isomorphic_sentence_runs",
    }
    for unit in report["metrics"]["subsections"]:
        assert unit["source_id"].startswith("golden:S")
        assert len(unit["text_hash"]) == 64
        assert required <= unit["metrics"].keys()


def test_emotional_layering_is_not_automated_and_four_controls_remain():
    report = build_report()
    mapping = report["metrics"]["style_control_mapping"]
    assert set(mapping) == {"emotion_intensity", "dialogue_ratio", "sentence_preference", "sensory_density"}
    assert mapping["emotion_intensity"]["mapping"] == "human_or_llm_required"
    issue = next(item for item in report["metrics"]["known_style_issues"] if item["id"] == "insufficient_emotional_layering")
    assert issue["deterministic_observability"] == []
    assert issue["automation_status"] == "not_automated"
