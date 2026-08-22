from app.realization_policy import (
    REALIZATION_POLICY_VERSION,
    compile_realization_policy,
    render_realization_policy,
)


def test_policy_is_sparse_and_preserves_writer_freedom():
    policy = compile_realization_policy(
        {
            "emotion_intensity": 35,
            "dialogue_ratio": 0.2,
            "sentence_preference": "balanced",
            "sensory_density": "sparse",
        },
        beat={"intensity": 3, "character_focus": "林晚不愿承认自己的迟疑"},
    )

    assert policy.version == REALIZATION_POLICY_VERSION
    assert len(policy.prohibitions) <= 4
    assert policy.organizing_principle
    assert policy.freedom_permission

    rendered = render_realization_policy(policy)
    assert "叙述姿态" in rendered
    assert "可以" in rendered or "不必" in rendered
    assert "信息选择性" not in rendered
    assert "解释压力" not in rendered
    assert "段落计划" not in rendered
    assert "动作序列" not in rendered
    assert len(rendered) < 650


def test_policy_normalises_invalid_profile_values_without_exposing_targets():
    policy = compile_realization_policy(
        {
            "emotion_intensity": 999,
            "dialogue_ratio": -2,
            "sentence_preference": "unknown",
            "sensory_density": "unknown",
        }
    )

    assert policy.normalized_profile == {
        "emotion_intensity": 100,
        "dialogue_ratio": 0.0,
        "sentence_preference": "balanced",
        "sensory_density": "medium",
    }
    rendered = render_realization_policy(policy)
    assert "100/100" not in rendered
    assert "0%" not in rendered
    assert "逐项完成" not in rendered
