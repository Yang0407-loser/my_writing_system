"""大纲管理 API —— 按 task_id 隔离，Redis 为唯一数据源。"""

import json
import time
import uuid
import datetime
import logging

import redis
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

from ..config import settings

router = APIRouter(tags=["outline"])


class OutlineNodesBody(BaseModel):
    nodes: list[dict] = []


class DeleteNodeBody(BaseModel):
    node: dict
    parent_id: str = ""
    index: int = 0


class DraftBody(BaseModel):
    draft: str = ""


class OutlineBudgetAdviceBody(BaseModel):
    nodes: list[dict] = Field(default_factory=list)
    style_profile: dict = Field(default_factory=dict)
    character_names: list[str] = Field(default_factory=list)
    chapter_budget: int | None = None
    style_brief: str = ""


# ═══ 工具函数 ═══════════════════════════════════════════════════════

def _get_redis():
    return redis.Redis.from_url(settings.REDIS_BACKEND_URL)


def _undo_key(task_id: str) -> str:
    return f"undo:outline:{task_id}"


def _versions_key(task_id: str) -> str:
    return f"{task_id}:outline_versions"


# ── 树 ↔ 扁平 转换 ──

def _build_tree_from_nodes(nodes: list[dict]) -> list[dict]:
    """从扁平 node 列表（id/parent_id）重建嵌套树。"""
    node_map: dict[str, dict] = {}
    for n in nodes:
        nc = dict(n)
        nc.setdefault("children", [])
        node_map[nc["id"]] = nc
    roots: list[dict] = []
    for n in node_map.values():
        pid = n.get("parent_id", "")
        if pid and pid in node_map:
            node_map[pid]["children"].append(n)
        else:
            roots.append(n)
    return roots


def _flatten_tree(tree: list[dict], parent_id: str = "") -> list[dict]:
    """树 → 扁平 node 列表，供 API 兼容返回。"""
    result: list[dict] = []
    for i, node in enumerate(tree):
        n = {
            "id": node.get("id", ""),
            "parent_id": parent_id,
            "title": node.get("title", ""),
            "description": node.get("description", ""),
            "key_points": node.get("key_points", []),
            "target_words": node.get("target_words", 2000),
            "locked": node.get("locked", False),
            "status": node.get("status", "draft"),
            "injections": node.get("injections", {}),
            "sort_order": i,
        }
        result.append(n)
        children = node.get("children", [])
        if children:
            result.extend(_flatten_tree(children, n["id"]))
    return result


def _tree_to_outline_v2(tree: list[dict]) -> list[dict]:
    """前端编辑器树 → coordinator 的 outline_v2 格式 [{section, subsections}]。"""
    result = []
    for i, node in enumerate(tree):
        children = node.get("children", [])
        subsections = []
        for j, child in enumerate(children):
            subsections.append({
                "subsection": j + 1,
                "title": child.get("title", ""),
                "description": child.get("description", ""),
                "key_points": child.get("key_points", []),
                "target_words": child.get("target_words", 2000),
                "status": child.get("status", "draft"),
            })
        result.append({
            "section": i + 1,
            "title": node.get("title", ""),
            "key_points": node.get("key_points", []),
            "subsections": subsections,
        })
    return result


def _tree_to_budget_outline(tree: list[dict]) -> list[dict]:
    """Build advisor input while preserving editor node IDs as provenance."""
    result = []
    for section_index, node in enumerate(tree, 1):
        subsections = []
        for subsection_index, child in enumerate(node.get("children", []), 1):
            subsections.append({
                "subsection": subsection_index,
                "source_id": str(child.get("id") or f"outline:{section_index}:{subsection_index}"),
                "title": child.get("title", ""),
                "description": child.get("description", ""),
                "key_points": child.get("key_points", []),
                "target_words": child.get("target_words", 2000),
            })
        result.append({
            "section": section_index,
            "source_id": str(node.get("id") or f"outline:{section_index}"),
            "title": node.get("title", ""),
            "subsections": subsections,
        })
    return result


