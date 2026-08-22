from app.style_evaluation import (
    STYLE_EVALUATION_VERSION,
    StyleDriftTracker,
    evaluate_style_drift,
)


TARGET = {
    "emotion_intensity": 35,
    "dialogue_ratio": 0.2,
    "sentence_preference": "balanced",
    "sensory_density": "sparse",
}


def test_evaluation_reports_target_deviation_with_text_evidence():
    text = (
        "第一步，他意识到这意味着事情已经结束。"
        "第二步，他明白自己终于完成了全部计划。"
        "第三步，他知道一切都得到了解决。"
        "这件事让他明白，从此所有问题都不会再回来。"
    )
    report = evaluate_style_drift(text, TARGET, section=2, subsection=1)

    assert report["schema_version"] == STYLE_EVALUATION_VERSION
    assert report["text_hash"]
    assert report["status"] in {"observe", "drift"}
    ids = {item["signal_id"] for item in report["content_signals"]}
    assert "explanation_pressure" in ids
    assert "summary_closure" in ids
    assert all(
        item["evidence"]
        for item in report["content_signals"]
        if item["status"] != "clear"
    )
    assert report["automatic_rewrite_recommended"] is False
    assert report["manual_dimensions"]["emotion_intensity"]["status"] == "human_or_llm_required"


def test_extreme_beat_marks_sentence_shift_as_contextual_not_silent_failure():
    text = "门响。她停下。灯灭了。脚步近了。她跑。"
    report = evaluate_style_drift(
        text,
        TARGET,
        section=3,
        subsection=2,
        beat={"intensity": 9, "character_focus": "逃离"},
    )

    sentence = next(
        item for item in report["target_deviations"]
        if item["control"] == "sentence_preference"
    )
    assert sentence["classification"] == "intentional_modulation"
    assert report["beat_context"]["intensity"] == 9


def test_tracker_adds_cross_subsection_drift_and_keeps_reports_serialisable():
    tracker = StyleDriftTracker(TARGET)
    first = tracker.observe(
        "她看着窗外的雨，掌心贴住温热的杯壁。过了一会儿，她才转身。",
        section=1,
        subsection=1,
    )
    second = tracker.observe(
        "“走吗？”“走。”“现在？”“现在。”“不等了？”“不等了。”",
        section=1,
        subsection=2,
    )

    assert first["history_comparison"]["baseline_count"] == 0
    assert second["history_comparison"]["baseline_count"] == 1
    assert tracker.reports == [first, second]
    assert second["history_comparison"]["metrics"]
