from pathlib import Path
import json

import pytest
from fastapi import HTTPException

from app.character_arc_contract import (
    HARD_ARC_TRANSITION,
    ORDINARY_PLOT_EVENT,
    SOFT_ARC_PROGRESS,
)
from app.routers.outline import (
    ArcProjectionConfirmBody,
    ArcProjectionPreviewBody,
    confirm_outline_arc_projection,
    preview_outline_arc_projection,
)
from app.writing.outline_event_contract import OutlineEventContractCompiler


ROOT = Path(__file__).resolve().parents[2]


class MemoryBoard:
    def __init__(self):
        self.data = {}
        self.set_calls = []

    def get(self, task_id, key):
        return self.data.get((task_id, key))

    def set(self, task_id, key, value):
        self.set_calls.append((task_id, key))
        self.data[(task_id, key)] = value


class SerializedMemoryBoard(MemoryBoard):
    def get(self, task_id, key):
        value = self.data.get((task_id, key))
        return json.dumps(value, ensure_ascii=False) if isinstance(value, dict) else value


def _confirmed_contract():
    sub = {
        "subsection": 1,
        "source_id": "sub-1",
        "title": "离开",
        "description": "",
        "key_points": ["林晚决定辞职"],
        "target_words": 600,
    }
    compiler = OutlineEventContractCompiler()
    proposed = compiler.compile_chapter(
        section=1,
        subsections=[sub],
        character_names=["林晚"],
        chapter_target_words=600,
    ).subsection_contracts[0].model_dump(mode="json")
    proposed["status"] = "confirmed"
    proposed["confirmation_requested"] = True
    for event in proposed["events"]:
        event["status"] = "confirmed"
        event["user_confirmed"] = True
    return compiler.confirm_submission(
        section=1,
        subsection=1,
        sub=sub,
        submitted=proposed,
    ).model_dump(mode="json")


def _body(contract=None):
    return {
        "nodes": [
            {
                "id": "chapter-1",
                "parent_id": "",
                "title": "第一章",
                "target_words": 600,
                "sort_order": 0,
            },
            {
                "id": "sub-1",
                "parent_id": "chapter-1",
                "title": "离开",
                "description": "",
                "key_points": ["林晚决定辞职"],
                "target_words": 600,
                "event_contract": contract,
                "sort_order": 0,
            },
        ],
        "characters": [{
            "id": "linwan",
            "name": "林晚",
            "personality": ["谨慎"],
            "motivation": "离开旧生活",
            "background": "在公司工作",
        }],
    }


def _candidate(result):
    return result["chapters"][0]["character_projections"][0]["candidates"][0]


def test_preview_is_read_only_and_only_exposes_traceable_projection(monkeypatch):
    board = MemoryBoard()
    monkeypatch.setattr("app.dependencies.bb", board)
    result = preview_outline_arc_projection(
        "task-1", ArcProjectionPreviewBody(**_body(_confirmed_contract()))
    )
    candidate = _candidate(result)
    assert board.set_calls == []
    assert result["production_effect"] is False
    assert candidate["event_type"] == "decision"
    assert candidate["outline_event_authoritative"] is True
    assert candidate["source_id"].startswith("sub-1:")
    assert candidate["event_text_hash"]


def test_soft_confirmation_writes_only_independent_review_artifact(monkeypatch):
    board = MemoryBoard()
    monkeypatch.setattr("app.dependencies.bb", board)
    payload = _body(_confirmed_contract())
    preview = preview_outline_arc_projection(
        "task-1", ArcProjectionPreviewBody(**payload)
    )
    candidate = _candidate(preview)
    result = confirm_outline_arc_projection(
        "task-1",
        ArcProjectionConfirmBody(
            **payload,
            projection_id=candidate["projection_id"],
            event_text_hash=candidate["event_text_hash"],
            classification=SOFT_ARC_PROGRESS,
            rationale="允许改写或延后的角色推进",
        ),
    )
    confirmed = _candidate(result)
    assert confirmed["status"] == "confirmed"
    assert confirmed["classification"] == SOFT_ARC_PROGRESS
    assert confirmed["requiredness"] == "soft"
    assert board.set_calls == [
        ("task-1", "character_arc_projection_review")
    ]
    assert ("task-1", "character_arcs") not in board.data
    assert ("task-1", "checkpoint") not in board.data
    assert result["production_effect"] is False


