"""大纲管理 API —— 按 task_id 隔离，并持久化项目工作区。"""

import json
import time
import uuid
import datetime
import logging

import redis
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from ..config import settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["outline"])


class OutlineNodesBody(BaseModel):
    nodes: list[dict] = []


class DeleteNodeBody(BaseModel):
    node: dict
    parent_id: str = ""
    index: int = 0


class DraftBody(BaseModel):
    draft: str = ""


class OutlineEvaluationBody(BaseModel):
    nodes: list[dict] = Field(default_factory=list)
    from_section: int = Field(default=1, ge=1)
    to_section: int | None = Field(default=None, ge=1)


class OutlineBudgetAdviceBody(BaseModel):
    nodes: list[dict] = Field(default_factory=list)
    style_profile: dict = Field(default_factory=dict)
    character_names: list[str] = Field(default_factory=list)
    chapter_budget: int | None = None
    style_brief: str = ""


class ArcProjectionPreviewBody(BaseModel):
    nodes: list[dict] = Field(default_factory=list)
    characters: list[dict] = Field(default_factory=list)


class ArcProjectionConfirmBody(ArcProjectionPreviewBody):
    projection_id: str
    event_text_hash: str
    classification: str
    before_state: str = ""
    trigger: str = ""
    after_state: str = ""
    observable_evidence: str = ""
    rationale: str = ""


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
        if node.get("event_contract"):
            n["event_contract"] = node["event_contract"]
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
            subsection = {
                "subsection": j + 1,
                "title": child.get("title", ""),
                "description": child.get("description", ""),
                "key_points": child.get("key_points", []),
                "target_words": child.get("target_words", 2000),
                "status": child.get("status", "draft"),
            }
            if child.get("event_contract"):
                subsection["event_contract"] = child["event_contract"]
            subsections.append(subsection)
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
            subsection = {
                "subsection": subsection_index,
                "source_id": str(child.get("id") or f"outline:{section_index}:{subsection_index}"),
                "title": child.get("title", ""),
                "description": child.get("description", ""),
                "key_points": child.get("key_points", []),
                "target_words": child.get("target_words", 2000),
            }
            if child.get("event_contract"):
                subsection["event_contract"] = child["event_contract"]
            subsections.append(subsection)
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
            child = {
                "id": str(uuid.uuid4()),
                "title": sub.get("title", ""),
                "description": sub.get("description", ""),
                "key_points": sub.get("key_points", []),
                "target_words": sub.get("target_words", 2000),
                "status": sub.get("status", "queued"),
            }
            if sub.get("event_contract"):
                child["event_contract"] = sub["event_contract"]
            node["children"].append(child)
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


