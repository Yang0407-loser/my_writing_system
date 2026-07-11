import os
import json as _json
import logging
import time
from datetime import datetime

from .celery_app import celery_app
from .agents.style_analyzer import StyleAnalyzer
from .agents.planner import Planner
from .agents.writer import Writer
from .agents.reviewer import Reviewer
from .agents.continuity_editor import ContinuityEditor
from .agents.character_manager import CharacterManager
from .blackboard import Blackboard
from .vector_store import VectorStore
from .world_state import WorldStateManager
from .narrative_event import EventGraph, NarrativeEvent
from .utils.prompt_templates import OUTLINE_REVIEW_PROMPT
from .utils.json_parser import parse_json
from .utils.word_counter import count_chinese_chars
from .utils.llm_client import set_api_key, reset_token_counter
from .config import settings, set_task_id

logger = logging.getLogger("writing_system.coordinator")


def _safe_serialize(obj):
    """安全序列化 world_state：支持 .serialize() 方法、dict、None。"""
    if obj is None:
        return {}
    if hasattr(obj, "serialize"):
        return obj.serialize()
    if isinstance(obj, dict):
        return obj
    return {}


def _add_timeline(bb, task_id, stage, agent, action, detail="", section=None):
    existing = bb.get(task_id, "timeline")
    if existing:
        try:
            timeline = _json.loads(existing) if isinstance(existing, str) else existing
        except (_json.JSONDecodeError, TypeError):
            timeline = []
    else:
        timeline = []
    timeline.append({
        "stage": stage, "agent": agent, "action": action,
        "detail": detail, "section": section,
    })
    bb.set(task_id, "timeline", timeline)


