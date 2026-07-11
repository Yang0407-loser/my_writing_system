import os
import re
from fastapi import APIRouter, Header, HTTPException, Query
from ..models import WriteRequest, WriteResponse, TaskStatus, ReviseRequest
from ..dependencies import bb
from ..task_store import TaskStore
from ..coordinator import writing_task
from ..celery_app import celery_app

router = APIRouter(tags=["tasks"])

@router.post("/write", response_model=WriteResponse)
def create_writing_task(
    req: WriteRequest,
    mode: str = Query("celery", description="执行模式: 'celery' 或 'interactive'"),
    x_api_key: str = Header("", alias="X-API-Key"),
):
    """提交写作任务。

    - mode=celery: Celery 异步执行，通过 GET /status/{task_id} 轮询进度，
      通过 GET /stream/{task_id} 获取流式事件。
    - mode=interactive: 同上，但每节完成后暂停等待用户确认，
      用户通过 POST /tasks/{task_id}/decide 发送决策。
    - X-API-Key: 用户自己的 API Key（可选，不填则使用服务器 Key）。
    """
    interactive = (mode == "interactive")

    task = writing_task.delay(
        topic=req.topic,
        reference_text=req.reference_text,
        target_words_per_section=req.target_words_per_section,
        character_text=req.character_text,
        characters=[c.model_dump() if hasattr(c, 'model_dump') else c for c in req.characters],
        interactive=interactive,
        world_setting=req.world_setting,
        story_synopsis=req.story_synopsis,

        style_profile=req.style_profile,
        outline=req.outline,
        api_key=x_api_key,
    )
    bb.set(task.id, "status", "pending")
    bb.set(task.id, "mode", mode)
    return WriteResponse(task_id=task.id, status="pending")


# ── 状态查询 ────────────────────────────────────────────────────

@router.get("/status/{task_id}", response_model=TaskStatus)
def get_task_status(task_id: str):
    data = bb.get_all(task_id)
    if not data:
        raise HTTPException(status_code=404, detail="任务不存在")
    return TaskStatus(
        task_id=task_id,
        status=data.get("status", "unknown"),
        progress=data.get("progress"),
        style=data.get("style"),
        outline=data.get("outline"),
        outline_v1=data.get("outline_v1"),
        outline_reviews=data.get("outline_reviews"),
        handover_notes=data.get("handover_notes"),
        fix_checklist=data.get("fix_checklist"),
        timeline=data.get("timeline"),
        draft=data.get("draft"),
        review=data.get("review"),
        error=data.get("error"),
        characters=data.get("characters"),
        character_arcs=data.get("character_arcs"),
        world_state=data.get("world_state"),
        constraints=data.get("constraints"),
        foreshadowings=data.get("foreshadowings"),
        ai_detect_log=data.get("ai_detect_log"),
        section_reviews=data.get("section_reviews"),
        token_usage=data.get("token_usage"),
    )


@router.get("/result/{task_id}")
def get_task_result(task_id: str):
    task = writing_task.AsyncResult(task_id)
    if task.ready():
        if task.successful():
            return task.result
        else:
            raise HTTPException(status_code=500, detail=str(task.info))
    else:
        data = bb.get_all(task_id)
        if not data:
            raise HTTPException(status_code=404, detail="任务不存在")
        return {"task_id": task_id, "status": data.get("status", "pending"), "message": "任务尚未完成"}


# ── Redis Stream 流式事件 ───────────────────────────────────────

@router.get("/stream/{task_id}")
def stream_events(
    task_id: str,
    last_id: str = Query("0-0", description="上一次收到的事件 ID，首次请求传 '0-0'"),
    count: int = Query(100, description="每次返回的最大事件数"),
):
    """轮询获取任务的流式事件（替代 WebSocket）。

    前端每 300-500ms 调用一次，传入上次最后收到的事件 ID。
    返回格式：
    {
        "events": [["msg_id", {"event": "token", ...}], ...],
        "status": "running" | "completed" | "failed",
        "last_id": "最新事件 ID"
    }
    """
    data = bb.get_all(task_id)
    if not data:
        raise HTTPException(status_code=404, detail="任务不存在")

    events = bb.xread_events(task_id, last_id=last_id, count=count)
    new_last_id = events[-1][0] if events else last_id

    return {
        "events": events,
        "status": data.get("status", "unknown"),
        "last_id": new_last_id,
    }


# ── 检查点决策 ──────────────────────────────────────────────────