@router.post("/{task_id}/outline/evaluate")
def evaluate_outline(task_id: str, body: OutlineEvaluationBody):
    """Run a deterministic, zero-LLM structural review of submitted nodes."""
    del task_id
    tree = _build_tree_from_nodes([dict(node) for node in body.nodes])
    selected = []
    to_section = body.to_section or len(tree)
    for section_index, section in enumerate(tree, 1):
        if body.from_section <= section_index <= to_section:
            selected.append((section_index, section))
    if not selected:
        raise HTTPException(status_code=422, detail="评估范围内没有大纲章节")

    issues = []
    titles = {}
    leaf_count = 0
    described_count = 0
    causal_count = 0
    dense_count = 0
    for section_index, section in selected:
        leaves = section.get("children") or []
        if not leaves:
            issues.append({
                "code": "empty_section",
                "severity": "high",
                "section": section_index,
                "message": "章节没有可写作的小节。",
            })
        for subsection_index, leaf in enumerate(leaves, 1):
            leaf_count += 1
            title = str(leaf.get("title") or "").strip()
            if not title:
                issues.append({
                    "code": "missing_title",
                    "severity": "high",
                    "section": section_index,
                    "subsection": subsection_index,
                    "message": "小节缺少标题。",
                })
            elif title in titles:
                issues.append({
                    "code": "duplicate_title",
                    "severity": "medium",
                    "section": section_index,
                    "subsection": subsection_index,
                    "message": f"小节标题“{title}”重复。",
                })
            else:
                titles[title] = (section_index, subsection_index)
            description = str(leaf.get("description") or "").strip()
            key_points = [
                str(item).strip()
                for item in leaf.get("key_points") or []
                if str(item).strip()
            ]
            if description or key_points:
                described_count += 1
            else:
                issues.append({
                    "code": "missing_story_action",
                    "severity": "medium",
                    "section": section_index,
                    "subsection": subsection_index,
                    "message": "小节缺少梗概和关键事件，生成目标不明确。",
                })
            event_text = " ".join([description, *key_points])
            causal_tokens = ("因为", "导致", "因此", "为了", "于是", "结果", "决定")
            if any(token in event_text for token in causal_tokens):
                causal_count += 1
            event_units = len(key_points) + (1 if description else 0)
            target_words = int(leaf.get("target_words") or 0)
            if event_units >= 4 and target_words and target_words < 1200:
                dense_count += 1
                issues.append({
                    "code": "event_density_high",
                    "severity": "medium",
                    "section": section_index,
                    "subsection": subsection_index,
                    "message": "事件数量相对目标字数过密，建议拆分或增加篇幅。",
                })

    denominator = max(1, leaf_count)
    high_count = sum(item["severity"] == "high" for item in issues)
    medium_count = sum(item["severity"] == "medium" for item in issues)
    logic_score = max(1, 10 - high_count * 3 - medium_count)
    fluency_score = max(1, round(4 + 6 * described_count / denominator))
    causality_score = max(1, round(3 + 7 * causal_count / denominator))
    density_score = max(1, round(10 - 7 * dense_count / denominator))
    return {
        "schema_version": "outline-structural-evaluation-v1",
        "analysis_mode": "deterministic_zero_llm",
        "range": {"from": body.from_section, "to": to_section},
        "section_count": len(selected),
        "leaf_count": leaf_count,
        "fluency_score": fluency_score,
        "causality_score": causality_score,
        "logic_score": logic_score,
        "density_score": density_score,
        "issues": issues,
    }


def _compile_arc_projection_preview(
    *,
    nodes: list[dict],
    characters: list[dict],
    prior_artifact: dict | None = None,
):
    from ..writing.character_arc_projection import CharacterArcProjector
    from ..writing.outline_event_contract import OutlineEventContractCompiler

    tree = _build_tree_from_nodes([dict(node) for node in nodes])
    outline = _tree_to_budget_outline(tree)
    names = [
        str(character.get("name") or "")
        for character in characters
        if str(character.get("id") or "") and str(character.get("name") or "")
    ]
    prior_by_section = {
        int(item.get("section") or 0): item
        for item in (prior_artifact or {}).get("chapters", [])
        if isinstance(item, dict)
    }
    compiler = OutlineEventContractCompiler()
    projector = CharacterArcProjector()
    chapters = []
    for section in outline:
        section_number = int(section.get("section") or 0)
        subsections = list(section.get("subsections") or [])
        chapter_contract = compiler.compile_chapter(
            section=section_number,
            subsections=subsections,
            character_names=names,
            chapter_target_words=sum(
                int(subsection.get("target_words") or 0)
                for subsection in subsections
            ),
        )
        chapters.append(projector.project(
            chapter_contract=chapter_contract,
            characters=characters,
            prior_projection=prior_by_section.get(section_number),
        ))
    return chapters


def _parse_arc_projection_review(value: object) -> dict | None:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


