"""经历事件线 API。"""

from fastapi import APIRouter, Query
from pydantic import BaseModel
from .. import experience_timeline as et

router = APIRouter(prefix="/api/experience", tags=["experience"])


@router.get("")
def list_events(task_id: str = Query("")):
    events = et.list_events(task_id)
    return {"events": events, "total": len(events)}


@router.get("/context")
def get_context(task_id: str = Query(...), chapter: int = Query(1)):
    ctx = et.build_experience_context(task_id, chapter)
    return {"context": ctx}


@router.get("/{event_id}")
def get_event(event_id: str):
    ev = et.get_event(event_id)
    if not ev:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="事件不存在")
    return ev