@router.post("/tasks/{task_id}/decide")
def task_decision(
    task_id: str,
    phase: str = Query(..., description="阶段: 'outline' 或 'section'"),
    action: str = Query("approve", description="'approve' | 'revise' | 'stop'"),
    feedback: str = Query("", description="修订反馈（action=revise 时必填）"),
    x_api_key: str = Header("", alias="X-API-Key"),
):
    """向任务的检查点发送决策并触发继续。

    - phase=outline: 大纲审批（action: approve / revise）
    - phase=section: 节完成确认（action: approve / stop）
    """
    data = bb.get_all(task_id)
    if not data:
        raise HTTPException(status_code=404, detail="任务不存在")

    decision = {"action": action, "feedback": feedback}
    bb.push_decision(task_id, phase, decision)

    if action != "stop":
        # 加载检查点，注入用户决策标记，然后启动新任务
        checkpoint = bb.load_checkpoint(task_id)
        if checkpoint:
            if action == "revise" and feedback:
                checkpoint["_user_feedback"] = feedback
            elif action == "approve":
                checkpoint["_outline_approved"] = True
            # 先存检查点（旧 task_id），再创建任务——避免 worker 竞态
            bb.save_checkpoint(task_id, checkpoint)
            new_task = writing_task.delay(
                topic=checkpoint.get("config_topic", ""),
                reference_text=checkpoint.get("config_reference_text", ""),
                target_words_per_section=checkpoint.get("config_target_words", 10000),
                character_text=checkpoint.get("config_character_text", ""),
                characters=checkpoint.get("characters", []),
                interactive=checkpoint.get("config_interactive", False),
                resume=True,
                resume_from_task_id=task_id,
                api_key=x_api_key,
            )
            return {"status": "ok", "phase": phase, "action": action, "new_task_id": new_task.id}
    else:
        # 撤销 Celery 任务，防止重启后自动续跑
        try:
            celery_app.control.revoke(task_id, terminate=False)
        except Exception:
            pass
        bb.set(task_id, "status", "stopped")
        bb.xadd_event(task_id, {"event": "cancelled", "message": "用户停止"})

    return {"status": "ok", "phase": phase, "action": action}


# ── 大纲编辑 ────────────────────────────────────────────────────

@router.post("/tasks/{task_id}/update-outline")
def update_outline(task_id: str, body: dict):
    """在审批阶段直接替换大纲。

    接受树状或扁平格式。树状格式会被扁平化为 [{section, subsections}] 并存两份：
    - outline_tree: 原始树（前端编辑器恢复用）
    - outline: 扁平版（写作管线用）

    仅在 status 为 awaiting_outline_approval 或 awaiting_section_confirm 时可用。
    """
    data = bb.get_all(task_id)
    if not data:
        raise HTTPException(status_code=404, detail="任务不存在")

    status = data.get("status", "")
    allowed = ("awaiting_outline_approval", "awaiting_section_confirm", "writing", "pending")
    if status not in allowed and "writing" not in (status or ""):
        raise HTTPException(status_code=400, detail="当前状态不允许编辑大纲")

    new_outline = body.get("outline")
    if not new_outline or not isinstance(new_outline, list):
        raise HTTPException(status_code=400, detail="outline 必须是非空数组")

    # 判断是否为树状格式（任意节点含 children 字段）
    is_tree = any("children" in sec for sec in new_outline)

    if is_tree:
        from ..models import flatten_tree_to_outline
        bb.set(task_id, "outline_tree", new_outline)
        flat = flatten_tree_to_outline(new_outline)
    else:
        flat = new_outline

    bb.set(task_id, "outline", flat)
    bb.push_notification(task_id, "outline_updated")
    checkpoint = bb.load_checkpoint(task_id)
    if checkpoint:
        checkpoint["outline_v2"] = flat
        checkpoint["outline_tree"] = new_outline
        bb.save_checkpoint(task_id, checkpoint)

    return {"status": "ok", "sections": len(flat)}


# ── 大纲审批（兼容旧接口）──────────────────────────────────────

@router.post("/tasks/{task_id}/approve-outline")
def approve_outline(task_id: str, action: str = "approve", feedback: str = ""):
    """用户审批大纲（兼容旧 API，内部转发到 /decide）。"""
    data = bb.get_all(task_id)
    if not data:
        raise HTTPException(status_code=404, detail="任务不存在")
    bb.push_decision(task_id, "outline", {"action": action, "feedback": feedback})
    return {"status": "ok", "action": action}


# ── 定向修订 ────────────────────────────────────────────────────