@router.post("/{task_id}/outline/arc-projection/preview")
def preview_outline_arc_projection(
    task_id: str, body: ArcProjectionPreviewBody
):
    """Read-only thin-UI preview; it never mutates outline or production arcs."""
    from ..dependencies import bb

    prior = _parse_arc_projection_review(
        bb.get(task_id, "character_arc_projection_review")
    )
    chapters = _compile_arc_projection_preview(
        nodes=body.nodes,
        characters=body.characters,
        prior_artifact=prior,
    )
    return {
        "schema_version": "character-arc-projection-review-v1",
        "chapters": [chapter.model_dump(mode="json") for chapter in chapters],
        "production_effect": False,
    }


@router.post("/{task_id}/outline/arc-projection/confirm")
def confirm_outline_arc_projection(
    task_id: str, body: ArcProjectionConfirmBody
):
    """Validate and store one review decision without touching character_arcs."""
    from ..dependencies import bb
    from ..writing.character_arc_projection import (
        CharacterArcProjector,
        iter_projection_candidates,
    )

    prior = _parse_arc_projection_review(
        bb.get(task_id, "character_arc_projection_review")
    )
    chapters = _compile_arc_projection_preview(
        nodes=body.nodes,
        characters=body.characters,
        prior_artifact=prior,
    )
    projector = CharacterArcProjector()
    target_chapter = None
    target_candidate = None
    for chapter in chapters:
        for candidate in iter_projection_candidates(chapter):
            if candidate.projection_id == body.projection_id:
                target_chapter = chapter
                target_candidate = candidate
                break
        if target_candidate is not None:
            break
    if target_candidate is None or target_chapter is None:
        raise HTTPException(status_code=409, detail="角色弧候选已失效，请刷新")
    try:
        confirmed = projector.confirm_candidate(
            candidate=target_candidate,
            submitted=body.model_dump(mode="json"),
        )
        updated = projector.replace_confirmed_candidate(
            projection=target_chapter,
            candidate=confirmed,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    chapters = [
        updated if chapter.section == updated.section else chapter
        for chapter in chapters
    ]
    artifact = {
        "schema_version": "character-arc-projection-review-v1",
        "chapters": [chapter.model_dump(mode="json") for chapter in chapters],
        "production_effect": False,
        "updated_at": datetime.datetime.now().isoformat(),
    }
    bb.set(task_id, "character_arc_projection_review", artifact)
    return {
        **artifact,
        "confirmed_projection_id": confirmed.projection_id,
    }


@router.get("/{task_id}/outline")
def get_outline(task_id: str):
    from ..dependencies import bb

    # 优先读 Redis outline_tree
    try:
        tree_data = bb.get(task_id, "outline_tree")
    except redis.exceptions.RedisError:
        logger.warning(
            "Redis unavailable while loading outline tree for task %s",
            task_id,
        )
        tree_data = None
    if tree_data and isinstance(tree_data, list) and len(tree_data) > 0:
        return {"nodes": _flatten_tree(tree_data), "tree": tree_data}

    # Fallback 1: 从 flat outline 反向重建树
    try:
        outline_data = bb.get(task_id, "outline")
    except redis.exceptions.RedisError:
        outline_data = None
    if outline_data and isinstance(outline_data, list) and len(outline_data) > 0:
        tree = _outline_v2_to_tree(outline_data)
        return {"nodes": _flatten_tree(tree), "tree": tree}

    # Fallback 2: tasks.db 历史记录（Redis 崩了也能恢复）
    try:
        from ..task_store import TaskStore
        from ..config import settings as _settings
        with TaskStore(_settings.TASK_DB_PATH) as ts:
            workspace = ts.find_workspace_for_task(task_id)
            record = ts.get(task_id)
        if workspace:
            saved_tree = workspace.get("outline") or []
            if saved_tree:
                return {"nodes": _flatten_tree(saved_tree), "tree": saved_tree}
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
    from ..writing.outline_event_contract import canonicalise_confirmed_tree
    tree = canonicalise_confirmed_tree(tree)
    flat = _tree_to_outline_v2(tree)

    # 先写 durable workspace；Redis 中断时项目仍可恢复。
    try:
        from ..task_store import TaskStore
        with TaskStore() as ts:
            workspace = ts.find_workspace_for_task(task_id) or {
                "workspace_task_id": task_id,
                "active_task_id": task_id,
                "status": "draft",
            }
            workspace["outline"] = tree
            ts.save_workspace(workspace["workspace_task_id"], workspace)
    except Exception:
        logger.warning(f"项目大纲持久化失败 for task {task_id}", exc_info=True)

    # Live runtime mirror is best effort.
    try:
        bb.set(task_id, "outline_tree", tree)
        bb.set(task_id, "outline", flat)
    except redis.exceptions.RedisError:
        logger.warning(
            "Redis unavailable while saving outline for task %s; durable copy saved",
            task_id,
        )

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

    try:
        from ..task_store import TaskStore

        with TaskStore() as ts:
            workspace = ts.find_workspace_for_task(task_id) or {
                "workspace_task_id": task_id,
                "active_task_id": task_id,
                "status": "draft",
            }
            workspace["outline"] = tree
            ts.save_workspace(workspace["workspace_task_id"], workspace)
    except Exception:
        logger.warning(
            f"Project outline restore persistence failed for task {task_id}",
            exc_info=True,
        )

    return {"nodes": _flatten_tree(tree)}


# ═══ 草稿 ══════════════════════════════════════════════════════════

@router.put("/{task_id}/draft")
def save_task_draft(task_id: str, body: DraftBody):
    from ..dependencies import bb
    try:
        data = bb.get_all(task_id)
    except redis.exceptions.RedisError:
        logger.warning(
            "Redis unavailable while resolving draft workspace for task %s",
            task_id,
        )
        data = {}
    try:
        from ..task_store import TaskStore
        with TaskStore() as ts:
            workspace = ts.find_workspace_for_task(task_id)
            workspace_task_id = str(
                data.get("workspace_task_id")
                or (workspace or {}).get("workspace_task_id")
                or task_id
            )
            workspace = workspace or {
                "active_task_id": task_id,
                "status": str(data.get("status") or "draft"),
            }
            workspace["active_task_id"] = task_id
            workspace["draft_backup"] = body.draft
            ts.save_workspace(workspace_task_id, workspace)
    except Exception:
        logger.warning(f"项目草稿持久化失败 for task {task_id}", exc_info=True)
    try:
        bb.set(task_id, "draft_backup", body.draft)
    except redis.exceptions.RedisError:
        logger.warning(
            "Redis unavailable while saving draft for task %s; durable copy saved",
            task_id,
        )
    return {"status": "saved"}


@router.post("/{task_id}/draft/beacon")
def save_task_draft_beacon(task_id: str, body: DraftBody):
    """POST-compatible unload fallback; normal autosave should keep using PUT."""
    return save_task_draft(task_id, body)


@router.get("/{task_id}/draft")
def get_task_draft(task_id: str):
    from ..dependencies import bb
    try:
        draft = bb.get(task_id, "draft_backup") or ""
    except redis.exceptions.RedisError:
        logger.warning(
            "Redis unavailable while loading draft for task %s",
            task_id,
        )
        draft = ""
    if draft:
        return {"draft": draft}
    try:
        try:
            data = bb.get_all(task_id)
        except redis.exceptions.RedisError:
            data = {}
        from ..task_store import TaskStore
        with TaskStore() as ts:
            workspace = ts.find_workspace_for_task(task_id)
            workspace_task_id = str(
                data.get("workspace_task_id")
                or (workspace or {}).get("workspace_task_id")
                or task_id
            )
            workspace = workspace or ts.get_workspace(workspace_task_id)
        if workspace:
            return {"draft": workspace.get("draft_backup") or ""}
    except Exception:
        logger.warning(f"项目草稿恢复失败 for task {task_id}", exc_info=True)
    return {"draft": ""}