@celery_app.task(
    bind=True,
    name="writing_task",
    autoretry_for=(RuntimeError, ConnectionError, TimeoutError),
    retry_backoff=True,
    retry_backoff_max=120,
    retry_jitter=True,
    max_retries=5,
    default_retry_delay=30,
)
def writing_task(
    self, topic: str = "", reference_text: str = "",
    target_words_per_section: int = 10000,
    character_text: str = "",
    characters: list[dict] | None = None,
    interactive: bool = False,
    resume: bool = False,
    resume_from_task_id: str = "",
    continue_from_task_id: str = "",
    continue_outline: list[dict] | None = None,
    world_setting: str = "",
    story_synopsis: str = "",

    style_profile: dict | None = None,
    outline: list[dict] | None = None,
    api_key: str = "",
):
    """写作流水线入口。resume=True 时从检查点恢复继续。"""
    task_id = self.request.id

    # 注入 task_id 到日志上下文
    set_task_id(task_id)

    # v0.9.1: 重置 per-task token 计数器
    reset_token_counter()

    # Set per-task API key at the earliest point
    if api_key:
        set_api_key(api_key)

    bb = Blackboard()

    # 防止重启后自动续跑已停止的任务
    if not resume:
        current_status = bb.get(task_id, "status")
        if current_status == "stopped":
            logger.info(f"[{task_id[:8]}] 任务已被停止，跳过执行")
            return {"task_id": task_id, "status": "stopped", "message": "任务已被用户停止"}

    # ── 恢复路径 ──
    if resume:
        # resume_from_task_id: 从其他任务的检查点恢复（避免竞态条件）
        checkpoint_src = resume_from_task_id or task_id
        state = bb.load_checkpoint(checkpoint_src)
        if not state:
            bb.set(task_id, "status", "failed")
            bb.set(task_id, "error", "检查点不存在，无法恢复")
            return {"task_id": task_id, "status": "failed", "error": "checkpoint not found"}
        # 将检查点转移到当前 task_id，后续 save_checkpoint 使用当前 ID
        bb.save_checkpoint(task_id, state)
        # Restore per-task API key from checkpoint; fall back to the new param
        resume_key = state.get("api_key", "") or api_key
        if resume_key:
            set_api_key(resume_key)
            state["api_key"] = resume_key
        phase = state.get("phase", "init")
        bb.set(task_id, "status", "running")
    else:
        state = {
            "task_id": task_id, "phase": "characters",
            "config_topic": topic, "config_reference_text": reference_text,
            "config_target_words": target_words_per_section,
            "config_character_text": character_text,
            "config_interactive": interactive,
            "config_world_setting": world_setting,
            "config_story_synopsis": story_synopsis,

            "config_style_profile": style_profile or {},
            "config_outline": outline or [],
            "api_key": api_key,
        }
        if characters is None:
            characters = []
        state["characters"] = characters

        # ── 续写模式：继承前作状态 ──
        if continue_from_task_id and continue_outline:
            prev_bb = Blackboard()
            prev_state = prev_bb.load_checkpoint(continue_from_task_id)
            if prev_state:
                state["phase"] = "style"  # 跳过角色提取
                state["characters"] = prev_state.get("characters") or characters
                state["style_profile"] = prev_state.get("style_profile")
                state["character_arcs"] = prev_state.get("character_arcs") or []
                # 用前作文本填充 RAG 向量库
                prev_sections = prev_state.get("section_texts", {})
                prev_assembled = "\n\n".join(
                    prev_sections.get(str(i), "") for i in sorted(int(k) for k in prev_sections.keys())
                )
                state["_prev_draft"] = prev_assembled
                state["_prev_handover"] = prev_state.get("handover_chain", [])
                # 把 outline_v2 设置为新大纲
                state["outline_v2"] = continue_outline
                # 跳过风格分析和大纲生成
                state["phase"] = "writing"
                _add_timeline(bb, task_id, "continue", "system",
                              f"续写模式：接续 {continue_from_task_id}，新增 {len(continue_outline)} 节")

        bb.set(task_id, "status", "running")
        phase = state.get("phase", "characters")

    try:
        # ── 阶段路由 ──
        phase_order = [
            "characters", "style", "outline", "awaiting_outline",
            "character_arcs", "narrative_rhythm", "world_state", "writing", "awaiting_section",
            "consistency", "continuity", "review", "completed",
        ]
        start_idx = phase_order.index(phase) if phase in phase_order else 0

        for p in phase_order[start_idx:]:
            t0 = time.time()
            logger.info(f"[{task_id[:8]}] >>> 进入阶段: {p}")
            state["phase"] = p
            bb.save_checkpoint(task_id, state)

            if p == "characters":
                state = _phase_characters(bb, task_id, state)
            elif p == "style":
                state = _phase_style(bb, task_id, state)
            elif p == "outline":
                # 如果用户提供了大纲，跳过 Planner 生成
                user_outline = state.get("config_outline") or []
                if user_outline and len(user_outline) > 0:
                    # 确保每节有 section 编号（前端大纲可能没有）
                    for i, sec in enumerate(user_outline):
                        if not sec.get("section"):
                            sec["section"] = i + 1
                    state["outline_v1"] = user_outline
                    state["outline_v2"] = user_outline
                    bb.set(task_id, "outline", user_outline)
                    bb.set(task_id, "outline_v1", user_outline)
                    _add_timeline(bb, task_id, "outline", "user",
                                  f"使用用户预设大纲: {len(user_outline)} 节")
                else:
                    state = _phase_outline(bb, task_id, state)
            elif p == "awaiting_outline":
                # 应用用户修订反馈
                user_fb = state.pop("_user_feedback", None)
                if user_fb:
                    topic = state.get("config_topic", "")
                    outline_v2 = state.get("outline_v2") or []
                    target_words = state.get("config_target_words", 10000)
                    planner = Planner()
                    outline_v2 = planner.revise_from_feedback(topic, outline_v2, user_fb, target_words)
                    state["outline_v2"] = outline_v2
                    bb.set(task_id, "outline", outline_v2)
                    _add_timeline(bb, task_id, "outline_review", "planner",
                                  "根据用户反馈修订大纲", user_fb[:200])
                # 用户已批准/跳过 → 不重复进入审批等待，直接继续
                if state.pop("_outline_approved", None):
                    pass
                elif state.get("config_interactive"):
                    bb.set(task_id, "status", "awaiting_outline_approval")
                    bb.xadd_event(task_id, {"event": "awaiting_decision", "phase": "outline"})
                    return {"task_id": task_id, "status": "awaiting_outline_approval"}
            elif p == "character_arcs":
                state = _phase_character_arcs(bb, task_id, state)
            elif p == "narrative_rhythm":
                state = _phase_narrative_rhythm(bb, task_id, state)
            elif p == "world_state":
                state = _phase_world_state(bb, task_id, state)
            elif p == "writing":
                result = _phase_writing(bb, task_id, state)
                if result.get("suspended"):
                    return {"task_id": task_id, "status": "awaiting_section_confirm"}
                state = result
            elif p == "awaiting_section":
                # 读取决策队列，检查是否要停止
                decision = bb.pop_decision(task_id, "section")
                if decision and decision.get("action") == "stop":
                    bb.set(task_id, "status", "stopped")
                    return {"task_id": task_id, "status": "stopped"}
                # 否则继续写作
            elif p == "consistency":
                state = _phase_consistency_check(bb, task_id, state)
            elif p == "continuity":
                state = _phase_continuity(bb, task_id, state)
            elif p == "review":
                state = _phase_review(bb, task_id, state)
            elif p == "completed":
                _phase_complete(bb, task_id, state)
                break

            elapsed = time.time() - t0
            logger.info(f"[{task_id[:8]}] <<< 完成阶段: {p} (耗时 {elapsed:.1f}s)")

    except Exception as e:
        bb.set(task_id, "status", "failed")
        bb.set(task_id, "error", str(e))
        bb.xadd_event(task_id, {"event": "error", "message": str(e)[:500]})
        _add_timeline(bb, task_id, "error", "system", f"出错: {str(e)[:200]}")
        bb.save_checkpoint(task_id, {"task_id": task_id, "phase": "failed", "status": "failed"})
        _save_task_history(bb, task_id, state, status="failed", error=str(e)[:500])
        raise

    timeline_raw = bb.get(task_id, "timeline")
    try:
        timeline = _json.loads(timeline_raw) if isinstance(timeline_raw, str) else (timeline_raw or [])
    except (_json.JSONDecodeError, TypeError):
        timeline = []

    return {
        "task_id": task_id, "topic": state.get("config_topic", ""),
        "style": state.get("style_profile"),
        "outline": state.get("outline_v2"),
        "draft": _assemble_draft(state),
        "review": state.get("review_result"),
        "handover_notes": state.get("handover_chain", []),
        "fix_checklist": state.get("fix_checklist"),
        "timeline": timeline,
        "characters": state.get("characters", []),
        "character_arcs": state.get("character_arcs", []),
        "output_file": state.get("_output_file", ""),
    }


# ══════════════════════════════════════════════════════════════════
# 阶段方法
# ══════════════════════════════════════════════════════════════════

def _phase_characters(bb, task_id, state):
    """Phase 0: 角色提取。"""
    characters = state.get("characters") or []
    character_text = state.get("config_character_text", "")

    if characters:
        bb.set(task_id, "characters", characters)
        _add_timeline(bb, task_id, "character", "system",
                      f"从角色库加载 {len(characters)} 个角色")
    elif character_text:
        bb.set(task_id, "status", "extracting_characters")
        cm = CharacterManager()
        try:
            characters = cm.extract_characters(character_text)
            if characters:
                bb.set(task_id, "characters", characters)
                _add_timeline(bb, task_id, "character", "character_manager",
                              f"提取 {len(characters)} 个角色",
                              ", ".join(c.get("name", "?") for c in characters))
        except Exception as e:
            _add_timeline(bb, task_id, "character", "system",
                          f"角色提取失败: {str(e)[:200]}")

    state["characters"] = characters
    return state


