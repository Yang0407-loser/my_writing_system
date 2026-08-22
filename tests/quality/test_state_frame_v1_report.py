import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / "reports" / "state-frame-v1-production-quality-baseline.json"


def _report():
    return json.loads(REPORT.read_text(encoding="utf-8"))


def test_report_is_read_only_private_text_free_and_traceable():
    report = _report()
    assert report["writer_calls"] == 0
    assert report["llm_calls"] == 0
    assert report["database_writes"] == 0
    assert report["production_effect"] is False
    assert report["contains_story_text"] is False
    assert report["summary"]["source_traceability_rate"] == 1.0
    assert all(not scene["contains_story_text"] for scene in report["scenes"])
    rendered = json.dumps(report, ensure_ascii=False).lower()
    for forbidden in ("完整正文", "prompt", "messages", "api key"):
        assert forbidden not in rendered


def test_report_keeps_three_dimensions_separate_and_does_not_fake_truth():
    report = _report()
    assert set(report["quality_baseline"]) == {
        "handover_continuity",
        "character_state_transition",
        "foreshadow_health",
    }
    assert all(
        value == "baseline_only" for value in report["quality_baseline"].values()
    )
    assert report["can_recommend_writer_shadow_injection"] is False
    assert report["recommendation"].startswith("do_not_inject")
