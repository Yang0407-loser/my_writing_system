"""项目 CRUD API。"""

import json
import time

import redis
from fastapi import APIRouter, Body, HTTPException, Query
from pydantic import BaseModel

from .. import project_store as ps
from ..config import settings

router = APIRouter(prefix="/api/projects", tags=["projects"])


class ProjectBody(BaseModel):
    name: str = "未命名项目"


class WorldSettingBody(BaseModel):
    text: str = ""


class OutlineNodesBody(BaseModel):
    nodes: list[dict] = []


class DeleteNodeBody(BaseModel):
    node: dict
    parent_id: str = ""
    index: int = 0


@router.post("")
def create_project(body: ProjectBody):
    return ps.create_project(body.name)


@router.get("")
def list_projects():
    return {"projects": ps.list_projects()}


@router.get("/{pid}")
def get_project(pid: str):
    p = ps.get_project(pid)
    if not p:
        raise HTTPException(status_code=404, detail="项目不存在")
    return p


@router.put("/{pid}")
def update_project(pid: str, body: ProjectBody):
    p = ps.update_project(pid, body.model_dump())
    if not p:
        raise HTTPException(status_code=404, detail="项目不存在")
    return p


@router.delete("/{pid}")
def delete_project(pid: str):
    if not ps.delete_project(pid):
        raise HTTPException(status_code=404, detail="项目不存在")
    return {"status": "deleted"}


@router.put("/{pid}/world-setting")
def update_world_setting(pid: str, body: WorldSettingBody):
    p = ps.save_world_setting(pid, body.text)
    if not p:
        raise HTTPException(status_code=404, detail="项目不存在")
    return p


# ═══ 大纲节点删除撤销 (Redis) ═══

def _undo_key(pid: str) -> str:
    return f"undo:outline:{pid}"


def _get_redis():
    return redis.Redis.from_url(settings.REDIS_BACKEND_URL)


@router.post("/{pid}/outline/delete-node")
def stage_delete_node(pid: str, body: DeleteNodeBody):
    """删除节点前暂存到 Redis，支持撤销"""
    node = body.node
    parent_id = body.parent_id
    node_index = body.index

    try:
        r = _get_redis()
        key = _undo_key(pid)
        entry = json.dumps({
            "node": node,
            "parent_id": parent_id,
            "index": node_index,
            "deleted_at": time.time(),
        }, ensure_ascii=False)
        r.lpush(key, entry)
        r.expire(key, 3600)
        return {"status": "staged", "undo_count": r.llen(key)}
    except Exception:
        return {"status": "staged_local", "undo_count": 0}


@router.post("/{pid}/outline/undo-delete")
def undo_delete_node(pid: str):
    """撤销最近一次删除，返回被恢复的节点信息"""
    try:
        r = _get_redis()
        key = _undo_key(pid)
        entry = r.lpop(key)
        if not entry:
            raise HTTPException(status_code=404, detail="没有可撤销的删除")
        return {"entry": json.loads(entry)}
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=503, detail="Redis 不可用，无法撤销")


@router.get("/{pid}/outline/undo-count")
def get_undo_count(pid: str):
    try:
        r = _get_redis()
        key = _undo_key(pid)
        return {"count": r.llen(key)}
    except Exception:
        return {"count": 0}


@router.get("/{pid}/outline")
def get_outline(pid: str):
    nodes = ps.get_outline_nodes(pid)
    return {"nodes": nodes, "tree": ps.get_outline_tree(pid)}


@router.post("/{pid}/outline")
def save_outline(pid: str, body: OutlineNodesBody):
    if not ps.get_project(pid):
        raise HTTPException(status_code=404, detail="项目不存在")
    return {"nodes": ps.save_outline_nodes(pid, body.nodes)}


@router.get("/{pid}/outline/versions")
def get_outline_versions(pid: str):
    return {"versions": ps.get_outline_versions(pid)}


@router.post("/{pid}/outline/restore/{version_id}")
def restore_outline(pid: str, version_id: int):
    nodes = ps.restore_outline_version(pid, version_id)
    if not nodes:
        raise HTTPException(status_code=404, detail="版本不存在")
    return {"nodes": nodes}


class DraftBody(BaseModel):
    draft: str = ""


@router.put("/{pid}/draft")
def save_project_draft(pid: str, body: DraftBody):
    if not ps.get_project(pid):
        raise HTTPException(status_code=404, detail="项目不存在")
    ok = ps.save_draft(pid, body.draft)
    return {"status": "saved" if ok else "no_change"}


@router.get("/{pid}/draft")
def get_project_draft(pid: str):
    return {"draft": ps.get_draft(pid)}