def _phase_style(bb, task_id, state):
    """Phase 1: 风格分析。若用户已提供 style_profile，直接使用。"""
    provided = state.get("config_style_profile") or {}

    if provided.get("style_brief"):
        bb.set(task_id, "style", provided)
        state["style_profile"] = provided
        _add_timeline(bb, task_id, "style", "system", "使用用户提供的风格参数")
        return state

    bb.set(task_id, "status", "analyzing_style")
    bb.xadd_event(task_id, {"event": "phase_change", "phase": "analyzing_style"})

    sa = StyleAnalyzer()
    style = sa.analyze(reference_text=state.get("config_reference_text", ""))
    if not style.get("style_brief"):
        style["style_brief"] = sa.build_brief(style)
    bb.set(task_id, "style", style)
    state["style_profile"] = style
    _add_timeline(bb, task_id, "style", "style_analyst", "完成风格分析")
    return state


def _phase_outline(bb, task_id, state):
    """Phase 2: 大纲评审。"""
    topic = state.get("config_topic", "")
    style = state.get("style_profile") or {}
    target_words = state.get("config_target_words", 10000)

    bb.set(task_id, "status", "planning")
    bb.xadd_event(task_id, {"event": "phase_change", "phase": "planning"})

    planner = Planner()
    outline_v1 = planner.generate_outline(
        topic, style, target_words,
        world_setting=state.get("config_world_setting", ""),
        story_synopsis=state.get("config_story_synopsis", ""),
    )
    bb.set(task_id, "outline_v1", outline_v1)
    state["outline_v1"] = outline_v1
    _add_timeline(bb, task_id, "outline_review", "planner",
                  "生成大纲 V1", f"共 {len(outline_v1)} 节")

    sa = StyleAnalyzer()
    style_review = sa.review_outline(topic, style, outline_v1)
    _add_timeline(bb, task_id, "outline_review", "style_analyst",
                  f"审查大纲: {'通过' if style_review.get('approved') else '有意见'}")

    writer = Writer()
    writer_review = _writer_review_outline(writer, topic, style, outline_v1)
    _add_timeline(bb, task_id, "outline_review", "writer",
                  f"审查大纲结构: {'通过' if writer_review.get('approved') else '有意见'}")

    feedback_parts = []
    if not style_review.get("approved"):
        feedback_parts.append(f"[风格分析师] {style_review.get('suggestion', '')}")
    if not writer_review.get("approved"):
        feedback_parts.append(f"[撰稿人] {writer_review.get('suggestion', '')}")

    if feedback_parts:
        feedback_text = "\n".join(feedback_parts)
        outline_v2 = planner.revise_from_feedback(topic, outline_v1, feedback_text, target_words)
        _add_timeline(bb, task_id, "outline_review", "planner",
                      "综合反馈修订大纲 V2", f"采纳意见: {feedback_text[:200]}")
    else:
        outline_v2 = outline_v1
        _add_timeline(bb, task_id, "outline_review", "planner", "大纲无需修订", "所有审查者批准")

    bb.set(task_id, "outline", outline_v2)
    state["outline_v2"] = outline_v2
    state["outline_reviews"] = [style_review, writer_review]

    # Phase 1: 提取故事线约束
    try:
        constraints = planner.extract_constraints(
            topic, outline_v2,
            world_setting=state.get("config_world_setting", ""),
        )
        state["constraints"] = constraints
        bb.set(task_id, "constraints", constraints)
        if constraints:
            logger.info(f"[{task_id[:8]}] 提取到 {len(constraints)} 条约束")
    except Exception:
        logger.warning(f"[{task_id[:8]}] 约束提取失败，将以无约束状态继续", exc_info=True)
        state["constraints"] = []
        bb.set(task_id, "constraints", [])

    return state


def _phase_character_arcs(bb, task_id, state):
    """Phase 2.6: 角色弧线规划。"""
    bb.set(task_id, "status", "planning_character_arcs")
    bb.xadd_event(task_id, {"event": "phase_change", "phase": "planning_character_arcs"})
    characters = state.get("characters") or []
    outline_v2 = state.get("outline_v2") or []

    if not characters:
        return state


    cm = CharacterManager()
    try:
        arcs = cm.plan_arcs(characters, outline_v2)
        if arcs:
            bb.set(task_id, "character_arcs", arcs)
            state["character_arcs"] = arcs
            _add_timeline(bb, task_id, "character", "character_manager",
                          f"角色弧线规划完成: {len(arcs)} 个角色")
    except Exception as e:
        _add_timeline(bb, task_id, "character", "system",
                      f"弧线规划失败: {str(e)[:200]}")
    return state


def _phase_narrative_rhythm(bb, task_id, state):
    """Phase 2.7: 情节节奏规划 — 为每个小节生成 intensity + character_focus。"""
    bb.set(task_id, "status", "planning_rhythm")
    bb.xadd_event(task_id, {"event": "phase_change", "phase": "planning_rhythm"})

    outline_v2 = state.get("outline_v2") or []
    characters = state.get("characters") or []
    style = state.get("style_profile") or {}
    topic = state.get("config_topic", "")

    style_brief = style.get("style_brief", "") if isinstance(style, dict) else ""
    if not style_brief:
        style_brief = f"情感强度{style.get('emotion_intensity', 50)}/100"

    # 简化的节奏生成：按小节位置计算 intensity 曲线
    total_subs = sum(len(s.get("subsections", [])) for s in outline_v2)
    beats = []
    char_idx = 0
    for sec in outline_v2:
        for sub in sec.get("subsections", []):
            pos = len(beats) / max(total_subs - 1, 1)  # 0.0 ~ 1.0
            # 正弦波曲线，确保低谷在中段、高峰在两端和中后段
            intensity = int(5 + 3 * (abs(pos - 0.5) * 2))  # 两端高、中间低
            intensity = max(3, min(10, intensity))
            focus_char = ""
            if characters:
                focus_char = characters[char_idx % len(characters)].get("name", "")
                char_idx += 1
            beats.append({
                "section": sec.get("section", 0),
                "subsection": sub.get("subsection", 0),
                "intensity": intensity,
                "character_focus": focus_char,
            })
    state["narrative_beats"] = beats
    bb.set(task_id, "narrative_beats", beats)
    _add_timeline(bb, task_id, "rhythm", "beat_generator",
                  f"节奏规划: {len(beats)} 节拍, intensity 范围 {min(b['intensity'] for b in beats) if beats else 0}-{max(b['intensity'] for b in beats) if beats else 0}")
    return state


