"""物品背包 API。"""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from .. import item_manager as im

router = APIRouter(prefix="/api/items", tags=["items"])


class ItemBody(BaseModel):
    task_id: str = ""
    name: str = ""
    description: str = ""
    type: str = "material"
    rarity: str = "common"
    abilities: list[str] = []
    origin_chapter: int = 0
    current_owner: str = ""
    status: str = "active"


class TransactionBody(BaseModel):
    item_id: str
    from_owner: str = ""
    to_owner: str = ""
    chapter: int = 0
    description: str = ""


@router.get("")
def list_items(task_id: str = Query(""), owner: str = Query("")):
    if owner:
        items = im.get_character_inventory(owner)
        return {"items": items, "total": len(items)}
    # 暂不支持全量查询，返回空
    return {"items": [], "total": 0}


@router.get("/inventory/{character_id}")
def character_inventory(character_id: str):
    items = im.get_character_inventory(character_id)
    return {"character_id": character_id, "items": items, "total": len(items)}


@router.post("")
def create_item(body: ItemBody):
    return im.create_item(body.model_dump())


@router.put("/{item_id}")
def update_item(item_id: str, body: ItemBody):
    item = im.update_item(item_id, body.model_dump(exclude_unset=True))
    if not item:
        raise HTTPException(status_code=404, detail="物品不存在")
    return item


@router.post("/transactions")
def record_transaction(body: TransactionBody):
    return im.record_transaction(
        body.item_id, body.from_owner, body.to_owner,
        body.chapter, body.description
    )
