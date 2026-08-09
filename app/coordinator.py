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
from .embedding.factory import preflight_embedding_backend
from .world_state import WorldStateManager
from .narrative_event import EventGraph, NarrativeEvent
from .utils.prompt_templates import OUTLINE_REVIEW_PROMPT
from .utils.json_parser import parse_json
from .utils.word_counter import count_chinese_chars
from .utils.llm_client import set_api_key, reset_token_counter, get_cumulative_tokens, get_token_breakdown, set_cost_label
from .config import settings, set_task_id
from .character_arc_contract import (
    build_v2_edge_plan,
    iter_v2_event_milestones,
    normalize_v2_arcs,
    resolve_contract_version,
)
from .writing.character_state_propagation import (
    build_character_state_propagation_event,
    character_arcs_hash,
    copy_character_arcs,
    is_valid_character_arcs,
    resolve_writer_character_arcs,
)

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


def _apply_writer_character_state(bb, task_id: str, state: dict, result: dict, fallback):
    """Adopt Writer state without relying on Blackboard as the source of truth."""
    character_arcs, source = resolve_writer_character_arcs(result, fallback)
    propagation = result.get("character_state_propagation")
    state_hash = character_arcs_hash(character_arcs)
    if not isinstance(propagation, dict):
        propagation = build_character_state_propagation_event(
            task_id=task_id,
            section=None,
            subsection=None,
            source=source,
            input_state_hash=state_hash,
            updated_state_hash=state_hash,
            coordinator_state_hash=state_hash,
            checkpoint_state_hash=state_hash,
            update_applied=False,
            fallback_reason=source,
            checkpoint_version="phase4r-r1",
        )
    else:
        propagation = dict(propagation)
        if source != "writer_updated":
            propagation["source"] = source
            propagation["fallback_reason"] = source
        propagation["coordinator_state_hash"] = state_hash
        propagation["checkpoint_state_hash"] = state_hash
    state["character_arcs"] = copy_character_arcs(character_arcs)
    state["_character_state_propagation"] = propagation
    bb.set(task_id, "character_arcs", copy_character_arcs(character_arcs))
    return character_arcs, propagation


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
        # 同步 config 到主 Redis hash，让 /status 可返回
        bb.set(task_id, "topic", topic)
        bb.set(task_id, "world_setting", world_setting)
        bb.set(task_id, "story_synopsis", story_synopsis)
        bb.set(task_id, "reference_text", reference_text)
        bb.set(task_id, "style_profile", style_profile or {})
        bb.set(task_id, "target_words_per_section", target_words_per_section)
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
        phase_timings = {}

    try:
        # ── 基础设施前置检查 ──
        # embedding 后端只在 _phase_writing 里才被触碰，那时 character_arcs 和
        # world_state 的 LLM token 已经花掉了；后端不可达抛出的 RuntimeError 又
        # 正好命中 autoretry_for，导致整任务重放、规划阶段重复计费。
        # 2026-07-26 的真实事故：5 次重放烧掉 26,058 token（占该任务约 40%）。
        # 这里探活零成本，且不改变重试语义——后端在退避窗口内恢复，任务照样成功。
        backend_ok, backend_reason = preflight_embedding_backend()
        if not backend_ok:
            logger.warning(
                f"[{task_id[:8]}] embedding 后端预检失败，零 token 退出: {backend_reason}"
            )
            raise RuntimeError(f"EmbeddingBackendUnavailable: {backend_reason}")

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
            phase_timings[p] = round(elapsed, 1)
            logger.info(f"[{task_id[:8]}] <<< 完成阶段: {p} (耗时 {elapsed:.1f}s)")

    except Exception as e:
        logger.warning(
            f"[{task_id[:8]}] 任务失败，累计 Token: {get_cumulative_tokens()}, "
            f"阶段耗时: {_json.dumps(phase_timings) if phase_timings else 'N/A'}"
        )
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

    # ── v0.9.2: Token 成本 & 端到端延迟汇总 ──
    total_tokens = get_cumulative_tokens()
    total_time = sum(phase_timings.values())
    est_cost = total_tokens * 0.000000435  # DeepSeek V4 Pro input price (cache miss)
    token_breakdown = get_token_breakdown()

    logger.info(
        f"[{task_id[:8]}] ====== 性能汇总 ======"
    )
    logger.info(
        f"[{task_id[:8]}] 总耗时: {total_time:.1f}s | "
        f"总 Token: {total_tokens} | 预估成本: ${est_cost:.4f}"
    )
    if token_breakdown:
        logger.info(
            f"[{task_id[:8]}] Agent Token 分布: {_json.dumps(token_breakdown, ensure_ascii=False)}"
        )
    logger.info(
        f"[{task_id[:8]}] 各阶段耗时: {_json.dumps(phase_timings, ensure_ascii=False)}"
    )

    # ── v0.9.2: 事实验证统计 ──
    try:
        from .world_state import fact_stats
        fs = fact_stats.summary()
        logger.info(
            f"[{task_id[:8]}] 事实验证: total={fs['total_facts']} "
            f"rule(subj={fs['rule_subjective']}/obj={fs['rule_objective']}/mixed={fs['rule_mixed']}) "
            f"llm(ok={fs['llm_verified']}/rej={fs['llm_rejected']}) "
            f"矛盾(detect={fs['contradictions_detected']}/confirm={fs['contradictions_confirmed']})"
        )
        logger.info(
            f"[{task_id[:8]}] 抽样事实: {_json.dumps(fs['sample_facts'][:10], ensure_ascii=False)}"
        )
    except Exception:
        logger.warning(f"[{task_id[:8]}] 事实验证统计记录失败", exc_info=True)

    # ── v0.9.5: 风格基线（4 维标签 → 预期区间，供离线对比） ──
    try:
        initial_style = state.get("config_style_profile") or state.get("style_profile") or {}
        style_baseline = {
            "emotion_intensity": initial_style.get("emotion_intensity", 50),
            "dialogue_ratio": initial_style.get("dialogue_ratio", 0.3),
            "sentence_preference": initial_style.get("sentence_preference", "balanced"),
            "sensory_density": initial_style.get("sensory_density", "medium"),
        }
        if style_baseline:
            bb.set(task_id, "style_baseline", style_baseline)
            logger.info(
                f"[{task_id[:8]}] 风格基线: {_json.dumps(style_baseline, ensure_ascii=False)}"
                f"（完成后运行 eval 对比漂移量）"
            )
    except Exception:
        logger.warning(f"[{task_id[:8]}] 风格基线保存失败", exc_info=True)

    # ── v0.9.3: 逐节延迟 p50/p95 ──
    try:
        section_timings = state.get("section_timings") or result.get("section_timings", []) if isinstance(result, dict) else []
        if section_timings:
            latencies = sorted([t["total_time_s"] for t in section_timings])
            n = len(latencies)
            p50 = latencies[int(n * 0.5)] if n > 0 else 0
            p95 = latencies[int(n * 0.95)] if n > 1 else latencies[-1] if n > 0 else 0
            avg_latency = sum(latencies) / n if n > 0 else 0
            bb.set(task_id, "section_timings", section_timings)
            logger.info(f"[{task_id[:8]}] 逐节延迟 (n={n}): avg={avg_latency:.1f}s p50={p50:.1f}s p95={p95:.1f}s")
    except Exception:
        logger.warning(f"[{task_id[:8]}] 逐节延迟统计失败", exc_info=True)

    # 写入黑板，前端可展示
    bb.set(task_id, "phase_timings", phase_timings)
    bb.set(task_id, "token_cost", {
        "total_tokens": total_tokens,
        "est_cost_usd": round(est_cost, 4),
        "total_time_s": round(total_time, 1),
        "by_agent": token_breakdown,
    })

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

    if provided.get("emotion_intensity"):
        bb.set(task_id, "style", provided)
        state["style_profile"] = provided
        _add_timeline(bb, task_id, "style", "system", "使用用户提供的风格参数")
        return state

    bb.set(task_id, "status", "analyzing_style")
    bb.xadd_event(task_id, {"event": "phase_change", "phase": "analyzing_style"})

    sa = StyleAnalyzer()
    style = sa.analyze(reference_text=state.get("config_reference_text", ""))
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

    style_summary = f"情感{style.get('emotion_intensity', 50)}/100 句长{style.get('sentence_preference', 'balanced')} 感官{style.get('sensory_density', 'medium')}" if isinstance(style, dict) else ""

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
    # 从 Redis 刷新大纲，获取用户最新编辑（subsection status 变更等）
    try:
        redis_outline = bb.get(task_id, "outline")
        if redis_outline and isinstance(redis_outline, list) and len(redis_outline) > 0:
            outline = redis_outline
            state["outline_v2"] = outline
    except Exception:
        logger.warning(f"[{task_id[:8]}] Redis 大纲刷新失败，使用本地缓存", exc_info=True)
    characters = state.get("characters") or []
    character_arcs = state.get("character_arcs") or []
    interactive = state.get("config_interactive", False)

    existing_draft = state.get("draft", {})
    existing_section_texts = state.get("section_texts", {})
    existing_handover = state.get("handover_chain", [])
    existing_backrefs = state.get("backref_suggestions", [])
    # 从 Redis 重建 WorldStateManager（state 序列化会丢失对象引用）
    event_graph = EventGraph(bb, task_id)
    # 本阶段每次进入都会从当前 character_arcs 全量重建里程碑。Celery 重试会从
    # characters 阶段整个重跑，弧线由 LLM 重新生成（措辞每次不同），若不先清空，
    # 每次尝试都会往图里追加一份同义改写，导致 pre_check 的必写事件列表按尝试
    # 次数线性膨胀，并被拼进 Writer prompt。清空后重建即幂等；逐字未变的里程碑
    # 通过 ledger 恢复 done/deviated 进度。
    arc_reset = event_graph.reset_arc_milestones()
    world_state = WorldStateManager(bb, task_id, event_graph=event_graph) if settings.ENABLE_WORLD_STATE else None
    # 将角色弧线里程碑注入 EventGraph
    arc_event_ids: dict[str, list[str]] = {}  # character_id -> [event_id, ...]
    section_event_ids: dict[int, list[str]] = {}  # section -> [event_id, ...]
    contract_version = resolve_contract_version(settings.CHARACTER_ARC_CONTRACT_VERSION)
    edge_count = 0

    if contract_version == "v2":
        # Old checkpoints are interpreted through a compatibility view only;
        # their stored character_arcs payload is not rewritten.
        character_arcs = normalize_v2_arcs(
            character_arcs,
            outline,
            legacy_unclassified_as_soft=True,
        )
        event_ids_by_milestone: dict[str, str] = {}
        for cid, ms in iter_v2_event_milestones(character_arcs):
            desc = ms.get("event", ms.get("description", ""))
            if not desc:
                continue
            eid = event_graph.add_arc_milestone(
                description=desc,
                section=ms.get("section", 0),
                subsection=ms.get("subsection", 0),
                character_id=cid,
                weight=9 if ms.get("requiredness") == "hard" else 3,
                classification=ms.get("classification", ""),
                requiredness=ms.get("requiredness", ""),
                contract_version="v2",
                source_id=ms.get("source_id", ""),
                source_hash=ms.get("source_hash", ""),
                rationale=ms.get("rationale", ""),
            )
            event_ids_by_milestone[str(ms.get("milestone_id", ""))] = eid
            arc_event_ids.setdefault(cid, []).append(eid)

        for edge in build_v2_edge_plan(character_arcs):
            from_event_id = event_ids_by_milestone.get(edge["from_milestone_id"])
            to_event_id = event_ids_by_milestone.get(edge["to_milestone_id"])
            if not from_event_id or not to_event_id:
                continue
            event_graph.link_events(from_event_id, to_event_id, metadata=edge)
            edge_count += 1
    else:
        for arc in (character_arcs or []):
            if isinstance(arc, dict) and arc.get("key_milestones"):
                cid = arc.get("character_id", "")
                eids: list[str] = []
                for ms in arc["key_milestones"]:
                    desc = ms.get("event", ms.get("description", ""))
                    if desc:
                        eid = event_graph.add_arc_milestone(
                            description=desc,
                            section=ms.get("section", 0), subsection=ms.get("subsection", 0),
                            character_id=cid,
                            weight=5,
                        )
                        eids.append(eid)
                        sec = ms.get("section", 0)
                        if sec:
                            section_event_ids.setdefault(sec, []).append(eid)
                if eids:
                    arc_event_ids[cid] = eids

        # v0.9.4 legacy behavior: same-character chain + same-section pairwise links.
        for eids in arc_event_ids.values():
            for i in range(len(eids) - 1):
                event_graph.link_events(eids[i], eids[i + 1])
                edge_count += 1
        for eids in section_event_ids.values():
            for i in range(len(eids)):
                for j in range(i + 1, len(eids)):
                    event_graph.link_events(eids[i], eids[j])
                    edge_count += 1
    restored_status = event_graph.restore_milestone_status(arc_reset["carried_status"])
    rebuilt = event_graph.get_summary()["arc_milestones_total"]
    logger.info(
        "arc_milestone_rebuild=%s",
        _json.dumps(
            {
                "section_scope": "all",
                "contract_version": contract_version,
                "removed_before_rebuild": arc_reset["removed"],
                "rebuilt_total": rebuilt,
                "status_carried_over": restored_status,
                "edge_count": edge_count,
                "idempotent": arc_reset["removed"] == 0 or rebuilt <= arc_reset["removed"],
                "production_effect": "arc_milestone_rebuild_only",
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    )
    if edge_count > 0:
        if contract_version == "v2":
            logger.info(f"[{task_id[:8]}] 创建 {edge_count} 条显式事件边 "
                        f"({len(arc_event_ids)} 个角色弧, contract=v2)")
        else:
            logger.info(f"[{task_id[:8]}] 创建 {edge_count} 条事件因果边 "
                        f"({len(arc_event_ids)} 个角色弧, {len(section_event_ids)} 个章节)")

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
        vector_store.enforce_task_limit(task_id)
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
    _accum = {
        "section_texts": {},
        "handover_notes": [],
        "backref_suggestions": [],
        "character_arcs": None,
        "character_state_propagation": None,
    }

    def on_section_done(
        section_num,
        section_texts=None,
        handover_notes=None,
        backref_suggestions=None,
        character_arcs=None,
        character_state_propagation=None,
    ):
        """交互模式：每节完成后保存检查点并挂起。"""
        _accum["section_texts"].update(section_texts or {})
        if handover_notes:
            _accum["handover_notes"] = handover_notes
        if backref_suggestions:
            _accum["backref_suggestions"] = backref_suggestions
        checkpoint_character_arcs = (
            copy_character_arcs(character_arcs)
            if is_valid_character_arcs(character_arcs)
            else copy_character_arcs(state.get("character_arcs") or [])
        )
        _accum["character_arcs"] = checkpoint_character_arcs
        if isinstance(character_state_propagation, dict):
            _accum["character_state_propagation"] = dict(character_state_propagation)
        state["draft"] = existing_draft
        state["section_texts"] = {str(k): v for k, v in _accum["section_texts"].items()}
        state["handover_chain"] = _accum["handover_notes"]
        state["backref_suggestions"] = _accum["backref_suggestions"]
        state["character_arcs"] = checkpoint_character_arcs
        if _accum["character_state_propagation"]:
            state["_character_state_propagation"] = dict(
                _accum["character_state_propagation"]
            )
        state["phase"] = "writing"
        bb.set(task_id, "character_arcs", copy_character_arcs(checkpoint_character_arcs))
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

        if state.get("style_evaluations"):
            bb.set(
                task_id,
                "style_evaluation_v1",
                state["style_evaluations"],
            )
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
            narrative_beats=state.get("narrative_beats"),
            reference_text=state.get("config_reference_text", ""),
        )
    except Exception as e:
        # Writer.run() 内部已有 per-subsection 错误处理，这里做最外层兜底
        logger.exception(f"[{task_id[:8]}] 写作阶段异常")
        bb.set(task_id, "status", "failed")
        bb.set(task_id, "error", f"写作阶段异常: {str(e)[:500]}")
        raise

    section_texts = result.get("section_texts", {})
    character_arcs, writer_propagation = _apply_writer_character_state(
        bb, task_id, state, result, character_arcs
    )
    logger.info(
        "character_state_propagation %s",
        _json.dumps(writer_propagation, ensure_ascii=True, sort_keys=True),
    )
    all_handover = result.get("handover_notes", [])
    all_backrefs = result.get("backref_suggestions", [])
    section_timings = result.get("section_timings", [])
    if section_timings:
        state["section_timings"] = section_timings
    state["style_evaluations"] = result.get(
        "style_evaluations", state.get("style_evaluations", [])
    )
    prior_policy_observations = state.get("style_policy_observations", [])
    new_policy_observations = result.get("style_policy_observations", [])
    policy_by_subsection = {
        (item.get("section"), item.get("subsection")): item
        for item in [*prior_policy_observations, *new_policy_observations]
        if isinstance(item, dict)
    }
    state["style_policy_observations"] = list(policy_by_subsection.values())

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
                        logger.warning(f"[{task_id[:8]}] 伏笔归档失败: {item[:40]}", exc_info=True)
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

    # v0.9.4: 矛盾检测统计埋点
    total_backrefs = len(backrefs)
    sections_with_backrefs = len(set(s.get("from_section") for s in backrefs if s.get("from_section")))

    fix_checklist = ce.run(backrefs, section_summaries)
    bb.set(task_id, "fix_checklist", fix_checklist)
    state["fix_checklist"] = fix_checklist

    critical_count = len(fix_checklist.get("critical_fixes", []))
    minor_count = len(fix_checklist.get("minor_fixes", []))
    _add_timeline(bb, task_id, "backref", "continuity_editor",
                  f"生成修正清单: {critical_count} 严重 + {minor_count} 轻微")

    # 执行 critical 修正
    fixes_applied = 0
    fixes_skipped = 0
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
                fixes_applied += 1
            else:
                fixes_skipped += 1
        state["section_texts"] = section_texts

    # 写入矛盾检测统计
    import json as _json
    contradiction_stats = {
        "total_backrefs": total_backrefs,
        "critical_fixes": critical_count,
        "minor_fixes": minor_count,
        "fixes_applied": fixes_applied,
        "fixes_skipped": fixes_skipped,
        "sections_with_backrefs": sections_with_backrefs,
    }
    bb.set(task_id, "contradiction_stats", _json.dumps(contradiction_stats, ensure_ascii=False))
    logger.info(f"[矛盾检测] 检出{total_backrefs}条 -> 严重{critical_count}/轻微{minor_count} -> 执行{fixes_applied}/跳过{fixes_skipped}")

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
    propagation = state.get("_character_state_propagation")
    if isinstance(propagation, dict):
        propagation = dict(propagation)
        propagation["reviewer_state_hash"] = character_arcs_hash(character_arcs)
        state["_character_state_propagation"] = propagation
        logger.info(
            "character_state_propagation %s",
            _json.dumps(propagation, ensure_ascii=True, sort_keys=True),
        )

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
    # v0.9.4: 间隔抽样审阅（每3节1次），减少 token 消耗
    sorted_sections = sorted(section_texts.keys())
    total_sections = len(sorted_sections)
    if total_sections > 6:
        sample_count = len([idx for idx in range(total_sections) if idx % 3 == 0])
        logger.info(f"[{task_id[:8]}] Reviewer 采样模式: 每3节1次, {sample_count}/{total_sections} 节参与审阅")
    else:
        logger.info(f"[{task_id[:8]}] Reviewer 全审模式: {total_sections} 节 ≤6, 全部审阅")
    skipped_sections = []
    for idx, i in enumerate(sorted_sections):
        if idx % 3 != 0 and total_sections > 6:
            skipped_sections.append(i)
            continue  # 跳过非采样节（但总节数≤6时全审）
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

    if skipped_sections:
        logger.info(f"[{task_id[:8]}] Reviewer 跳过 {len(skipped_sections)} 节: {skipped_sections}")
    logger.info(f"[{task_id[:8]}] Reviewer 实际审阅 {len(section_reviews)} 节")

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

    # ── v0.9.5: 量化指标（风格硬统计 / 伏笔健康 / 交接穿透率） ──
    assembled = "\n\n".join(section_texts.get(i, "") for i in sorted(section_texts.keys()))

    # 1. 风格硬统计
    try:
        from .style_stats import style_report
        style_metrics = style_report(assembled, style)
        review["style_metrics"] = style_metrics
        if style_metrics.get("deviation"):
            dev = style_metrics["deviation"]
            _add_timeline(bb, task_id, "review", "system",
                          f"风格偏差: {dev['verdict']} (总偏差={dev['total_deviation']})")
    except Exception:
        logger.warning(f"[{task_id[:8]}] 风格硬统计计算失败", exc_info=True)

    # 2. 伏笔健康度
    try:
        from .foreshadowing_store import get_foreshadowing_summary
        max_section = max(section_texts.keys()) if section_texts else 0
        fs_summary = get_foreshadowing_summary(task_id, max_section)
        review["foreshadowing_health"] = fs_summary
        _add_timeline(bb, task_id, "review", "system",
                      f"伏笔健康度: {fs_summary['health']} "
                      f"({fs_summary['resolved']}/{fs_summary['total']} 已回收, "
                      f"{fs_summary['broken']} 断裂)")
    except Exception:
        logger.warning(f"[{task_id[:8]}] 伏笔健康度检查失败", exc_info=True)

    # 3. 交接笔记穿透率
    try:
        from .handover_penetration import compute_handover_penetration
        hp_result = compute_handover_penetration(handover_chain, section_texts)
        review["handover_penetration"] = hp_result
        _add_timeline(bb, task_id, "review", "system",
                      f"交接穿透率: {hp_result['verdict']} "
                      f"({hp_result['total_hits']}/{hp_result['total_keywords']} "
                      f"= {hp_result['overall_penetration']:.0%})")
    except Exception:
        logger.warning(f"[{task_id[:8]}] 交接穿透率计算失败", exc_info=True)

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

        from .writing.state_frame_persistence import (
            history_for_checkpoint,
            merge_history_into_analysis,
        )
        existing_task = ts.get(task_id) or {}
        existing_analysis = existing_task.get("analysis_json")
        analysis_base = (
            dict(existing_analysis)
            if isinstance(existing_analysis, dict)
            else {}
        )
        current_analysis = state.get("analysis", {}) or {}
        if isinstance(current_analysis, dict):
            analysis_base.update(current_analysis)
        analysis = merge_history_into_analysis(
            analysis_base,
            history_for_checkpoint(bb, task_id),
        )
        from .writing.subsection_handover_persistence import (
            history_for_checkpoint as handover_history_for_checkpoint,
            merge_history_into_analysis as merge_handover_history_into_analysis,
        )
        analysis = merge_handover_history_into_analysis(
            analysis,
            handover_history_for_checkpoint(bb, task_id),
        )

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
            "analysis": analysis,
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
    style_summary = f"情感{style.get('emotion_intensity', 50)}/100 句长{style.get('sentence_preference', 'balanced')}" if isinstance(style, dict) else ""
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