def _outline_v2_to_tree(outline: list[dict]) -> list[dict]:
    """Coordinator outline_v2 格式 → 前端编辑器树格式（fallback 用）。"""
    result = []
    for sec in outline:
        node = {
            "id": str(uuid.uuid4()),
            "title": sec.get("title", ""),
            "key_points": sec.get("key_points", []),
            "collapsed": False,
            "children": [],
        }
        for sub in sec.get("subsections", []):
            node["children"].append({
                "id": str(uuid.uuid4()),
                "title": sub.get("title", ""),
                "description": sub.get("description", ""),
                "key_points": sub.get("key_points", []),
                "target_words": sub.get("target_words", 2000),
                "status": sub.get("status", "queued"),
            })
        result.append(node)
    return result


# ── 版本快照 (Redis list, 最多 5 个) ──

def _push_version(r, task_id: str, tree: list[dict]):
    entry = json.dumps({
        "id": str(int(time.time() * 1000)),
        "created_at": datetime.datetime.now().isoformat(),
        "nodes_json": tree,
    }, ensure_ascii=False)
    key = _versions_key(task_id)
    r.lpush(key, entry)
    r.ltrim(key, 0, 4)


def _get_versions(r, task_id: str) -> list[dict]:
    key = _versions_key(task_id)
    items = r.lrange(key, 0, -1)
    result = []
    for item in items:
        raw = item.decode("utf-8") if isinstance(item, bytes) else item
        entry = json.loads(raw)
        nodes = entry.get("nodes_json", [])
        if isinstance(nodes, str):
            nodes = json.loads(nodes)
        result.append({
            "id": entry["id"],
            "created_at": entry["created_at"],
            "node_count": len(nodes) if isinstance(nodes, list) else 0,
        })
    return result


def _restore_version(r, task_id: str, version_id) -> list[dict] | None:
    key = _versions_key(task_id)
    items = r.lrange(key, 0, -1)
    idx = None
    for i, item in enumerate(items):
        raw = item.decode("utf-8") if isinstance(item, bytes) else item
        entry = json.loads(raw)
        if str(entry.get("id")) == str(version_id):
            idx = i
            break
    if idx is None:
        return None
    raw = items[idx].decode("utf-8") if isinstance(items[idx], bytes) else items[idx]
    entry = json.loads(raw)
    tree = entry.get("nodes_json", [])
    return json.loads(tree) if isinstance(tree, str) else tree


# ═══ 撤销删除 (Redis) ═══

@router.post("/{task_id}/outline/delete-node")
def stage_delete_node(task_id: str, body: DeleteNodeBody):
    try:
        r = _get_redis()
        key = _undo_key(task_id)
        entry = json.dumps({
            "node": body.node,
            "parent_id": body.parent_id,
            "index": body.index,
            "deleted_at": time.time(),
        }, ensure_ascii=False)
        r.lpush(key, entry)
        r.expire(key, 86400)
        return {"status": "staged", "undo_count": r.llen(key)}
    except Exception:
        logger.warning(f"Redis undo 操作失败 for task {task_id}", exc_info=True)
        return {"status": "staged_local", "undo_count": 0}


@router.post("/{task_id}/outline/undo-delete")
def undo_delete_node(task_id: str):
    try:
        r = _get_redis()
        key = _undo_key(task_id)
        entry = r.lpop(key)
        if not entry:
            raise HTTPException(status_code=404, detail="没有可撤销的删除")
        return {"entry": json.loads(entry)}
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=503, detail="Redis 不可用，无法撤销")


@router.get("/{task_id}/outline/undo-count")
def get_undo_count(task_id: str):
    try:
        r = _get_redis()
        key = _undo_key(task_id)
        return {"count": r.llen(key)}
    except Exception:
        logger.warning(f"Redis undo-count 查询失败 for task {task_id}", exc_info=True)
        return {"count": 0}


# ═══ 大纲 CRUD ═══

@router.post("/{task_id}/outline/budget-advice")
def get_outline_budget_advice(task_id: str, body: OutlineBudgetAdviceBody):
    """Return deterministic advice without reading or mutating task storage."""
    del task_id  # The route stays task-scoped; analysis uses only the submitted draft.
    from ..writing.outline_budget_advisor import OutlineBudgetAdvisor

    tree = _build_tree_from_nodes([dict(node) for node in body.nodes])
    outline = _tree_to_budget_outline(tree)
    result = OutlineBudgetAdvisor().advise_outline(
        outline=outline,
        style_profile=body.style_profile,
        character_names=body.character_names,
        chapter_budget=body.chapter_budget,
        style_brief=body.style_brief,
    )
    return result.model_dump(mode="json")


