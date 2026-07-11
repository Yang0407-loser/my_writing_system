"""角色关系 API。"""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from .. import character_relation_store as crs

router = APIRouter(prefix="/api/character-relations", tags=["character-relations"])


class RelationBody(BaseModel):
    task_id: str = ""
    character_a: str = ""
    character_b: str = ""
    relation_type: str = "盟友"
    direction: str = "positive"
    intensity: int = 5
    stages: list[dict] = []
    current_stage: int = 0
    source: str = "manual"
    source_section: int = 0
    description: str = ""


@router.get("")
def list_relations(task_id: str = Query("")):
    items = crs.list_relations(task_id)
    return {"relations": items, "total": len(items)}


@router.get("/presets")
def get_presets():
    return {
        "relation_types": crs.PRESET_RELATION_TYPES,
        "directions": [{"value": v, "label": l} for v, l in crs.DIRECTION_LABELS.items()],
    }


@router.get("/{relation_id}")
def get_relation(relation_id: str):
    rel = crs.get_relation(relation_id)
    if not rel:
        raise HTTPException(status_code=404, detail="关系不存在")
    return rel


@router.post("")
def create_relation(body: RelationBody):
    return crs.create_relation(body.model_dump())


@router.put("/{relation_id}")
def update_relation(relation_id: str, body: RelationBody):
    rel = crs.update_relation(relation_id, body.model_dump(exclude_unset=True))
    if not rel:
        raise HTTPException(status_code=404, detail="关系不存在")
    return rel


@router.delete("/{relation_id}")
def delete_relation(relation_id: str):
    ok = crs.delete_relation(relation_id)
    if not ok:
        raise HTTPException(status_code=404, detail="关系不存在")
    return {"status": "deleted"}


@router.post("/{relation_id}/advance-stage")
def advance_stage(relation_id: str, stage_index: int = Query(...),
                  status: str = Query("done")):
    rel = crs.advance_stage(relation_id, stage_index, status)
    if not rel:
        raise HTTPException(status_code=404, detail="关系不存在或阶段索引无效")
    return rel