@router.post("/tasks/{task_id}/revise")
def revise_subsection(task_id: str, req: ReviseRequest, x_api_key: str = Header("", alias="X-API-Key")):
    data = bb.get_all(task_id)
    if not data:
        raise HTTPException(status_code=404, detail="任务不存在")
    if data.get("status") != "completed":
        raise HTTPException(status_code=400, detail="任务尚未完成，无法修订")

    from ..agents.writer import Writer
    from ..utils.llm_client import set_api_key
    import re as _regex
    if x_api_key: set_api_key(x_api_key)
    writer = Writer()

    draft = data.get("draft", "")
    lines = draft.split("\n")
    start_idx = None
    end_idx = len(lines)
    # 支持多种节标题格式: "第X节：" / "第X节:" / "Section X:" / "第X章："
    sec_patterns = [
        _regex.compile(rf"第\s*{req.section}\s*[节章][：:]"),
        _regex.compile(rf"[Ss]ection\s+{req.section}[：:]"),
    ]
    next_patterns = [
        _regex.compile(rf"第\s*{req.section + 1}\s*[节章][：:]"),
        _regex.compile(rf"[Ss]ection\s+{req.section + 1}[：:]"),
    ]
    for i, line in enumerate(lines):
        if start_idx is None and any(p.search(line) for p in sec_patterns):
            start_idx = i
        elif start_idx is not None and any(p.search(line) for p in next_patterns):
            end_idx = i
            break

    if start_idx is None:
        raise HTTPException(status_code=404, detail=f"未找到第{req.section}节")

    original = "\n".join(lines[start_idx:end_idx])
    revised = writer.revise_subsection(original, req.instruction)

    new_lines = lines[:start_idx] + [revised] + lines[end_idx:]
    new_draft = "\n".join(new_lines)
    bb.set(task_id, "draft", new_draft)

    return {"status": "revised", "section": req.section}


# ── 字段编辑 ────────────────────────────────────────────────────

@router.post("/tasks/{task_id}/edit-field")
def edit_task_field(task_id: str, field: str, body: dict):
    data = bb.get_all(task_id)
    if not data:
        raise HTTPException(status_code=404, detail="任务不存在")
    if data.get("status") != "completed":
        raise HTTPException(status_code=400, detail="任务尚未完成，无法编辑")
    allowed = {"outline", "review", "handover_notes", "outline_v1", "draft"}
    if field not in allowed:
        raise HTTPException(status_code=400, detail=f"不允许编辑字段: {field}")
    bb.set(task_id, field, body["value"])
    return {"status": "ok", "field": field}


# ── 续写 API ────────────────────────────────────────────────────

@router.post("/tasks/{task_id}/continue")
def continue_writing(task_id: str, body: dict, x_api_key: str = Header("", alias="X-API-Key")):
    """基于已完成任务续写新章节。

    Body:
        additional_outline: 新增的大纲（节×小节结构），必填
        target_words_per_section: 每节目标字数，默认沿用前作
        mode: 'celery' | 'interactive'
    """
    prev_checkpoint = bb.load_checkpoint(task_id)
    if not prev_checkpoint:
        data = bb.get_all(task_id)
        if not data:
            raise HTTPException(status_code=404, detail="原任务不存在")
        if data.get("status") not in ("completed", "stopped"):
            raise HTTPException(status_code=400, detail="原任务状态不允许续写")

    # 加载前作状态
    prev_checkpoint = bb.load_checkpoint(task_id) or {}
    prev_draft_map = prev_checkpoint.get("draft", {})
    prev_section_texts = prev_checkpoint.get("section_texts", {})
    assembled_prev = "\n\n".join(
        prev_section_texts.get(str(i), "") for i in sorted(int(k) for k in prev_section_texts.keys())
    )

    additional_outline = body.get("additional_outline", [])
    if not additional_outline:
        raise HTTPException(status_code=400, detail="additional_outline 不能为空")

    target_words = body.get("target_words_per_section", prev_checkpoint.get("config_target_words", 3000))
    interactive = body.get("mode", "celery") == "interactive"

    # 重新编号新大纲（接上前作节号）
    prev_section_count = len(prev_section_texts)
    offset_outline = []
    for i, sec in enumerate(additional_outline):
        new_sec = dict(sec)
        new_sec["section"] = prev_section_count + i + 1
        offset_outline.append(new_sec)

    task = writing_task.delay(
        topic=prev_checkpoint.get("config_topic", ""),
        reference_text=assembled_prev[:8000],  # 前作作为风格参考
        target_words_per_section=target_words,
        character_text="",
        characters=prev_checkpoint.get("characters") or [],
        interactive=interactive,
        continue_from_task_id=task_id,
        continue_outline=offset_outline,
        api_key=x_api_key,
    )

    bb.set(task.id, "status", "pending")
    bb.set(task.id, "mode", "interactive" if interactive else "celery")
    bb.set(task.id, "continue_from", task_id)
    return {"task_id": task.id, "status": "pending", "continue_from": task_id, "total_new_sections": len(offset_outline)}