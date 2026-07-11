"""支线故事 API。"""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from .. import subplot_manager as sm

router = APIRouter(prefix="/api/subplots", tags=["subplots"])


class SubplotBody(BaseModel):
    task_id: str = ""
    name: str = ""
    description: str = ""
    type: str = "character_arc"
    protagonist_id: str = ""
    antagonist_id: str = ""
    volume_start: int = 1
    volume_end: int = 5
    elements: list[dict] = []
    status: str = "planned"
    priority: int = 5
    related_subplots: list[str] = []
    pov: str = "protagonist"


@router.get("")
def list_subplots(task_id: str = Query("")):
    items = sm.list_subplots(task_id)
    return {"subplots": items, "total": len(items)}


@router.get("/heat-map")
def heat_map(task_id: str = Query(...), total_chapters: int = Query(50)):
    subplots = sm.list_subplots(task_id)
    heat = sm.calculate_heat_map(subplots, total_chapters)
    heat_data = [{"chapter": ch, "count": cnt, "level": sm.get_heat_level(cnt)}
                 for ch, cnt in sorted(heat.items())]
    return {"heat_map": heat_data}


@router.get("/{subplot_id}")
def get_subplot(subplot_id: str):
    sp = sm.get_subplot(subplot_id)
    if not sp:
        raise HTTPException(status_code=404, detail="支线不存在")
    return sp


@router.post("")
def create_subplot(body: SubplotBody):
    return sm.create_subplot(body.model_dump())


@router.put("/{subplot_id}")
def update_subplot(subplot_id: str, body: SubplotBody):
    sp = sm.update_subplot(subplot_id, body.model_dump(exclude_unset=True))
    if not sp:
        raise HTTPException(status_code=404, detail="支线不存在")
    return sp


@router.delete("/{subplot_id}")
def delete_subplot(subplot_id: str):
    ok = sm.delete_subplot(subplot_id)
    if not ok:
        raise HTTPException(status_code=404, detail="支线不存在")
    return {"status": "deleted"}


@router.post("/auto-bind")
def auto_bind(task_id: str = Query(...), total_chapters: int = Query(50)):
    updated = sm.auto_bind_subplot_elements(task_id, total_chapters)
    return {"updated": len(updated), "subplots": updated}