@router.get("/{task_id}/outline")
def get_outline(task_id: str):
    from ..dependencies import bb

    # 优先读 Redis outline_tree
    tree_data = bb.get(task_id, "outline_tree")
    if tree_data and isinstance(tree_data, list) and len(tree_data) > 0:
        return {"nodes": _flatten_tree(tree_data), "tree": tree_data}

    # Fallback 1: 从 flat outline 反向重建树
    outline_data = bb.get(task_id, "outline")
    if outline_data and isinstance(outline_data, list) and len(outline_data) > 0:
        tree = _outline_v2_to_tree(outline_data)
        return {"nodes": _flatten_tree(tree), "tree": tree}

    # Fallback 2: tasks.db 历史记录（Redis 崩了也能恢复）
    try:
        from ..task_store import TaskStore
        from ..config import settings as _settings
        ts = TaskStore(_settings.TASK_DB_PATH)
        record = ts.get(task_id)
        if record:
            saved_outline = record.get("outline_json") or []
            if saved_outline and isinstance(saved_outline, list) and len(saved_outline) > 0:
                tree = _outline_v2_to_tree(saved_outline)
                return {"nodes": _flatten_tree(tree), "tree": tree}
    except Exception:
        logger.warning(f"历史大纲加载失败 for task {task_id}", exc_info=True)

    return {"nodes": [], "tree": []}


@router.post("/{task_id}/outline")
def save_outline(task_id: str, body: OutlineNodesBody):
    from ..dependencies import bb

    nodes = body.nodes

    # 确保每个节点有 id
    import uuid as _uuid
    for node in nodes:
        if not node.get("id"):
            node["id"] = str(_uuid.uuid4())

    # 树重建 + 转 outline_v2
    tree = _build_tree_from_nodes(nodes)
    flat = _tree_to_outline_v2(tree)

    # 写 Redis
    bb.set(task_id, "outline_tree", tree)
    bb.set(task_id, "outline", flat)

    # 版本快照
    try:
        r = _get_redis()
        _push_version(r, task_id, tree)
    except Exception:
        logger.warning(f"大纲版本快照保存失败 for task {task_id}", exc_info=True)

    # 更新 checkpoint（若 coordinator 正在运行）
    try:
        checkpoint = bb.load_checkpoint(task_id)
        if checkpoint:
            checkpoint["outline_v2"] = flat
            checkpoint["outline_tree"] = tree
            bb.save_checkpoint(task_id, checkpoint)
    except Exception:
        logger.warning(f"大纲检查点更新失败 for task {task_id}", exc_info=True)

    # 唤醒 Writer（若等待队列更新）
    try:
        bb.push_notification(task_id, "outline_updated")
    except Exception:
        logger.warning(f"Writer 唤醒通知失败 for task {task_id}", exc_info=True)

    return {"nodes": _flatten_tree(tree)}


@router.get("/{task_id}/outline/versions")
def get_outline_versions(task_id: str):
    r = _get_redis()
    versions = _get_versions(r, task_id)
    return {"versions": versions}


@router.post("/{task_id}/outline/restore/{version_id}")
def restore_outline(task_id: str, version_id: int):
    from ..dependencies import bb

    r = _get_redis()
    tree = _restore_version(r, task_id, version_id)
    if not tree:
        raise HTTPException(status_code=404, detail="版本不存在")

    flat = _tree_to_outline_v2(tree)
    bb.set(task_id, "outline_tree", tree)
    bb.set(task_id, "outline", flat)

    checkpoint = bb.load_checkpoint(task_id)
    if checkpoint:
        checkpoint["outline_v2"] = flat
        checkpoint["outline_tree"] = tree
        bb.save_checkpoint(task_id, checkpoint)

    return {"nodes": _flatten_tree(tree)}


# ═══ 草稿 ══════════════════════════════════════════════════════════

@router.put("/{task_id}/draft")
def save_task_draft(task_id: str, body: DraftBody):
    from ..dependencies import bb
    bb.set(task_id, "draft_backup", body.draft)
    return {"status": "saved"}


@router.get("/{task_id}/draft")
def get_task_draft(task_id: str):
    from ..dependencies import bb
    return {"draft": bb.get(task_id, "draft_backup") or ""}