def _phase_world_state(bb, task_id, state):
    """Phase 2.8: 世界状态初始化 —— 从 world_setting 和角色背景提取初始事实。"""
    bb.set(task_id, "status", "planning_world_state")
    bb.xadd_event(task_id, {"event": "phase_change", "phase": "planning_world_state"})
    if not settings.ENABLE_WORLD_STATE:
        return state

    ws = WorldStateManager(bb, task_id)
    event_graph = EventGraph(bb, task_id)  # v3: 初始事实也写入 EventGraph
    world_setting_text = state.get("config_world_setting", "")
    characters = state.get("characters") or []

    # 从世界观设定提取事实
    if world_setting_text.strip():
        from .utils.llm_client import get_llm_client
        llm = get_llm_client()
        try:
            prompt = f"""从以下世界观设定中提取 3-8 条客观事实。每条一句话。

世界观设定：
{world_setting_text[:settings.WORLD_STATE_EXTRACT_CHARS]}

输出 JSON 数组：
[{{"category": "geography|history|rule", "fact": "一句话事实"}}]"""
            resp = llm.chat_completion(
                [{"role": "system", "content": "请以 JSON 数组格式输出。"},
                 {"role": "user", "content": prompt}],
                temperature=0.2, max_tokens=600,
            )
            from .utils.json_parser import parse_json
            facts = parse_json(resp)
            if isinstance(facts, list):
                for f in facts:
                    fact_text = f.get("fact", "")
                    ws.add_fact(category=f.get("category", "history"),
                                fact=fact_text, source_section=0)
                _add_timeline(bb, task_id, "world_state", "system",
                              f"提取 {len(facts)} 条初始世界事实")
        except Exception:
            logger.warning(f"[{task_id[:8]}] 世界事实提取失败，跳过", exc_info=True)
            _add_timeline(bb, task_id, "world_state", "system",
                          "世界事实提取跳过")

    # 从角色背景提取事实
    for c in characters:
        bg = c.get("background", "")
        if bg.strip():
            fact_text = f"{c.get('name', '?')}: {bg}"
            ws.add_fact(category="character_fact", fact=fact_text, source_section=0)
        secret = c.get("secret", "")
        if secret.strip():
            fact_text = f"{c.get('name', '?')}的秘密: {secret}"
            ws.add_fact(category="character_fact", fact=fact_text, source_section=0)

    # 提取世界锚点（关键专有名词）写入伏笔表，确保每节 prompt 可见
    from .foreshadowing_store import ensure_world_anchors
    n = ensure_world_anchors(task_id, world_setting_text, characters)
    if n:
        _add_timeline(bb, task_id, "world_state", "system",
                      f"世界锚点已写入伏笔表 ({n} 个)")

    state["world_state"] = ws
    return state


