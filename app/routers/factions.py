"""势力/阵营 API。"""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from .. import faction_store as fs

router = APIRouter(prefix="/api/factions", tags=["factions"])


class FactionBody(BaseModel):
    task_id: str = ""
    name: str
    type: str = "宗门"
    leader_name: str = ""
    description: str = ""
    goal: str = ""
    strength: int = 5
    territory: str = ""
    is_active: bool = True
    genre: str = ""
    tags: list[str] = []
    source: str = "user"


class MemberBody(BaseModel):
    faction_id: str
    character_name: str
    role: str = "弟子"
    joined_chapter: int = 0


class RelationBody(BaseModel):
    task_id: str = ""
    faction_a: str
    faction_b: str
    relation: str = "中立"
    description: str = ""
    established_chapter: int = 0


@router.post("")
def create_faction(body: FactionBody):
    return fs.create_faction(
        body.task_id, body.name,
        type=body.type, leader_name=body.leader_name,
        description=body.description, goal=body.goal,
        strength=body.strength, territory=body.territory,
        is_active=body.is_active, genre=body.genre,
        tags=body.tags, source=body.source,
    )


@router.get("")
def list_factions(task_id: str = Query(""), type: str = Query("")):
    return {"factions": fs.list_factions(task_id, type), "types": fs.FACTION_TYPES}


@router.get("/{fid}")
def get_faction(fid: str):
    f = fs.get_faction(fid)
    if not f:
        raise HTTPException(status_code=404, detail="势力不存在")
    f["members"] = fs.get_members(fid)
    return f


@router.put("/{fid}")
def update_faction(fid: str, body: FactionBody):
    f = fs.update_faction(fid, body.model_dump(exclude_unset=True))
    if not f:
        raise HTTPException(status_code=404, detail="势力不存在")
    return f


@router.delete("/{fid}")
def delete_faction(fid: str):
    if not fs.delete_faction(fid):
        raise HTTPException(status_code=404, detail="势力不存在")
    return {"status": "deleted"}


@router.post("/{fid}/members")
def add_member(fid: str, body: MemberBody):
    return fs.add_member(fid, body.character_name, body.role, body.joined_chapter)


@router.delete("/{fid}/members/{name}")
def remove_member(fid: str, name: str):
    if not fs.remove_member(fid, name):
        raise HTTPException(status_code=404, detail="成员不存在")
    return {"status": "removed"}


@router.post("/relations")
def set_relation(body: RelationBody):
    return fs.set_relation(
        body.task_id, body.faction_a, body.faction_b,
        body.relation, body.description, body.established_chapter,
    )


@router.get("/relations/list")
def get_relations(task_id: str = Query(""), faction: str = Query("")):
    return {"relations": fs.get_relations(task_id, faction), "relation_types": fs.RELATION_TYPES}


@router.delete("/relations/{rid}")
def remove_relation(rid: str):
    if not fs.delete_relation(rid):
        raise HTTPException(status_code=404, detail="关系不存在")
    return {"status": "deleted"}


@router.get("/context/build")
def get_faction_context(task_id: str = Query(...)):
    return {"context": fs.build_faction_context(task_id)}
