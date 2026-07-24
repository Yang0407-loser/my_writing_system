"""Read-only StateFrame V1 inspection endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..config import settings
from ..dependencies import bb
from ..writing.state_frame_persistence import (
    load_task_history_read_only,
    normalize_history,
    record_to_api_payload,
    select_record,
)
from ..writing.state_frame_service import build_state_frame_artifacts


router = APIRouter(prefix="/tasks", tags=["state-frames"])


@router.get("/{task_id}/state-frame/{section}/{subsection}")
def get_state_frame(task_id: str, section: int, subsection: int):
    if section < 1 or subsection < 1:
        raise HTTPException(status_code=400, detail="invalid_section_or_subsection")
    task_data = bb.get_all(task_id)
    checkpoint = bb.load_checkpoint(task_id) or {}
    for source, raw_history in (
        ("blackboard", task_data.get("state_frame_history_v1")),
        ("checkpoint", checkpoint.get("state_frame_history_v1")),
    ):
        if not raw_history:
            continue
        try:
            record = select_record(
                normalize_history(raw_history), section, subsection
            )
        except Exception:
            record = None
        if record is not None:
            return record_to_api_payload(record, source=source)

    task_history = load_task_history_read_only(settings.TASK_DB_PATH, task_id)
    if task_history is not None:
        record = select_record(task_history, section, subsection)
        if record is not None:
            return record_to_api_payload(record, source="task_history")

    if not task_data and not checkpoint:
        raise HTTPException(
            status_code=404, detail="historical_state_frame_unavailable"
        )

    relations = []
    foreshadows = []
    try:
        from ..character_relation_store import list_relations_read_only
        relations = list_relations_read_only(task_id)
    except Exception:
        relations = []
    try:
        from ..foreshadowing_store import list_foreshadowings_read_only
        foreshadows = list_foreshadowings_read_only(task_id)
    except Exception:
        foreshadows = []
    payload = build_state_frame_artifacts(
        task_id=task_id,
        section=section,
        subsection=subsection,
        task_data=task_data,
        checkpoint=checkpoint,
        relations=relations,
        foreshadows=foreshadows,
    )
    payload["source"] = "live_partial_reconstruction"
    payload["reconstructed"] = True
    return payload


@router.get("/{task_id}/state-frame/{section}/{subsection}/{artifact}")
def get_state_frame_artifact(
    task_id: str, section: int, subsection: int, artifact: str
):
    if artifact not in {"before", "after", "delta", "quality"}:
        raise HTTPException(status_code=404, detail="unknown_state_frame_artifact")
    payload = get_state_frame(task_id, section, subsection)
    return {
        "artifact": artifact,
        "data": payload[artifact],
        "production_effect": False,
    }