def _phase_writing(bb, task_id, state):
    """Phase 3: 继承制写作 —— 委托 Writer.run() 统一执行。"""
    topic = state.get("config_topic", "")
    style = state.get("style_profile") or {}
    outline = state.get("outline_v2") or []
    characters = state.get("characters") or []
    character_arcs = state.get("character_arcs") or []
    interactive = state.get("config_interactive", False)

    existing_draft = state.get("draft", {})
    existing_section_texts = state.get("section_texts", {})
    existing_handover = state.get("handover_chain", [])
    existing_backrefs = state.get("backref_suggestions", [])
    # 从 Redis 重建 WorldStateManager（state 序列化会丢失对象引用）
    event_graph = EventGraph(bb, task_id)
    world_state = WorldStateManager(bb, task_id, event_graph=event_graph) if settings.ENABLE_WORLD_STATE else None
    # 将角色弧线里程碑注入 EventGraph
    for arc in (character_arcs or []):
        if isinstance(arc, dict) and arc.get("key_milestones"):
            cid = arc.get("character_id", "")
            for ms in arc["key_milestones"]:
                desc = ms.get("event", ms.get("description", ""))
                if desc:
                    event_graph.add_arc_milestone(
                        description=desc,
                        section=ms.get("section", 0), subsection=ms.get("subsection", 0),
                        character_id=cid,
                        weight=5,
                    )

    bb.set(task_id, "status", "writing")
    bb.xadd_event(task_id, {"event": "phase_change", "phase": "writing"})

    vector_store = VectorStore()

    # ── 续写模式：注入前作上下文 ──
    prev_draft = state.pop("_prev_draft", None)
    prev_handover_list = state.pop("_prev_handover", None)
    if prev_draft:
        from .utils.text_chunker import chunk_text
        chunks = chunk_text(prev_draft, settings.CHUNK_SIZE, settings.CHUNK_OVERLAP)
        for chunk in chunks:
            vector_store.add_text(text=chunk, metadata={
                "task_id": task_id, "section": 0, "subsection": 0,
                "title": "前作", "topic": topic,
            })
        _add_timeline(bb, task_id, "writing", "system",
                      f"续写模式：前作 {count_chinese_chars(prev_draft)} 字已入库 ({len(chunks)} 块)")

    total_subs = sum(len(s.get("subsections", [])) for s in outline)
    _add_timeline(bb, task_id, "writing", "writer", "开始写作", f"共 {total_subs} 小节")

    writer = Writer()

    # 构建已有数据的恢复上下文
    existing_section_texts_int = {int(k): v for k, v in existing_section_texts.items()}

    def stream_callback(payload, section, subsection, event_type):
        event = {"event": event_type, "section": section, "subsection": subsection}
        if event_type == "token":
            event["text"] = payload
        elif event_type == "section_end":
            event["text"] = payload
            event["word_count"] = count_chinese_chars(payload)
        elif event_type == "expand_start":
            event["message"] = "字数不足，自动续写"
        elif event_type == "handover":
            event["message"] = f"第{section}节交接笔记已生成"
        bb.xadd_event(task_id, event)
        if event_type == "section_end":
            current_draft = bb.get(task_id, "draft") or ""
            new_section = f"\n\n第{section}节第{subsection}小节\n{payload}"
            bb.set(task_id, "draft", current_draft + new_section)

    # 用可变容器收集写作过程中的累积数据，供 on_section_done 和后续代码访问
    _accum = {"section_texts": {}, "handover_notes": [], "backref_suggestions": []}

    def on_section_done(section_num, section_texts=None, handover_notes=None, backref_suggestions=None):
        """交互模式：每节完成后保存检查点并挂起。"""
        _accum["section_texts"].update(section_texts or {})
        if handover_notes:
            _accum["handover_notes"] = handover_notes
        if backref_suggestions:
            _accum["backref_suggestions"] = backref_suggestions
        state["draft"] = existing_draft
        state["section_texts"] = {str(k): v for k, v in _accum["section_texts"].items()}
        state["handover_chain"] = _accum["handover_notes"]
        state["backref_suggestions"] = _accum["backref_suggestions"]
        state["character_arcs"] = character_arcs
        state["phase"] = "writing"
        bb.set(task_id, "character_arcs", character_arcs)
        bb.save_checkpoint(task_id, state)
        bb.xadd_event(task_id, {
            "event": "awaiting_decision", "phase": "section",
            "section": section_num, "message": f"第{section_num}节完成，等待确认",
        })
        return False  # 返回 False 表示暂停

    try:
        # Phase 1: 加载约束和规则上下文
        constraints = state.get("constraints", [])
        rules_context = ""  # rule_store.build_rules_context() 在writer内部调用

        # 构建支线上下文
        from .subplot_manager import build_subplot_context
        from .character_relation_store import build_relation_context
        subplot_context = build_subplot_context(task_id)
        relation_context = build_relation_context(task_id)
        improvement_context = state.get("improvement_targets", "") or bb.get(task_id, "improvement_targets") or ""

        # 构建长期记忆上下文
        from .experience_timeline import build_experience_context
        experience_context = build_experience_context(task_id, chapter=999)
        if experience_context:
            _add_timeline(bb, task_id, "writing", "system",
                          f"长期记忆已加载 ({len(experience_context)} 字符)")

        if subplot_context:
            _add_timeline(bb, task_id, "writing", "system",
                          f"支线上下文已加载 ({len(subplot_context)} 字符)")
        if relation_context:
            _add_timeline(bb, task_id, "writing", "system",
                          f"关系上下文已加载 ({len(relation_context)} 字符)")

        result = writer.run(
            topic=topic,
            style=style,
            outline=outline,
            vector_store=vector_store,
            blackboard=bb,
            task_id=task_id,
            characters=characters,
            character_arcs=character_arcs,
            stream_callback=stream_callback,
            interactive=interactive,
            on_section_done=on_section_done if interactive else None,
            world_setting=state.get("config_world_setting", ""),
            prev_draft=prev_draft or "",
            prev_handover_list=prev_handover_list if prev_handover_list else (existing_handover if existing_handover and not prev_handover_list else None),
            existing_draft=existing_draft,
            existing_section_texts=existing_section_texts_int,
            world_state=world_state if settings.ENABLE_WORLD_STATE else None,
            event_graph=event_graph,
            resume_context=state.pop("context_state", None),
            constraints=constraints,
            rules_context=rules_context,
            subplot_context=subplot_context,
            relation_context=relation_context,
            improvement_context=improvement_context,
            experience_context=experience_context,
        )
    except Exception as e:
        # Writer.run() 内部已有 per-subsection 错误处理，这里做最外层兜底
        logger.exception(f"[{task_id[:8]}] 写作阶段异常")
        bb.set(task_id, "status", "failed")
        bb.set(task_id, "error", f"写作阶段异常: {str(e)[:500]}")
        raise

    section_texts = result.get("section_texts", {})
    all_handover = result.get("handover_notes", [])
    all_backrefs = result.get("backref_suggestions", [])

    # 合并 on_section_done 中累积的数据（交互模式下由回调填充）
    if _accum["section_texts"]:
        section_texts = {int(k) if isinstance(k, str) and k.isdigit() else k: v
                         for k, v in _accum["section_texts"].items()}
    if _accum["handover_notes"]:
        all_handover = _accum["handover_notes"]
    if _accum["backref_suggestions"]:
        all_backrefs = _accum["backref_suggestions"]

    # --- 伏笔自动归档 + 世界锚点联动 ---
    try:
        from .foreshadowing_store import (
            create_foreshadowing, update_foreshadowing,
            list_foreshadowings, get_active_for_chapter,
        )
        all_fs = list_foreshadowings(task_id)
        existing_names = {f["name"] for f in all_fs}
        # 世界锚点映射：{锚点名: 锚点记录}
        world_anchors = {
            f["name"]: f
            for f in all_fs
            if "world_anchor" in (f.get("tags") or []) or f.get("resolve_chapter") == 999
        }
        for hn in all_handover:
            fs_text = hn.get("foreshadowing", "")
            if not fs_text or fs_text == "无":
                continue
            items = [f.strip() for f in fs_text.split(";") if f.strip() and f.strip() != "无"]
            section_num = hn.get("from_section", 0)
            for item in items:
                if not item or item in existing_names:
                    continue
                # 检查是否涉及已有世界锚点（同名包含匹配）
                matched_anchor = None
                for anchor_name, anchor_record in world_anchors.items():
                    if anchor_name in item:
                        matched_anchor = anchor_record
                        break
                if matched_anchor:
                    # 更新世界锚点描述（追加变更历史）
                    old_desc = matched_anchor.get("description", "")
                    new_desc = f"{old_desc}。第{section_num}章: {item[:100]}"
                    update_foreshadowing(matched_anchor["id"], {
                        "description": new_desc[:500],
                        "status": "planted",
                    })
                    _add_timeline(bb, task_id, "writing", "system",
                                  f"世界锚点已更新: {matched_anchor['name']} — {item[:30]}")
                else:
                    # 正常创建剧情伏笔
                    try:
                        create_foreshadowing({
                            "task_id": task_id,
                            "name": item[:80],
                            "description": item,
                            "plant_chapter": section_num,
                            "resolve_chapter": hn.get("to_section"),
                            "status": "planted",
                            "importance": 5,
                        })
                        existing_names.add(item)
                        _add_timeline(bb, task_id, "writing", "system", f"伏笔已归档: {item[:40]}")
                    except Exception:
                        pass
    except Exception as e:
        logger.warning(f"伏笔自动归档失败: {e}", exc_info=True)

    # Writer.run() 中的 on_section_done 已处理交互挂起
    # 如果交互模式且返回了不完整结果，说明被挂起了
    expected_sections = len(outline)
    completed_sections = len(section_texts)
    context_state = result.get("context_state")

    if interactive and completed_sections < expected_sections:
        state["draft"] = existing_draft
        state["section_texts"] = {str(k): v for k, v in section_texts.items()}
        state["handover_chain"] = all_handover
        state["backref_suggestions"] = all_backrefs
        state["character_arcs"] = character_arcs
        state["context_state"] = context_state
        state["phase"] = "writing"
        bb.save_checkpoint(task_id, state)
        bb.set(task_id, "status", "awaiting_section_confirm")
        return {"suspended": True, **state}

    # 非交互模式遇到 draft 墙（awaiting_queue），保存检查点不推进阶段
    if bb.get(task_id, "status") == "awaiting_queue":
        state["draft"] = existing_draft
        state["section_texts"] = {str(k): v for k, v in section_texts.items()}
        state["handover_chain"] = all_handover
        state["backref_suggestions"] = all_backrefs
        state["character_arcs"] = character_arcs
        state["context_state"] = context_state
        state["phase"] = "writing"
        bb.save_checkpoint(task_id, state)
        return {"suspended": True, **state}

    # 全部完成
    assembled = "\n\n".join(section_texts.get(i, "") for i in sorted(section_texts.keys()))
    bb.set(task_id, "draft", assembled)
    import json as _json
    bb.set(task_id, "section_texts", _json.dumps({str(k): v for k, v in section_texts.items()}, ensure_ascii=False))
    state["draft"] = existing_draft
    state["section_texts"] = {str(k): v for k, v in section_texts.items()}
    state["handover_chain"] = all_handover
    state["backref_suggestions"] = all_backrefs
    state["character_arcs"] = character_arcs
    state["context_state"] = context_state
    return state


