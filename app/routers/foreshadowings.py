"""伏笔管理 API。"""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from .. import foreshadowing_store as fs

router = APIRouter(prefix="/api/foreshadowings", tags=["foreshadowings"])


class ForeshadowingBody(BaseModel):
    task_id: str = ""
    name: str = ""
    description: str = ""
    plant_chapter: int = 0
    resolve_chapter: int | None = None
    status: str = "pending"
    related_characters: list[str] = []
    related_items: list[str] = []
    importance: int = 5
    tags: list[str] = []


# ── CRUD ─────────────────────────────────────────────────────────

@router.get("")
def list_foreshadowings(task_id: str = Query("")):
    items = fs.list_foreshadowings(task_id=task_id)

    # 追溯补建：已有世界设定但无世界锚点 → 自动提取
    if task_id and not any("world_anchor" in (f.get("tags") or []) for f in items):
        try:
            from ..project_store import get_project
            from ..character_store import CharacterStore
            proj = get_project(task_id)
            if proj and proj.get("world_setting", "").strip():
                chars = CharacterStore().list_all(limit=200)
                n = fs.ensure_world_anchors(task_id, proj["world_setting"], chars)
                if n:
                    items = fs.list_foreshadowings(task_id=task_id)
        except Exception:
            pass

    return {"foreshadowings": items, "total": len(items)}


@router.get("/chapter/{chapter}")
def get_chapter_foreshadowings(task_id: str = Query(...), chapter: int = 0):
    """获取某章节相关的活跃伏笔及上下文字符串。"""
    active = fs.get_active_for_chapter(task_id, chapter)
    context = fs.build_foreshadowing_context(task_id, chapter)
    return {"foreshadowings": active, "context": context}


@router.get("/{fs_id}")
def get_foreshadowing(fs_id: str):
    item = fs.get_foreshadowing(fs_id)
    if not item:
        raise HTTPException(status_code=404, detail="伏笔不存在")
    return item


@router.post("")
def create_foreshadowing(body: ForeshadowingBody):
    return fs.create_foreshadowing(body.model_dump())


@router.put("/{fs_id}")
def update_foreshadowing(fs_id: str, body: ForeshadowingBody):
    item = fs.update_foreshadowing(fs_id, body.model_dump(exclude_unset=True))
    if not item:
        raise HTTPException(status_code=404, detail="伏笔不存在")
    return item


@router.delete("/{fs_id}")
def delete_foreshadowing(fs_id: str):
    ok = fs.delete_foreshadowing(fs_id)
    if not ok:
        raise HTTPException(status_code=404, detail="伏笔不存在")
    return {"status": "deleted"}
