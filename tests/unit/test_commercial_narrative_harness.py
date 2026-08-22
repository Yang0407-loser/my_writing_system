from app.writing.commercial_narrative_harness import (
    HARNESS_VERSION,
    classify_scene,
    compile_commercial_narrative_harness,
    harness_hash,
    render_commercial_narrative_harness,
)


def test_static_dialogue_scene_gets_dialogue_conflict_contract():
    harness = compile_commercial_narrative_harness(
        scene_text="主角拿出证据质问掌柜，双方谈判后必须作出回答。",
        required_events=[],
    )

    assert harness.scene_mode == "dialogue_conflict"
    assert any("筹码" in rule for rule in harness.scene_contract)
    assert any("不靠突然打斗" in rule for rule in harness.scene_contract)


def test_action_scene_gets_action_pressure_contract():
    result = classify_scene("追兵围住出口，主角夺路冲出后继续逃。")

    assert result.mode == "action_pressure"
    assert set(result.action_markers) >= {"追", "围", "夺", "冲", "逃"}


def test_ambiguous_scene_falls_back_to_general():
    assert classify_scene("他一边追，一边质问。").mode == "general"


def test_compiler_tracks_source_hashes_but_does_not_duplicate_event_text():
    secret_event = "父亲其实是失踪的国王"
    harness = compile_commercial_narrative_harness(
        scene_text="主角发现戒指上的徽记。",
        required_events=[
            {"source_id": "outline:S1.1:key_point:1", "text_hash": "abc", "text": secret_event}
        ],
    )
    rendered = render_commercial_narrative_harness(harness)

    assert harness.version == HARNESS_VERSION
    assert harness.required_event_count == 1
    assert harness.source_refs == (
        {"source_id": "outline:S1.1:key_point:1", "text_hash": "abc"},
    )
    assert secret_event not in rendered
    assert "不按清单顺序逐项交差" in rendered


def test_harness_hash_is_stable_and_changes_with_scene_strategy():
    first = compile_commercial_narrative_harness(scene_text="追兵冲来")
    same = compile_commercial_narrative_harness(scene_text="追兵冲来")
    different = compile_commercial_narrative_harness(scene_text="双方谈判质问")

    assert harness_hash(first) == harness_hash(same)
    assert harness_hash(first) != harness_hash(different)
