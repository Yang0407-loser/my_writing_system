"""角色库 API。"""

from fastapi import APIRouter, Header, HTTPException, Query
from ..dependencies import bb, char_store
from ..utils.llm_client import set_api_key

router = APIRouter(prefix="/api/characters", tags=["characters"])


@router.get("")
def list_characters(search: str = "", limit: int = 50, trait: str = ""):
    chars = char_store.list_all(search=search, limit=limit, trait_filter=trait)
    stats = char_store.stats()
    return {"characters": chars, "total": len(chars), "stats": stats}


@router.get("/stats")
def get_character_stats():
    return char_store.stats()


@router.get("/{char_id}")
def get_character(char_id: str):
    char = char_store.get(char_id)
    if not char:
        raise HTTPException(status_code=404, detail="角色不存在")
    return char


@router.post("")
def create_character(body: dict, on_conflict: str = "skip"):
    char = char_store.create(body)
    return char


@router.put("/{char_id}")
def update_character(char_id: str, body: dict):
    if not char_store.get(char_id):
        raise HTTPException(status_code=404, detail="角色不存在")
    char_store.update(char_id, body)
    return char_store.get(char_id)


@router.delete("/{char_id}")
def delete_character(char_id: str):
    if not char_store.delete(char_id):
        raise HTTPException(status_code=404, detail="角色不存在")
    return {"status": "deleted"}


@router.post("/extract")
def extract_characters(body: dict, x_api_key: str = Header("", alias="X-API-Key")):
    from ..agents.character_manager import CharacterManager
    if x_api_key: set_api_key(x_api_key)
    cm = CharacterManager()
    chars = cm.extract_characters(body.get("text", ""))
    return {"characters": chars}


@router.post("/batch-save")
def batch_save_characters(body: dict):
    characters = body.get("characters", [])
    on_conflict = body.get("on_conflict", "skip")
    saved, skipped, merged = [], [], []
    for c in characters:
        existing = char_store.get(c.get("id", ""))
        if existing:
            if on_conflict == "skip":
                skipped.append(c)
            elif on_conflict == "overwrite":
                char_store.update(c["id"], c)
                saved.append(c)
            elif on_conflict == "merge":
                merged.append(c)
        else:
            char_store.create(c)
            saved.append(c)
    return {"saved": saved, "skipped": skipped, "merged": merged}