def test_complete_hard_confirmation_is_preserved(monkeypatch):
    board = MemoryBoard()
    monkeypatch.setattr("app.dependencies.bb", board)
    payload = _body(_confirmed_contract())
    candidate = _candidate(preview_outline_arc_projection(
        "task-1", ArcProjectionPreviewBody(**payload)
    ))
    result = confirm_outline_arc_projection(
        "task-1",
        ArcProjectionConfirmBody(
            **payload,
            projection_id=candidate["projection_id"],
            event_text_hash=candidate["event_text_hash"],
            classification=HARD_ARC_TRANSITION,
            before_state="忍耐",
            trigger="收到不合理调岗",
            after_state="主动离职",
            observable_evidence="提交辞职信",
            rationale="结束旧职业阶段",
        ),
    )
    confirmed = _candidate(result)
    assert confirmed["classification"] == HARD_ARC_TRANSITION
    assert confirmed["requiredness"] == "hard"
    assert confirmed["missing_hard_fields"] == []


def test_saved_review_round_trips_through_blackboard_json(monkeypatch):
    board = SerializedMemoryBoard()
    monkeypatch.setattr("app.dependencies.bb", board)
    payload = _body(_confirmed_contract())
    candidate = _candidate(preview_outline_arc_projection(
        "task-1", ArcProjectionPreviewBody(**payload)
    ))
    confirm_outline_arc_projection(
        "task-1",
        ArcProjectionConfirmBody(
            **payload,
            projection_id=candidate["projection_id"],
            event_text_hash=candidate["event_text_hash"],
            classification=SOFT_ARC_PROGRESS,
        ),
    )
    refreshed = _candidate(preview_outline_arc_projection(
        "task-1", ArcProjectionPreviewBody(**payload)
    ))
    assert refreshed["status"] == "confirmed"
    assert refreshed["user_confirmed"] is True


def test_unconfirmed_outline_event_and_stale_hash_are_rejected(monkeypatch):
    board = MemoryBoard()
    monkeypatch.setattr("app.dependencies.bb", board)
    proposed_payload = _body(None)
    proposed = _candidate(preview_outline_arc_projection(
        "task-1", ArcProjectionPreviewBody(**proposed_payload)
    ))
    with pytest.raises(HTTPException) as unconfirmed:
        confirm_outline_arc_projection(
            "task-1",
            ArcProjectionConfirmBody(
                **proposed_payload,
                projection_id=proposed["projection_id"],
                event_text_hash=proposed["event_text_hash"],
                classification=SOFT_ARC_PROGRESS,
            ),
        )
    assert unconfirmed.value.status_code == 409
    assert "outline_event_not_confirmed" in unconfirmed.value.detail

    confirmed_payload = _body(_confirmed_contract())
    confirmed = _candidate(preview_outline_arc_projection(
        "task-1", ArcProjectionPreviewBody(**confirmed_payload)
    ))
    with pytest.raises(HTTPException) as stale:
        confirm_outline_arc_projection(
            "task-1",
            ArcProjectionConfirmBody(
                **confirmed_payload,
                projection_id=confirmed["projection_id"],
                event_text_hash="0" * 64,
                classification=ORDINARY_PLOT_EVENT,
            ),
        )
    assert stale.value.status_code == 409
    assert stale.value.detail == "event_source_changed"


def test_thin_ui_has_explicit_review_and_no_production_wiring():
    api = (ROOT / "app/static/js/api.js").read_text(encoding="utf-8")
    source = (ROOT / "app/static/js/main.js").read_text(encoding="utf-8")
    template = (ROOT / "app/static/index.html").read_text(encoding="utf-8")
    assert "previewArcProjection" in api
    assert "confirmArcProjection" in api
    assert "./api.js?v=20260815b" in source
    assert "openArcProjectionReview" in source
    assert "['decision','state_transition']" in source
    assert "角色弧确认（小规模实验）" in template
    open_block = source.split(
        "async function openArcProjectionReview", 1
    )[1].split("function arcHardFieldsComplete", 1)[0]
    assert open_block.index("showArcProjection.value = true") < open_block.index(
        "API.previewArcProjection"
    )
    assert "正在根据已确认事件生成角色弧候选" in template
    assert "不会直接进入 Writer、角色弧规划或 EventGraph" in template
    assert "篇幅 → 事件结构" in template
    assert "WRITER_" not in source.split(
        "async function openArcProjectionReview", 1
    )[1].split("async function undoDeleteFn", 1)[0]