def _phase_consistency_check(bb, task_id, state):
    """Phase 3.5: 角色一致性检查。"""
    characters = state.get("characters") or []
    section_texts = state.get("section_texts", {})
    draft_text = "\n\n".join(section_texts.get(i, "") for i in sorted(section_texts.keys()))

    if not characters or os.getenv("ENABLE_CONSISTENCY_CHECK", "true").lower() == "false":
        return state

    bb.set(task_id, "status", "checking_character_consistency")
    bb.xadd_event(task_id, {"event": "phase_change", "phase": "consistency_check"})
    try:
        from .utils.prompt_templates import CHARACTER_CONSISTENCY_PROMPT
        from .utils.llm_client import get_llm_client
        llm = get_llm_client()
        characters_json = _json.dumps(characters, ensure_ascii=False, indent=2)
        prompt = CHARACTER_CONSISTENCY_PROMPT.format(
            section_text=draft_text[:4000], characters_json=characters_json,
        )
        messages = [
            {"role": "system", "content": "你是一位严谨的角色一致性检查员。"},
            {"role": "user", "content": prompt},
        ]
        resp = llm.chat_completion(messages, temperature=0.3, max_tokens=1000)
        issues = None
        try:
            issues = _json.loads(resp)
        except _json.JSONDecodeError:
            try:
                issues = parse_json(resp)
            except ValueError:
                pass
        if issues:
            bb.set(task_id, "consistency_issues", issues)
            _add_timeline(bb, task_id, "consistency", "character_checker", f"发现 {len(issues)} 处角色不一致")
    except Exception as e:
        logger.warning(f"[{task_id[:8]}] 角色一致性检查失败", exc_info=True)
        _add_timeline(bb, task_id, "consistency", "system", f"角色一致性检查失败: {str(e)[:200]}")
    return state


def _phase_continuity(bb, task_id, state):
    """Phase 4: 连续性编辑。"""
    backrefs = state.get("backref_suggestions") or []
    section_texts = state.get("section_texts", {})
    outline_v2 = state.get("outline_v2") or []

    bb.set(task_id, "status", "continuity_editing")
    bb.xadd_event(task_id, {"event": "phase_change", "phase": "continuity_editing"})

    ce = ContinuityEditor()
    section_summary_parts = []
    for i, sec in enumerate(outline_v2, 1):
        preview = section_texts.get(i, "")[:200].replace("\n", " ")
        section_summary_parts.append(f"第{i}节 ({count_chinese_chars(section_texts.get(i, ''))}字): {preview}...")
    section_summaries = "\n".join(section_summary_parts)

    fix_checklist = ce.run(backrefs, section_summaries)
    bb.set(task_id, "fix_checklist", fix_checklist)
    state["fix_checklist"] = fix_checklist

    critical_count = len(fix_checklist.get("critical_fixes", []))
    minor_count = len(fix_checklist.get("minor_fixes", []))
    _add_timeline(bb, task_id, "backref", "continuity_editor",
                  f"生成修正清单: {critical_count} 严重 + {minor_count} 轻微")

    # 执行 critical 修正
    if fix_checklist.get("critical_fixes"):
        bb.set(task_id, "status", "fixing")
        bb.xadd_event(task_id, {"event": "phase_change", "phase": "fixing"})
        writer = Writer()
        for fix in fix_checklist["critical_fixes"]:
            target_sec = fix.get("target_section")
            if target_sec and target_sec in section_texts:
                original = section_texts[target_sec]
                revised = writer.revise_subsection(original, fix.get("description", ""))
                section_texts[target_sec] = revised
                _add_timeline(bb, task_id, "fixing", "writer",
                              f"修正第{target_sec}节", fix.get("description", "")[:200],
                              section=target_sec)
        state["section_texts"] = section_texts

    return state


