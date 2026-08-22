from experiments.world_runtime_writer_canary.wr310_reviewer_real_task_side_by_side import (
    build_real_task_report,
    compare_character_context,
    compare_relation_context,
    render_relation_context,
)


def _wr_reviewer():
    return {
        "character_consistency_context": "character:lin-wan: status=employed",
        "relation_context": "（无关系数据：WR 本体无 relationship_state，legacy_only）",
        "subplot_context": "（无支线数据：WR 本体无 subplot 类型，legacy_only）",
        "coverage": {
            "relation_context_status": "legacy_only_not_projected",
            "subplot_context_status": "legacy_only_not_projected",
        },
    }


def _snapshot(**overrides):
    payload = {
        "character_count": 1,
        "legacy_character_context": "林晚（女，26岁）\n  动机: 记录生活切片",
        "character_arcs_status": "unavailable_redis_blackboard",
        "relation_count": 2,
        "legacy_relation_context": render_relation_context([
            {
                "character_a": "林晚",
                "character_b": "周野",
                "relation_type": "观察者→参与者",
                "direction": "positive",
                "intensity": 7,
                "stages": [],
                "description": "从拍照片到当店员",
            },
            {
                "character_a": "林晚",
                "character_b": "吴阿姨",
                "relation_type": "邻里",
                "direction": "positive",
                "intensity": 5,
                "stages": [],
                "description": "",
            },
        ]),
        "subplot_count": 0,
        "legacy_subplot_context": "",
        "handover_note_count": 1,
    }
    payload.update(overrides)
    return payload


def test_render_relation_context_matches_legacy_format():
    text = render_relation_context([
        {
            "character_a": "林晚",
            "character_b": "周野",
            "relation_type": "观察者→参与者",
            "direction": "positive",
            "intensity": 7,
            "stages": [
                {"stage": "观察", "status": "done"},
                {"stage": "参与", "status": "active"},
            ],
            "current_stage": 1,
            "description": "从拍照片到当店员",
        }
    ])
    assert "## 角色关系状态" in text
    assert "【林晚 ↔ 周野】观察者→参与者 | 正向 | 羁绊 7/10" in text
    assert "✓观察 → ●参与" in text
    assert "当前阶段: 参与" in text


def test_compare_character_and_relation_context():
    snapshot = _snapshot()
    wr_reviewer = _wr_reviewer()
    character = compare_character_context(snapshot, wr_reviewer)
    assert character["legacy_status"] == "real_task_snapshot"
    assert character["value_status"] == "different_shape"
    assert character["legacy_arcs_status"] == "unavailable_redis_blackboard"
    relation = compare_relation_context(snapshot, wr_reviewer)
    assert relation["data_loss_risk"] is True
    assert relation["wr_status"] == "legacy_only_placeholder"


def test_real_task_report_blocks_on_relation_data(monkeypatch):
    monkeypatch.setattr(
        "experiments.world_runtime_writer_canary.wr310_reviewer_real_task_side_by_side.project_reviewer_context",
        lambda committed: _wr_reviewer(),
    )
    report = build_real_task_report(_snapshot(), committed=None)
    assert report["recommendation"] == (
        "blocked_until_relationship_ontology_or_legacy_retention"
    )
    assert report["summary"]["data_loss_risk_fields"] == ["relation_context"]


def test_real_task_report_allows_when_relation_and_subplot_empty(monkeypatch):
    monkeypatch.setattr(
        "experiments.world_runtime_writer_canary.wr310_reviewer_real_task_side_by_side.project_reviewer_context",
        lambda committed: _wr_reviewer(),
    )
    report = build_real_task_report(
        _snapshot(relation_count=0, legacy_relation_context="", subplot_count=0),
        committed=None,
    )
    assert report["recommendation"] == "needs_character_rich_field_decision"
    assert report["summary"]["data_loss_risk_fields"] == []