def _phase_review(bb, task_id, state):
    """Phase 5: 审阅。"""
    topic = state.get("config_topic", "")
    style = state.get("style_profile") or {}
    section_texts = state.get("section_texts", {})
    handover_chain = state.get("handover_chain") or []
    fix_checklist = state.get("fix_checklist") or {}
    characters = state.get("characters") or []
    character_arcs = state.get("character_arcs") or []
    outline_v2 = state.get("outline_v2") or []

    bb.set(task_id, "status", "reviewing")
    bb.xadd_event(task_id, {"event": "phase_change", "phase": "reviewing"})

    reviewer = Reviewer()
    section_reviews = []
    # 构建卷名→叶子节点映射
    volume_labels = {}
    for vi, vol in enumerate(outline_v2, 1):
        vol_title = vol.get("title", f"第{vi}卷")
        subs = vol.get("subsections", [])
        leaves = [s.get("title", "") for s in subs]
        volume_labels[vi] = {"title": vol_title, "leaves": leaves}
    for i in sorted(section_texts.keys()):
        try:
            sr = reviewer.review_section(i, topic, style, section_texts[i])
            sr["section"] = i
            vi = volume_labels.get(i, {})
            sr["volume_title"] = vi.get("title", f"第{i}卷")
            sr["leaf_titles"] = vi.get("leaves", [])
            section_reviews.append(sr)
        except Exception:
            logger.warning(f"[{task_id[:8]}] 第{i}节审阅失败，使用默认评分", exc_info=True)
            vi = volume_labels.get(i, {})
            section_reviews.append({
                "section": i, "score": None, "scores": {}, "highlight": {}, "lowlight": {},
                "consistency_notes": "", "improvement": "", "rewrite_target": "",
                "volume_title": vi.get("title", f"第{i}卷"),
                "leaf_titles": vi.get("leaves", []),
                "_fallback": True,
            })

    handover_chain_text = "\n".join(
        f"第{n.get('from_section', '?')}节→第{n.get('to_section', '?')}节: "
        f"伏笔={n.get('foreshadowing', '')[:80]}"
        for n in handover_chain
    )

    section_summary_parts = []
    for i, sec in enumerate(outline_v2, 1):
        preview = section_texts.get(i, "")[:200].replace("\n", " ")
        section_summary_parts.append(f"第{i}节 ({count_chinese_chars(section_texts.get(i, ''))}字): {preview}...")
    section_summaries = "\n".join(section_summary_parts)

    # 构建新上下文：分节评分汇总 + 支线 + 关系
    section_scores_parts = []
    for sr in section_reviews:
        sc = sr.get("scores", {})
        if sc:
            dims = ", ".join(f"{k}={v}" for k, v in sc.items())
            section_scores_parts.append(f"第{sr.get('section','?')}节: {dims}")
    section_scores_summary = "\n".join(section_scores_parts) if section_scores_parts else ""

    from .subplot_manager import build_subplot_context
    from .character_relation_store import build_relation_context
    subplot_ctx = build_subplot_context(task_id)
    relation_ctx = build_relation_context(task_id)

    assembled = "\n\n".join(section_texts.get(i, "") for i in sorted(section_texts.keys()))
    global_review = reviewer.review_global(
        topic=topic, style=style, section_summaries=section_summaries,
        total_words=count_chinese_chars(assembled),
        handover_chain=handover_chain_text,
        fix_summary=fix_checklist.get("summary", ""),
        characters=characters, character_arcs=character_arcs,
        subplot_context=subplot_ctx, relation_context=relation_ctx,
        section_scores=section_scores_summary,
    )

    review = {
        "section_reviews": section_reviews,
        "global_score": global_review.get("global_score", 6),
        "chapter_scores": global_review.get("chapter_scores", []),
        "tension_curve": global_review.get("tension_curve", ""),
        "pacing_issues": global_review.get("pacing_issues", []),
        "style_adherence": global_review.get("style_adherence", ""),
        "subplot_health": global_review.get("subplot_health", []),
        "character_arc_health": global_review.get("character_arc_health", []),
        "top_3_actions": global_review.get("top_3_actions", []),
        "strength": global_review.get("strength", ""),
        "weakness": global_review.get("weakness", ""),
        "suggestion": global_review.get("suggestion", ""),
        "handover_insight": global_review.get("handover_insight", ""),
        "character_consistency": global_review.get("character_consistency", ""),
        "character_arc_progress": global_review.get("character_arc_progress", ""),
    }
    bb.set(task_id, "review", review)
    state["review_result"] = review

    # 保存改进目标供后续写作使用 (审阅→Writer 闭环)
    top3 = global_review.get("top_3_actions", [])
    if top3:
        improvement_text = "\n".join(f"{i+1}. {a}" for i, a in enumerate(top3))
        state["improvement_targets"] = improvement_text
        bb.set(task_id, "improvement_targets", improvement_text)
        _add_timeline(bb, task_id, "review", "system",
                      f"改进目标已保存: {len(top3)} 条")
    _add_timeline(bb, task_id, "review", "reviewer",
                  f"全局评分 {global_review.get('global_score', '?')}/10")
    return state


def _phase_complete(bb, task_id, state):
    """标记完成并导出。"""
    section_texts = state.get("section_texts", {})
    assembled = "\n\n".join(section_texts.get(i, "") for i in sorted(section_texts.keys()))

    bb.set(task_id, "status", "completed")
    bb.set(task_id, "progress", f"完成 — 共 {count_chinese_chars(assembled)} 字")
    bb.xadd_event(task_id, {"event": "done", "draft": assembled, "review": state.get("review_result")})

    output_path = _export_draft(task_id, state.get("config_topic", ""), assembled,
                                state.get("handover_chain", []),
                                state.get("review_result") or {},
                                state.get("fix_checklist") or {})
    state["_output_file"] = output_path

    _save_task_history(bb, task_id, state, status="completed")

    logger.info(f"[{task_id[:8]}] 任务完成，输出: {output_path}")


def _save_task_history(bb, task_id, state, status="completed", error=""):
    """写入任务历史到 SQLite，完成和失败都记录。"""
    try:
        from .task_store import TaskStore
        ts = TaskStore(settings.TASK_DB_PATH)
        event_graph = EventGraph(bb, task_id)
        events_data = [e.to_dict() for e in event_graph._events.values()]
        outline_data = state.get("outline_v2") or []
        tree_raw = bb.get(task_id, "outline_tree")
        if tree_raw:
            try:
                outline_data = _json.loads(tree_raw) if isinstance(tree_raw, str) else tree_raw
            except (_json.JSONDecodeError, TypeError):
                pass
        if status == "failed":
            assembled = _assemble_draft(state)
        else:
            section_texts = state.get("section_texts", {})
            assembled = "\n\n".join(section_texts.get(i, "") for i in sorted(section_texts.keys()))

        ts.save(task_id, {
            "topic": state.get("config_topic", ""),
            "word_count": count_chinese_chars(assembled),
            "section_count": len(state.get("section_texts", {})),
            "status": status,
            "mode": "interactive" if state.get("config_interactive") else "celery",
            "style": state.get("style_profile"),
            "outline": outline_data,
            "handover_notes": state.get("handover_chain"),
            "characters": state.get("characters"),
            "review": state.get("review_result"),
            "world_setting": state.get("config_world_setting", ""),
            "story_synopsis": state.get("config_story_synopsis", ""),
            "target_words": state.get("config_target_words", 0),
            "world_state": _safe_serialize(state.get("world_state")),
            "draft": assembled,
            "output_file": state.get("_output_file", ""),
            "events": events_data,
            "analysis": state.get("analysis", {}) or {},
        })
    except Exception:
        logger.warning("任务历史写入失败", exc_info=True)



# ══════════════════════════════════════════════════════════════════
# 辅助函数
# ══════════════════════════════════════════════════════════════════

def _assemble_draft(state):
    """从 section_texts 拼装完整草稿。"""
    section_texts = state.get("section_texts", {})
    if not section_texts:
        return state.get("draft", "")
    # 按数值排序（兼容 str/int 混合 key）
    keys = sorted(section_texts.keys(), key=lambda k: int(k) if str(k).isdigit() else k)
    return "\n\n".join(section_texts.get(k, "") for k in keys)


def _writer_review_outline(writer, topic, style, outline) -> dict:
    """撰稿人审查大纲的可执行性。"""
    style_brief = style.get("style_brief", "") if isinstance(style, dict) else ""
    style_summary = style_brief if style_brief else (
        f"情感强度{style.get('emotion_intensity', 50)}/100，"
        f"段落长度约{style.get('paragraph_length_avg', 200)}字"
    )
    outline_text = _json.dumps(outline, ensure_ascii=False, indent=2)
    prompt = OUTLINE_REVIEW_PROMPT.format(
        reviewer_role="撰稿人", review_perspective="可执行性（结构是否合理、小节是否过多/过少、逻辑是否连贯）",
        topic=topic, style_summary=style_summary, outline_text=outline_text,
    )
    messages = [
        {"role": "system", "content": "你是一位撰稿人。请审查大纲的可执行性。"},
        {"role": "user", "content": prompt},
    ]
    response = writer.llm.chat_completion(messages, temperature=0.3, max_tokens=800)
    try:
        result = parse_json(response)
        return {"reviewer": "writer", "approved": result.get("approved", True),
                "criticism": result.get("criticism", ""), "suggestion": result.get("suggestion", "")}
    except ValueError:
        return {"reviewer": "writer", "approved": True, "criticism": "", "suggestion": ""}


def _export_draft(task_id, topic, draft, handover_notes, review, fix_checklist):
    """导出最终文稿到 output/ 目录。"""
    out_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "output")
    os.makedirs(out_dir, exist_ok=True)
    safe_topic = "".join(c for c in topic if c.isalnum() or c in " _-")[:30]
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filepath = os.path.join(out_dir, f"{safe_topic}_{timestamp}.md")

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(f"# {topic}\n\n")
        f.write(f"> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"> 总字数: {count_chinese_chars(draft)}\n")
        f.write(f"> 全局评分: {review.get('global_score', '?')}/10\n\n")
        f.write("---\n\n")
        f.write(draft)
        f.write("\n\n---\n\n## 审阅意见\n\n")
        f.write(f"**全局评分**: {review.get('global_score', '?')}/10\n\n")
        f.write(f"**建议**: {review.get('suggestion', '')}\n\n")
        if review.get('handover_insight'):
            f.write(f"**交接笔记洞察**: {review.get('handover_insight', '')}\n\n")
        f.write("\n## 交接笔记链\n\n")
        for n in handover_notes:
            f.write(f"- **第{n.get('from_section')}节→第{n.get('to_section')}节**\n")
            f.write(f"  - 伏笔: {n.get('foreshadowing', '')}\n")
            f.write(f"  - 人物状态: {n.get('character_state', '')}\n")
            f.write(f"  - 待承接: {n.get('open_threads', '')}\n\n")
        if fix_checklist and fix_checklist.get("critical_fixes"):
            f.write("\n## 修正清单\n\n")
            for fix in fix_checklist["critical_fixes"]:
                f.write(f"- **[严重]** 第{fix.get('target_section')}节: {fix.get('description', '')}\n")
            for fix in fix_checklist.get("minor_fixes", []):
                f.write(f"- [轻微] 第{fix.get('target_section')}节: {fix.get('description', '')}\n")

    return filepath
