import re
import json
import logging
import time
import threading
import hashlib
from typing import Callable
from .base import BaseAgent
from .character_manager import CharacterManager
from .character_formatter import CharacterFormatter
from .context_manager import ContextManager
from ..utils.prompt_templates import (
    TARGETED_REVISE_PROMPT,
    HANDOVER_EXTRACTION_PROMPT,
    HANDOVER_EXTRACTION_PROMPT_V2,
    HANDOVER_EXTRACTION_PROMPT_V21,
    HANDOVER_EXTRACTION_PROMPT_V22,
    HANDOVER_EXTRACTION_PROMPT_V23,
)
from ..utils.word_counter import count_chinese_chars
from ..utils.style_brief import StyleSummarizer
from ..realization_policy import compile_realization_policy, render_realization_policy
from ..style_evaluation import StyleDriftTracker
from ..utils.json_parser import parse_json
from ..config import settings
from ..world_state import WorldStateManager
from ..narrative_event import EventGraph, rank_and_fill, format_events_for_prompt
from ..rule_checks import pre_check, post_check
from ..retrieval_observability import measure_retrieval_usage
from ..retrieval_pipeline import QueryPlanner, ShadowRetriever
from ..writing import (
    GenerationArtifact,
    GenerationController,
    AntiAIExpressionController,
    NarrativeRealityChecker,
    PromptBuilder,
    SceneSpecCanaryController,
    StateCommitter,
    build_character_state_propagation_event,
    character_arcs_hash,
    copy_character_arcs,
    is_valid_character_arcs,
    ShadowBoundaryValidationRunner,
    ShadowPostWriteExtractionRunner,
    SharedPostWriteExtractor,
    SubsectionInput,
    SubsectionGenerator,
    SubsectionPipeline,
    WriterExecutionContractController,
    compile_commercial_narrative_harness,
    compile_narrative_integrity,
    compile_world_pressure_contract,
    compose_narrative_control_context,
    harness_hash,
    narrative_integrity_hash,
    render_commercial_narrative_harness,
    render_narrative_integrity,
    render_world_pressure_contract,
    world_pressure_hash,
)
from .. import foreshadowing_store
from .. import rule_store
from ..writing.state_frame_persistence import StateFrameHistoryRecorder
from ..writing.subsection_handover_history import (
    HandoverExtractionObservation,
    observation_from_note,
    payload_for_persistence,
    task_id_hash as handover_task_id_hash,
)
from ..writing.subsection_handover_persistence import (
    SubsectionHandoverHistoryRecorder,
)
from ..writing.handover_contract_v2 import (
    HandoverContractValidatorV2,
    adapt_v2_to_legacy_handover_note,
    build_handover_sources,
    compile_next_boundary,
    render_v2_prompt_context,
    sha256_json as sha256_handover_json,
)
from ..writing.handover_contract_v21 import (
    HANDOVER_COMPACT_V21_MAX_OUTPUT_TOKENS,
    HANDOVER_COMPACT_V21_VERSION,
    HANDOVER_COMPACT_V22_MAX_OUTPUT_TOKENS,
    HANDOVER_COMPACT_V22_VERSION,
    HANDOVER_COMPACT_V23_MAX_OUTPUT_TOKENS,
    HANDOVER_COMPACT_V23_VERSION,
    build_compact_source_registry,
    render_v21_prompt_context,
    restore_and_validate_v21,
    restore_and_validate_v22,
    restore_and_validate_v23,
)
from ..utils.llm_client import estimate_tokens as estimate_llm_tokens

logger = logging.getLogger("writing_system.writer")

_HANDOVER_NEXT_BOUNDARY_VERSIONS = frozenset({"v2", "v2.1", "v2.2", "v2.3"})


def _estimate_prompt_tokens(text: str) -> int:
    """Stable local estimate used for context budgeting telemetry."""
    if not text:
        return 0
    chinese = len(re.findall(r"[\u4e00-\u9fff]", text))
    other = len(text) - chinese
    return int(chinese * 1.5 + other * 0.3)


def _narrative_density_instruction(density: float) -> str:
    """根据叙事密度值返回对应的写作策略指令。"""
    if density >= 0.8:
        return (
            "高密度叙事。请精密描写，动作逐步分解，信息密集不省略，"
            "关键数据给出具体数值。场景应有丰富的感官细节和内部逻辑。"
        )
    elif density >= 0.4:
        return (
            "中等密度叙事。适度留白——关键场景精细描写，过渡段落简洁推进。"
            "对话间保留呼吸感，数据只在关键时刻给出。"
        )
    else:
        return (
            "低密度叙事，冰山原则。动作只写结果不写过程，对话多用未完成句和潜台词，"
            "让留白本身传递信息。删掉一切可以删掉的词。"
        )


def _split_for_fallback(text: str, chunk_len: int = 40) -> list[str]:
    """将 fallback 文本按句子边界分割为小块，模拟流式输出。"""
    parts = re.split(r"(?<=[。！？.!?\n])", text)
    result = []
    for part in parts:
        if len(part) <= chunk_len:
            result.append(part)
        else:
            for i in range(0, len(part), chunk_len):
                result.append(part[i:i + chunk_len])
    return [p for p in result if p]


class Writer(BaseAgent):
    """撰稿人：继承制写作——每节站在前一节的肩膀上，传递交接笔记，发现矛盾时回溯修正。

    支持两种模式：
    - 批量模式 (stream_callback=None)：使用非流式 LLM 调用
    - 流式模式 (stream_callback 不为 None)：使用 streaming LLM，每收到 token 回调 stream_callback
    """

    def __init__(self):
        super().__init__(model=settings.WRITER_LLM_MODEL)

    def generate_subsection_candidate(self, **kwargs):
        """Canonical facade for one side-effect-free subsection generation.

        `run` remains the frozen legacy facade. Coordinator-owned canonical
        runtime code calls this seam and owns commit/projection sequencing.
        """

        post_validator = kwargs.pop(
            "post_validator", lambda draft: {"complete": True, "warnings": []}
        )
        generator = SubsectionGenerator(
            generation_controller=GenerationController(
                self.llm,
                character_violation_checker=self._check_character_violations,
                fallback_splitter=_split_for_fallback,
            ),
            handover_extractor=self._candidate_handover_extractor,
            post_validator=post_validator,
        )
        return generator.generate_subsection_candidate(**kwargs)

    def _candidate_handover_extractor(self, **kwargs):
        return self._extract_handover_with_observation(
            kwargs.pop("section_text"),
            kwargs.pop("section_num"),
            kwargs.pop("sub_num"),
            **kwargs,
        )

    def run(
        self,
        topic: str,
        style: dict,
        outline: list[dict],
        vector_store,
        blackboard,
        task_id: str,
        characters: list[dict] | None = None,
        character_arcs: list[dict] | None = None,
        stream_callback: Callable | None = None,
        interactive: bool = False,
        on_section_done: Callable | None = None,
        world_setting: str = "",
        prev_draft: str = "",
        prev_handover_list: list[dict] | None = None,
        existing_draft: dict[str, str] | None = None,
        existing_section_texts: dict[int, str] | None = None,
        world_state: WorldStateManager | None = None,
        event_graph: EventGraph | None = None,
        resume_context: dict | None = None,
        constraints: list[dict] | None = None,
        rules_context: str = "",
        subplot_context: str = "",
        relation_context: str = "",
        improvement_context: str = "",
        experience_context: str = "",
        narrative_beats: list[dict] | None = None,
        reference_text: str = "",
        rag_metadata_provider: Callable[[int, int], dict | None] | None = None,
    ) -> dict:
        """返回 {draft, handover_notes, backref_suggestions, section_texts}。

        Args:
            stream_callback: 流式回调 fn(token, section_num, sub_num, event_type) -> None
            interactive: 是否交互模式（每节完成后通过 on_section_done 等待确认）
            on_section_done: 交互模式下的节完成回调 fn(section_num) -> bool
                            返回 True 继续，False 停止
            world_setting: 世界观设定文本
            prev_draft: 续写模式下的前作文本（已切块入库，仅用于上下文注入）
            prev_handover_list: 续写模式下前作的交接笔记链
            existing_draft: 从检查点恢复的已完成小节 {sub_key: text}
            existing_section_texts: 从检查点恢复的已完成节 {section_num: text}
        """
        # v0.9.4: Token 归属标签
        from ..utils.llm_client import set_cost_label
        set_cost_label("writer")

        cm = ContextManager(self.llm)
        state_committer = StateCommitter()
        shadow_boundary_validator = self._build_shadow_boundary_validation_runner()
        shadow_post_write_extractor = self._build_shadow_post_write_extraction_runner(
            blackboard=blackboard,
            task_id=task_id,
        )
        state_frame_history = (
            StateFrameHistoryRecorder(blackboard, task_id)
            if blackboard is not None
            else None
        )
        subsection_handover_history = (
            SubsectionHandoverHistoryRecorder(blackboard, task_id)
            if blackboard is not None
            else None
        )
        scene_spec_canary = SceneSpecCanaryController(
            mode=settings.WRITER_SCENE_SPEC_MODE,
            canary_task_ids=settings.WRITER_SCENE_SPEC_CANARY_TASK_IDS,
        )
        execution_contract_controller = WriterExecutionContractController(
            mode=settings.WRITER_EXECUTION_CONTRACT_MODE,
        )
        if resume_context:
            cm.deserialize(resume_context)
        character_arcs = copy_character_arcs(character_arcs or [])
        character_state_propagation = build_character_state_propagation_event(
            task_id=task_id,
            section=None,
            subsection=None,
            source="legacy_input_fallback",
            input_state_hash=character_arcs_hash(character_arcs),
            updated_state_hash=character_arcs_hash(character_arcs),
            update_applied=False,
            fallback_reason="no_character_state_update",
            checkpoint_version=state_committer.CHECKPOINT_VERSION,
        )
        full_draft = ""
        handover_notes = []
        backref_suggestions = []
        section_timings = []  # 逐节计时: [{section, subsection, llm_time_s, total_time_s, char_count}]
        section_texts = dict(existing_section_texts) if existing_section_texts else {}
        previous_sub_texts = []  # P2: 累积已生成的小节正文，用于重复检测
        existing_draft = existing_draft or {}
        style_control_mode = settings.WRITER_STYLE_CONTROL_MODE
        style_policy_observations: list[dict] = []
        anti_ai_expression_controller = AntiAIExpressionController(
            settings.WRITER_ANTI_AI_EXPRESSION_MODE
        )
        anti_ai_expression_observations: list[dict] = []
        commercial_harness_mode = settings.WRITER_COMMERCIAL_HARNESS_MODE
        if commercial_harness_mode not in {"off", "shadow", "canary"}:
            commercial_harness_mode = "shadow"
        commercial_harness_observations: list[dict] = []
        narrative_integrity_mode = settings.WRITER_NARRATIVE_INTEGRITY_MODE
        if narrative_integrity_mode not in {"off", "shadow", "canary"}:
            narrative_integrity_mode = "shadow"
        narrative_integrity_observations: list[dict] = []
        world_pressure_mode = settings.WRITER_WORLD_PRESSURE_MODE
        if world_pressure_mode not in {"off", "shadow", "canary"}:
            world_pressure_mode = "shadow"
        world_pressure_observations: list[dict] = []
        narrative_reality_checker = NarrativeRealityChecker(
            enabled=settings.WRITER_NARRATIVE_REALITY_CHECKS,
            allowed_names=[
                str(item.get("name", ""))
                for item in (characters or [])
                if isinstance(item, dict) and item.get("name")
            ],
        )
        style_drift_tracker = StyleDriftTracker(
            style,
            character_names=[
                str(item.get("name", ""))
                for item in (characters or [])
                if isinstance(item, dict) and item.get("name")
            ],
        )
        if blackboard:
            try:
                previous_style_evaluations = blackboard.get(
                    task_id, "style_evaluation_v1"
                )
                if isinstance(previous_style_evaluations, str):
                    previous_style_evaluations = json.loads(
                        previous_style_evaluations
                    )
                style_drift_tracker.reports.extend(
                    item
                    for item in (previous_style_evaluations or [])
                    if isinstance(item, dict)
                )
            except Exception:
                logger.warning(
                    f"[{task_id[:8]}] 历史风格评测恢复失败，fallback=current-run-only",
                    exc_info=True,
                )

        prev_handover = None
        if prev_handover_list:
            handover_notes = list(prev_handover_list)
            prev_handover = prev_handover_list[-1] if prev_handover_list else None

        narrative_density = style.get("narrative_density", 0.7) if isinstance(style, dict) else 0.7
        density_instruction = _narrative_density_instruction(narrative_density)
        style_structured = StyleSummarizer.for_writer(style) if isinstance(style, dict) else ""

        # 用 LLM 将模糊风格参数翻译为具体行为指令（一次调用，全任务复用）
        style_behavior_text = ""
        if (
            style
            and isinstance(style, dict)
            and settings.ENABLE_STYLE_BEHAVIOR
            and style_control_mode in {"legacy", "shadow"}
        ):
            try:
                import json as _json
                from ..utils.prompt_templates import STYLE_BEHAVIOR_PROMPT
                behavior_prompt = STYLE_BEHAVIOR_PROMPT.format(style_params=_json.dumps(style, ensure_ascii=False, indent=2))
                style_behavior_text = self.llm.chat_completion(
                    [{"role": "user", "content": behavior_prompt}],
                    temperature=0.3, max_tokens=600, prompt_name="style_behavior"
                )
                logger.info(f"[{task_id[:8]}] 风格行为指令已生成 ({len(style_behavior_text)} 字)")
            except Exception:
                logger.warning(f"[{task_id[:8]}] 风格行为指令生成失败，跳过", exc_info=True)

        # v0.9.4: 构建风格示例文本（参数→模板，照猫画虎）
        style_examples = ""
        if (
            style
            and isinstance(style, dict)
            and settings.ENABLE_STYLE_BEHAVIOR
            and style_control_mode in {"legacy", "shadow"}
        ):
            try:
                from ..utils.style_mapping import build_style_examples
                style_examples = build_style_examples(style)
                if style_examples:
                    logger.info(f"[{task_id[:8]}] 风格示例已生成 ({len(style_examples)} 字)")
            except Exception:
                logger.warning(f"[{task_id[:8]}] 风格示例生成失败，跳过", exc_info=True)

        # v0.9.5: 参考原文 few-shot（比预写示例更强的风格信号）
        reference_passages = ""
        if (
            reference_text
            and reference_text.strip()
            and style_control_mode in {"legacy", "shadow"}
        ):
            import re as _re
            paras = _re.split(r'\n{2,}', reference_text.strip())
            paras = [p.strip() for p in paras if len(p.strip()) > 80]
            selected = []
            n = len(paras)
            if n > 0:
                indices = set()
                indices.add(0)
                if n > 1: indices.add(n - 1)
                if n > 3: indices.add(n // 2)
                for i in [2, n - 2, n // 4, 3 * n // 4]:
                    if 0 <= i < n and len(indices) < 5:
                        indices.add(i)
                selected = [paras[i] for i in sorted(indices)]
            if selected:
                reference_passages = "## 风格参考原文（请模仿以下段落的句法节奏、对话风格和用词习惯，照猫画虎）\n\n"
                for i, p in enumerate(selected, 1):
                    reference_passages += f"### 参考段落 {i}\n{p}\n\n"
                logger.info(f"[{task_id[:8]}] 参考原文 few-shot: {len(selected)} 段 ({sum(len(p) for p in selected)} 字)")

        # v0.9.4: 构建节拍查找表 (section, subsection) -> {intensity, character_focus}
        beat_lookup: dict[tuple, dict] = {}
        if narrative_beats:
            for b in narrative_beats:
                key = (b.get("section", 0), b.get("subsection", 0))
                beat_lookup[key] = b

        # P0 预检: 大纲是否包含足够的关键事件
        total_kp = sum(len(sub.get("key_points", [])) for sec in outline for sub in sec.get("subsections", []))
        total_desc = sum(1 for sec in outline for sub in sec.get("subsections", []) if sub.get("description"))
        if total_kp == 0 and total_desc == 0:
            logger.warning(f"[{task_id[:8]}] 大纲缺少 key_points 和 description，"
                          f"将从标题自动生成约束（约束力较弱）。建议为每个大纲节点添加关键事件。")

        sec_idx = 0
        while sec_idx < len(outline):
            sec = outline[sec_idx]
            section_num = sec.get("section", 0)
            section_title = sec.get("title", "")

            # P10: 每节前检查大纲是否有更新（支持边改大纲边写正文）
            if blackboard and section_num > 1 and sec_idx > 0:
                try:
                    updated = blackboard.get(task_id, "outline")
                    if updated and isinstance(updated, list):
                        # 检测新增节
                        existing_sections = {s.get("section") for s in outline}
                        new_sections = 0
                        for new_sec in updated:
                            ns = new_sec.get("section", 0)
                            if ns not in existing_sections:
                                outline.append(new_sec)
                                existing_sections.add(ns)
                                new_sections += 1
                        # 检测已有节的 subsection status 变更（用户手动设置 done/queued）
                        status_changes = 0
                        for cur_sec in outline:
                            cs = cur_sec.get("section", 0)
                            updated_sec = next((u for u in updated if u.get("section") == cs), None)
                            if not updated_sec:
                                continue
                            cur_subs = {s.get("subsection"): s.get("status", "queued")
                                       for s in cur_sec.get("subsections", [])}
                            upd_subs = {s.get("subsection"): s.get("status", "queued")
                                       for s in updated_sec.get("subsections", [])}
                            for sub_id, new_status in upd_subs.items():
                                old_status = cur_subs.get(sub_id)
                                if old_status and old_status != new_status:
                                    for sub in cur_sec.get("subsections", []):
                                        if sub.get("subsection") == sub_id:
                                            sub["status"] = new_status
                                            status_changes += 1
                                            break
                        if new_sections or status_changes:
                            logger.info(f"[{task_id[:8]}] 大纲更新: +{new_sections}节, "
                                       f"{status_changes}处subsection状态变更")
                except Exception:
                    logger.warning(f"[{task_id[:8]}] 大纲动态更新失败，继续使用原大纲", exc_info=True)
            subsections = sec.get("subsections", [])
            n_subs = len(subsections)

            # 从检查点恢复：跳过已完成的节
            first_sub_key = f"{section_num}-1"
            if first_sub_key in existing_draft and existing_draft.get(first_sub_key, "").strip():
                logger.info(f"[{task_id[:8]}] 跳过已完成: 第{section_num}节")
                section_text = section_texts.get(section_num, "")
                full_draft += section_text
                sec_idx += 1
                continue

            logger.info(f"[{task_id[:8]}] 开始写第{section_num}节「{section_title}」({n_subs} 小节)")

            section_outline = (
                f"第{section_num}节「{section_title}」"
                f"—— 要点：{'、'.join(sec.get('key_points', []))}"
            )

            section_text = f"第{section_num}节：{section_title}\n\n"
            section_handover_parts = []
            should_stop = False

            for sub in subsections:
                sub_num = sub.get("subsection", 0)
                sub_key = f"{section_num}-{sub_num}"

                # 从检查点恢复：跳过已完成的小节
                if sub_key in existing_draft:
                    sub_text = existing_draft[sub_key]
                    section_text += f"【{sub.get('title', '')}】\n{sub_text}\n\n"
                    previous_sub_texts.append(sub_text)  # P2: 恢复的文本也加入
                    continue

                sub_title = sub.get("title", "")
                # P11: done=已写完跳过；draft=断点墙，遇到即停止整个写作
                sub_status = sub.get("status", "draft")
                if sub_status == "done":
                    logger.info(f"[{task_id[:8]}] 跳过 done: 第{section_num}.{sub_num}小节")
                    continue
                if sub_status == "draft":
                    logger.info(f"[{task_id[:8]}] 遇到 draft 断点: 第{section_num}.{sub_num}小节，停止写作")
                    if blackboard:
                        blackboard.set(task_id, "status", "awaiting_queue")
                        blackboard.set(task_id, "progress", f"遇到断点: 第{section_num}.{sub_num}小节为draft，等待用户切换")
                        blackboard.xadd_event(task_id, {
                            "event": "awaiting_queue",
                            "section": section_num,
                            "subsection": sub_num,
                            "message": f"已写到第{section_num}.{sub_num}小节，后续为draft断点"
                        })
                    should_stop = True
                    break
                # B2: 子节前检查是否被用户停止
                if blackboard and blackboard.get(task_id, "status") == "stopped":
                    logger.info(f"[{task_id[:8]}] 检测到停止信号，中断写作")
                    should_stop = True
                    break
                key_points = sub.get("key_points", [])
                if not key_points and not sub.get("description"):
                    if sub_title and sub_title not in ("新节点", "新章", "新卷"):
                        key_points = [sub_title]
                target_words = sub.get("target_words", 2000)
                sub_desc = sub.get("description", "")
                call_max_tokens = min(max(settings.WRITER_MAX_TOKENS_FLOOR, target_words * 4),
                                     settings.WRITER_MAX_TOKENS_CEIL)

                t_sub_start = time.time()
                # --- 进度 ---
                if blackboard:
                    blackboard.set(task_id, "progress",
                        f"第{section_num}节第{sub_num}/{len(subsections)}小节")

                # --- RAG 检索 (v0.9.1: 语义召回 + 因果扩展) ---
                query = ""
                retrieval_trace = {
                    "query": "",
                    "filter": {"task_id": task_id},
                    "elapsed_ms": 0.0,
                    "candidate_count": 0,
                    "returned_count": 0,
                    "candidates": [],
                    "disabled": not settings.ENABLE_RAG,
                }
                phase3_shadow = {
                    "enabled": settings.RAG_PHASE3_SHADOW,
                    "mode": "shadow",
                    "writer_uses": "legacy",
                    "skipped": not settings.RAG_PHASE3_SHADOW,
                }
                if not settings.ENABLE_RAG:
                    retrieved_items = []
                    causal_events = []
                else:
                    q_parts = [topic, section_title]
                    if sub_title != section_title:
                        q_parts.append(sub_title)
                    q_parts.extend(kp for kp in key_points if kp not in q_parts)
                    query = ' '.join(q_parts)
                    retrieved_items = vector_store.search_with_meta(
                        query,
                        k=settings.RAG_TOP_K,
                        task_id=task_id,
                        candidate_k=settings.RAG_TRACE_CANDIDATE_K or None,
                    )
                    retrieval_trace = vector_store.last_search_trace
                    if settings.RAG_PHASE3_SHADOW:
                        try:
                            character_names = []
                            for character in characters or []:
                                if isinstance(character, dict):
                                    name = str(character.get("name", "")).strip()
                                else:
                                    name = str(getattr(character, "name", "")).strip()
                                if name:
                                    character_names.append(name)
                            phase3_plan = QueryPlanner(
                                max_queries=settings.RAG_PHASE3_MAX_QUERIES
                            ).plan(
                                topic=topic,
                                section_title=section_title,
                                subsection_title=sub_title,
                                key_points=key_points,
                                description=sub_desc,
                                character_names=character_names,
                                current_section=section_num,
                                current_subsection=sub_num,
                            )
                            phase3_shadow = ShadowRetriever(
                                candidate_k=settings.RAG_PHASE3_CANDIDATE_K,
                                min_score=settings.RAG_PHASE3_MIN_SCORE,
                                max_results=settings.RAG_TOP_K,
                            ).run(vector_store, phase3_plan, task_id=task_id)
                            phase3_shadow["enabled"] = True
                            phase3_shadow["skipped"] = False
                        except Exception as exc:
                            phase3_shadow = {
                                "enabled": True,
                                "mode": "shadow",
                                "writer_uses": "legacy",
                                "skipped": True,
                                "error": type(exc).__name__,
                            }
                            logger.warning(
                                f"[{task_id[:8]}] Phase 3 shadow 检索失败 "
                                f"(第{section_num}.{sub_num}节)，Writer 继续使用旧检索",
                                exc_info=True,
                            )
                    # 因果扩展
                    causal_events = []
                    if event_graph and retrieved_items:
                        try:
                            sections = {item["section"] for item in retrieved_items}
                            semantic_events = event_graph.get_events_by_sections(sections)
                            causal_events = event_graph.expand_causal(semantic_events)
                            causal_events = [e for e in causal_events
                                             if e.section not in sections and e.section < section_num]
                        except Exception:
                            logger.warning(
                                f"[{task_id[:8]}] 因果扩展失败 (第{section_num}.{sub_num}节)，"
                                f"回退到纯语义检索", exc_info=True
                            )
                retrieved_context = ""
                if retrieved_items:
                    retrieved_context = "已写段落参考（以下段落与当前章节语义相关，供风格和情节参照）：\n"
                    for i, item in enumerate(retrieved_items, 1):
                        src = ""
                        if item.get("title"):
                            src = f"第{item['section']}节 · {item['title']}"
                        else:
                            src = f"第{item['section']}.{item['subsection']}小节"
                        retrieved_context += f"\n### 参考 {i}：{src}\n{item['text']}\n"
                # 因果事件追加到参考末尾
                if causal_events:
                    retrieved_context += "\n---\n以下事件与当前章节存在因果关联（语义检索漏掉但剧情逻辑相关）：\n"
                    for evt in causal_events:
                        retrieved_context += f"[因果关联] 第{evt.section}章 · {evt.description}\n"

                # --- v0.9.2: RAG 召回日志（供离线抽样评估） ---
                try:
                    if not blackboard:
                        raise RuntimeError("blackboard unavailable")
                    rag_log = blackboard.get(task_id, "rag_recall_log")
                    if isinstance(rag_log, str):
                        import json as _json
                        rag_log = _json.loads(rag_log)
                    rag_log = rag_log if isinstance(rag_log, list) else []
                    rag_log.append({
                        "section": section_num, "subsection": sub_num,
                        "query": query[:120],
                        "semantic_items": [{
                            "id": item.get("id", ""),
                            "rank": item.get("rank", index),
                            "section": item["section"],
                            "subsection": item.get("subsection", 0),
                            "title": item.get("title", "")[:60],
                            "text": item.get("text", "")[:200],
                            "distance": item.get("distance"),
                            "score": item.get("score"),
                            "metadata": item.get("metadata", {}),
                        } for index, item in enumerate(retrieved_items, 1)] if retrieved_items else [],
                        "semantic_sections": [item["section"] for item in retrieved_items] if retrieved_items else [],
                        "causal_sections": [e.section for e in causal_events] if causal_events else [],
                        "retrieval_trace": retrieval_trace,
                        "phase3_shadow": phase3_shadow,
                        "writer_usage": [],
                    })
                    blackboard.set(task_id, "rag_recall_log", rag_log)
                except Exception:
                    logger.warning(f"[{task_id[:8]}] RAG 召回日志写入失败", exc_info=True)

                # --- 角色上下文 ---
                character_context = CharacterFormatter.build_context(characters, character_arcs)
                arc_context = CharacterFormatter.build_arc_context(
                    characters, character_arcs,
                    section=section_num, subsection=sub_num,
                )

                # --- 交接笔记上下文 (v0.9.1: JSON -> NL brief) ---
                handover_context = Writer._build_handover_brief(
                    prev_handover if prev_handover else {},
                    llm_client=self.llm,
                )

                summary_context = cm.get_summary()

                # --- 世界状态 ---
                world_facts_str = "（无）"
                world_contradictions_str = "（无）"
                if world_state and settings.ENABLE_WORLD_STATE:
                    keywords = [topic, section_title, sub_title] + key_points
                    facts = world_state.query_relevant(keywords, section_num, top_k=8)
                    if facts:
                        world_facts_str = "\n".join(
                            f"- [{f['category']}] {f['fact']}" + (" ⚠矛盾" if f.get("contradiction_of") else "")
                            for f in facts
                        )
                    warnings = world_state.consume_warnings()
                    if warnings:
                        world_contradictions_str = "\n".join(warnings)

                # --- 事件上下文（rank_and_fill 排序） ---
                ranked_events_str = "（无特殊事件）"
                required_events = []
                if event_graph:
                    relevant = event_graph.query_relevant(section_num, sub_num)
                    if relevant:
                        ranked_events_str = format_events_for_prompt(relevant)
                    # 提取本节必须体现的弧线事件
                    rc = pre_check(event_graph, section_num, sub_num)
                    required_events = rc["required"]
                    if rc["prompt_text"]:
                        ranked_events_str = rc["prompt_text"] + "\n" + ranked_events_str

                # --- 统一约束上下文 (P5b+P5c) ---
                # 合并: 全局规则 + 章节约束 + 伏笔 → 单一 rules_context 块
                parts = []

                # 1. 全局规则 (LOCKED级)
                global_rules = rules_context or rule_store.build_rules_context()
                if global_rules:
                    parts.append(global_rules)

                # 2. 章节约束 (LOCKED级: 来自StorylineConstraint，合并入规则块)
                if constraints:
                    chapter_constraints = [
                        c for c in constraints
                        if c.get("source_chapter") == section_num and c.get("status") == "active"
                    ]
                    if chapter_constraints:
                        sorted_c = sorted(chapter_constraints, key=lambda c: c.get("priority", 5), reverse=True)
                        lines = ["## 本章硬性约束", "以下约束必须在当前章节中遵守："]
                        for i, c in enumerate(sorted_c, 1):
                            tag = "【LOCKED】" if c.get("priority", 5) >= 8 else "【RECOMMENDED】"
                            lines.append(f"{i}. {tag} {c.get('description', '')}")
                        parts.append("\n".join(lines))

                # 3. 伏笔上下文 (RECOMMENDED级)
                if task_id:
                    fs_ctx = foreshadowing_store.build_foreshadowing_context(task_id, section_num)
                    if fs_ctx:
                        parts.append(fs_ctx)

                # 4. 世界元素上下文 (RECOMMENDED级) — 势力/地图/物品
                if task_id:
                    try:
                        from ..faction_store import build_faction_context
                        fc_ctx = build_faction_context(task_id, section_num)
                        if fc_ctx: parts.append(fc_ctx)
                    except Exception:
                        logger.warning(
                            f"[{task_id[:8]}] 势力上下文加载失败 (第{section_num}.{sub_num}节)，fallback=skip",
                            exc_info=True,
                        )
                    try:
                        from ..map_manager import build_location_context
                        lc_ctx = build_location_context(task_id)
                        if lc_ctx: parts.append(lc_ctx)
                    except Exception:
                        logger.warning(
                            f"[{task_id[:8]}] 地图上下文加载失败 (第{section_num}.{sub_num}节)，fallback=skip",
                            exc_info=True,
                        )
                    try:
                        from ..item_manager import build_item_context
                        ic_ctx = build_item_context(task_id)
                        if ic_ctx: parts.append(ic_ctx)
                    except Exception:
                        logger.warning(
                            f"[{task_id[:8]}] 物品上下文加载失败 (第{section_num}.{sub_num}节)，fallback=skip",
                            exc_info=True,
                        )

                rules_ctx = "\n\n".join(parts) if parts else ""

                # 5. 支线上下文 + 关系上下文 + 改进目标 + 长期记忆 (写作指引级，软约束)
                soft_parts = []
                if experience_context:
                    soft_parts.append(experience_context)
                if improvement_context:
                    soft_parts.append("## 前次审阅改进目标\n\n" + improvement_context)
                if subplot_context:
                    soft_parts.append(subplot_context)
                if relation_context:
                    soft_parts.append(relation_context)
                if soft_parts:
                    soft_ctx = "\n\n".join(soft_parts)
                    rules_ctx = rules_ctx + "\n\n" + soft_ctx if rules_ctx else soft_ctx

                # --- 构建硬约束：强制事件 + 进度追踪 ---
                mandatory_events = self._build_mandatory_events(
                    key_points=key_points,
                    section_key_points=sec.get("key_points", []),
                    sub_desc=sub_desc,
                    section_num=section_num,
                    sub_num=sub_num,
                )
                execution_required_events = self._collect_mandatory_event_sources(
                    key_points=key_points,
                    section_key_points=sec.get("key_points", []),
                    sub_desc=sub_desc,
                    section_num=section_num,
                    sub_num=sub_num,
                )
                progress_context = self._build_progress_context(
                    outline=outline,
                    current_section=section_num,
                    current_sub=sub_num,
                    n_subs=n_subs,
                    key_points=key_points,
                    sub_desc=sub_desc,
                )

                # --- 风格硬约束 ---
                style_constraints = ""
                if style_behavior_text:
                    style_constraints = "【风格硬约束 - 必须严格遵循】\n" + style_behavior_text

                # --- v0.9.4: 节拍风格提醒 ---
                beat_reminder = ""
                beat = beat_lookup.get((section_num, sub_num))
                if beat:
                    intensity = beat.get("intensity", 5)
                    focus = beat.get("character_focus", "")
                    # 叙事节奏标签——区别于全局 style.emotion_intensity（控制用词风格）
                    rhythm_label = ("铺垫/过渡" if intensity <= 4 else
                                    "日常/推进" if intensity <= 6 else
                                    "冲突/升温" if intensity <= 8 else "高潮/爆发")
                    beat_reminder = (
                        f"【叙事节奏】本节在故事弧线中的位置: {intensity}/10 ({rhythm_label})。"
                        f"这影响的是事件密度和张力走向，而非用词风格。"
                    )
                    if focus:
                        beat_reminder += f" 本节的叙事重心是: {focus}。"

                realization_policy = compile_realization_policy(style, beat=beat)
                rendered_realization_policy = render_realization_policy(
                    realization_policy
                )
                policy_observation = {
                    "section": section_num,
                    "subsection": sub_num,
                    "mode": style_control_mode,
                    "version": realization_policy.version,
                    "policy_hash": hashlib.sha256(
                        rendered_realization_policy.encode("utf-8")
                    ).hexdigest(),
                    "characters": len(rendered_realization_policy),
                    "injected": style_control_mode == "policy",
                }
                style_policy_observations.append(policy_observation)
                if blackboard:
                    try:
                        blackboard.set(
                            task_id,
                            "style_policy_observations_v1",
                            style_policy_observations,
                        )
                    except Exception:
                        logger.warning(
                            f"[{task_id[:8]}] Realization Policy shadow记录失败 "
                            f"(第{section_num}.{sub_num}节)，fallback=return-only",
                            exc_info=True,
                        )

                # --- 确定性 Prompt 边界（R1：字段和值保持原样） ---
                base_style_context = (
                    rendered_realization_policy
                    if style_control_mode == "policy"
                    else (reference_passages + "\n" + style_examples).strip()
                )
                commercial_harness = compile_commercial_narrative_harness(
                    scene_text="\n".join(
                        [sub_desc, *(str(item) for item in (key_points or []))]
                    ),
                    required_events=execution_required_events,
                )
                rendered_commercial_harness = render_commercial_narrative_harness(
                    commercial_harness
                )
                integrity_policy = compile_narrative_integrity(
                    required_events=execution_required_events,
                )
                rendered_integrity_policy = render_narrative_integrity(
                    integrity_policy
                )
                world_pressure_contract = compile_world_pressure_contract(
                    settings.WRITER_WORLD_PRESSURE_PRESET
                )
                rendered_world_pressure = (
                    render_world_pressure_contract(world_pressure_contract)
                    if world_pressure_contract is not None
                    else ""
                )
                active_integrity_parts: list[str] = []
                if narrative_integrity_mode == "canary":
                    active_integrity_parts.append(rendered_integrity_policy)
                if (
                    world_pressure_mode == "canary"
                    and rendered_world_pressure
                ):
                    active_integrity_parts.append(rendered_world_pressure)
                effective_integrity_constraints = "\n\n".join(
                    active_integrity_parts
                )
                effective_style_context = compose_narrative_control_context(
                    integrity_context="",
                    integrity_mode="shadow",
                    genre_context=rendered_commercial_harness,
                    genre_mode=commercial_harness_mode,
                    style_context=base_style_context,
                )
                anti_ai_expression_constraints = (
                    anti_ai_expression_controller.final_prompt_constraints()
                )
                if anti_ai_expression_controller.mode != "off":
                    anti_ai_observation = anti_ai_expression_controller.observation(
                        section=section_num,
                        subsection=sub_num,
                    )
                    anti_ai_expression_observations.append(anti_ai_observation)
                    if blackboard:
                        try:
                            blackboard.set(
                                task_id,
                                "anti_ai_expression_kernel_v0",
                                anti_ai_expression_observations,
                            )
                        except Exception:
                            logger.warning(
                                f"[{task_id[:8]}] Anti-AI Expression observation failed "
                                f"(section={section_num}.{sub_num}, fallback=return-only)",
                                exc_info=True,
                            )
                integrity_observation = {
                    "section": section_num,
                    "subsection": sub_num,
                    "mode": narrative_integrity_mode,
                    "version": integrity_policy.version,
                    "policy_hash": narrative_integrity_hash(integrity_policy),
                    "characters": len(rendered_integrity_policy),
                    "required_event_count": integrity_policy.required_event_count,
                    "source_refs": list(integrity_policy.source_refs),
                    "injected": narrative_integrity_mode == "canary",
                    "delivery": (
                        "hard_constraints"
                        if narrative_integrity_mode == "canary"
                        else "shadow"
                    ),
                }
                narrative_integrity_observations.append(integrity_observation)
                if blackboard and narrative_integrity_mode != "off":
                    try:
                        blackboard.set(
                            task_id,
                            "narrative_integrity_observations_v0",
                            narrative_integrity_observations,
                        )
                    except Exception:
                        logger.warning(
                            f"[{task_id[:8]}] Narrative Integrity observation failed "
                            f"(section={section_num}.{sub_num}, fallback=return-only)",
                            exc_info=True,
                        )
                if world_pressure_contract is not None:
                    world_pressure_observation = {
                        "section": section_num,
                        "subsection": sub_num,
                        "mode": world_pressure_mode,
                        "version": world_pressure_contract.version,
                        "preset": world_pressure_contract.preset,
                        "contract_hash": world_pressure_hash(world_pressure_contract),
                        "characters": len(rendered_world_pressure),
                        "world_setting_present": bool(world_setting.strip()),
                        "injected": world_pressure_mode == "canary",
                        "delivery": (
                            "hard_constraints"
                            if world_pressure_mode == "canary"
                            else "shadow"
                        ),
                    }
                    world_pressure_observations.append(world_pressure_observation)
                    if blackboard and world_pressure_mode != "off":
                        try:
                            blackboard.set(
                                task_id,
                                "world_pressure_observations_v0",
                                world_pressure_observations,
                            )
                        except Exception:
                            logger.warning(
                                f"[{task_id[:8]}] World Pressure observation failed "
                                f"(section={section_num}.{sub_num}, fallback=return-only)",
                                exc_info=True,
                            )
                commercial_observation = {
                    "section": section_num,
                    "subsection": sub_num,
                    "mode": commercial_harness_mode,
                    "version": commercial_harness.version,
                    "harness_hash": harness_hash(commercial_harness),
                    "characters": len(rendered_commercial_harness),
                    "scene_mode": commercial_harness.scene_mode,
                    "required_event_count": commercial_harness.required_event_count,
                    "source_refs": list(commercial_harness.source_refs),
                    "classification_evidence": commercial_harness.classification_evidence,
                    "injected": commercial_harness_mode == "canary",
                }
                commercial_harness_observations.append(commercial_observation)
                if blackboard and commercial_harness_mode != "off":
                    try:
                        blackboard.set(
                            task_id,
                            "commercial_narrative_harness_v0",
                            commercial_harness_observations,
                        )
                    except Exception:
                        logger.warning(
                            f"[{task_id[:8]}] Commercial Harness observation failed "
                            f"(section={section_num}.{sub_num}, fallback=return-only)",
                            exc_info=True,
                        )

                prompt_values = {
                    "mandatory_events": mandatory_events,
                    "character_constraints": self._build_character_constraints(characters),
                    "style_constraints": style_constraints,
                    "narrative_integrity_constraints": effective_integrity_constraints,
                    "progress_context": progress_context,
                    "rules_context": rules_ctx if rules_ctx else "",
                    "topic": topic,
                    "section": section_num,
                    "subsection": sub_num,
                    "subsection_title": sub_title,
                    "section_outline": section_outline,
                    "key_points": "、".join(key_points),
                    "sub_description": sub_desc if sub_desc else "（按大意自由发挥）",
                    "world_setting": world_setting if world_setting.strip() else "",
                    "world_facts": world_facts_str,
                    "world_contradictions": world_contradictions_str,
                    "style_structured": style_structured,
                    "narrative_density_instruction": density_instruction,
                    "ranked_events": ranked_events_str,
                    "emotion_intensity": style.get("emotion_intensity", 50) if isinstance(style, dict) else 50,
                    "sentence_preference": style.get("sentence_preference", "balanced") if isinstance(style, dict) else "balanced",
                    "sensory_density": style.get("sensory_density", "medium") if isinstance(style, dict) else "medium",
                    "dialogue_ratio": int((style.get("dialogue_ratio", 0.2) if isinstance(style, dict) else 0.2) * 100),
                    "character_context": character_context,
                    "arc_context": arc_context,
                    "handover_context": handover_context,
                    "summary_context": summary_context if summary_context else "（故事开头）",
                    "retrieved_context": retrieved_context if retrieved_context else "（无相关段落）",
                    "target_words": target_words,
                    "beat_reminder": beat_reminder,
                    "style_examples": effective_style_context,
                    "anti_ai_expression_constraints": anti_ai_expression_constraints,
                }
                context_token_estimates = {
                    "outline": _estimate_prompt_tokens(
                        f"{section_outline}\n{sub_desc}\n{'、'.join(key_points)}"
                    ),
                    "rules": _estimate_prompt_tokens(
                        rules_ctx + "\n" + effective_integrity_constraints
                    ),
                    "characters": _estimate_prompt_tokens(character_context + "\n" + arc_context),
                    "handover": _estimate_prompt_tokens(handover_context),
                    "recent_summary": _estimate_prompt_tokens(summary_context),
                    "rag": _estimate_prompt_tokens(retrieved_context),
                    "world": _estimate_prompt_tokens(
                        world_setting + "\n" + world_facts_str + "\n" + world_contradictions_str
                    ),
                    "events": _estimate_prompt_tokens(ranked_events_str + "\n" + mandatory_events),
                    "style": _estimate_prompt_tokens(
                        style_structured + "\n" + density_instruction + "\n"
                        + effective_style_context + "\n"
                        + anti_ai_expression_constraints
                    ),
                }
                source_manifest = [
                    {
                        "source_id": f"writer-field:{field}",
                        "field": field,
                        "text_hash": hashlib.sha256(str(value).encode("utf-8")).hexdigest(),
                    }
                    for field, value in prompt_values.items()
                ]
                prepared = SubsectionInput(
                    task_id=task_id,
                    section=section_num,
                    subsection=sub_num,
                    outline_target=f"第{section_num}节{sec.get('title', '')}: {sub_desc or '、'.join(key_points)}",
                    target_words=target_words,
                    generation_settings={
                        "max_tokens": call_max_tokens,
                        "temperature": 0.5,
                        "top_p": 0.9,
                    },
                    prepared_context_fields=prompt_values,
                    source_manifest=source_manifest,
                )
                subsection_pipeline = SubsectionPipeline(prepared)
                state_frame_before_id = None
                if state_frame_history is not None:
                    before_source_hash = hashlib.sha256(
                        json.dumps(
                            source_manifest,
                            ensure_ascii=False,
                            sort_keys=True,
                        ).encode("utf-8")
                    ).hexdigest()
                    state_frame_before_id = state_frame_history.capture_before(
                        section=section_num,
                        subsection=sub_num,
                        before_source_hash=before_source_hash,
                        checkpoint_version=state_committer.CHECKPOINT_VERSION,
                    )
                prompt_artifact = PromptBuilder().build(
                    prepared, token_by_source=context_token_estimates
                )
                execution_contract_application = None
                scene_spec_application = None
                if execution_contract_controller.enabled or scene_spec_canary.enabled:
                    next_subsection = next(
                        (
                            item for item in subsections
                            if int(item.get("subsection", 0)) == sub_num + 1
                        ),
                        None,
                    )
                if execution_contract_controller.mode == "canary":
                    execution_contract_application = execution_contract_controller.apply(
                        prompt_artifact,
                        task_id=task_id,
                        section=section_num,
                        current_subsection=sub,
                        next_subsection=next_subsection,
                        is_last_subsection=sub is subsections[-1],
                        required_events=execution_required_events,
                        target_characters=target_words,
                    )
                    prompt_artifact = execution_contract_application.prompt
                elif scene_spec_canary.enabled:
                    scene_spec_application = scene_spec_canary.apply(
                        prompt_artifact,
                        task_id=task_id,
                        section=section_num,
                        current_subsection=sub,
                        next_subsection=next_subsection,
                        is_last_subsection=sub is subsections[-1],
                    )
                    prompt_artifact = scene_spec_application.prompt
                if execution_contract_controller.mode == "shadow":
                    execution_contract_application = execution_contract_controller.apply(
                        prompt_artifact,
                        task_id=task_id,
                        section=section_num,
                        current_subsection=sub,
                        next_subsection=next_subsection,
                        is_last_subsection=sub is subsections[-1],
                        required_events=execution_required_events,
                        target_characters=target_words,
                    )
                    prompt_artifact = execution_contract_application.prompt
                subsection_pipeline.record_prompt(prompt_artifact)
                messages = prompt_artifact.messages
                input_tokens_estimate = prompt_artifact.estimated_tokens
                if state_frame_history is not None:
                    state_frame_history.bind_prompt_hash(
                        state_frame_before_id,
                        prompt_artifact.messages_hash,
                    )

                # --- LLM 调用（支持重试） ---
                t_llm_start = time.time()
                logger.info(f"[{task_id[:8]}] 第{section_num}.{sub_num}小节 LLM 开始 (max_tokens={call_max_tokens})")
                raw_output = self._generate_with_retry(
                    messages=messages,
                    call_max_tokens=call_max_tokens,
                    stream_callback=stream_callback,
                    section_num=section_num,
                    sub_num=sub_num,
                    mandatory_events_text=mandatory_events,
                    characters=characters,
                    previous_texts=previous_sub_texts,
                    prev_sub_text=previous_sub_texts[-1] if previous_sub_texts else "",
                    target_goal=f"第{section_num}节{sec.get('title','')}: {sub_desc or '、'.join(key_points)}",
                    task_id=task_id,
                )
                t_llm = time.time() - t_llm_start
                out_chars = count_chinese_chars(raw_output)
                t_total = time.time() - t_sub_start
                section_timings.append({"section": section_num, "subsection": sub_num,
                    "llm_time_s": round(t_llm, 1), "total_time_s": round(t_total, 1),
                    "char_count": out_chars,
                    "input_tokens_estimate": input_tokens_estimate,
                    "output_tokens_estimate": _estimate_prompt_tokens(raw_output),
                    "context_block_tokens": context_token_estimates,
                    "rewrite_count": getattr(self, "_last_retry_count", 0),
                })
                logger.info(f"[{task_id[:8]}] 第{section_num}.{sub_num}小节 LLM 完成 "
                           f"(耗时 {t_llm:.1f}s, {out_chars} 字)")

                # --- 提取交接信息（独立 LLM 调用，不影响正文纯净度） ---
                sub_text = raw_output  # Writer 只输出纯正文，无需正则切分
                post_write_extraction_context = self._build_post_write_extraction_context(
                    characters=characters,
                    character_arcs=character_arcs,
                    event_graph=event_graph,
                    section=section_num,
                    subsection=sub_num,
                )
                if blackboard and retrieved_items:
                    try:
                        rag_log = blackboard.get(task_id, "rag_recall_log")
                        if isinstance(rag_log, str):
                            import json as _json
                            rag_log = _json.loads(rag_log)
                        if isinstance(rag_log, list):
                            for record in reversed(rag_log):
                                if (record.get("section"), record.get("subsection")) == (section_num, sub_num):
                                    usage = measure_retrieval_usage(retrieved_items, sub_text)
                                    record["writer_usage"] = usage
                                    record["writer_unused_count"] = sum(
                                        item["classification"] == "not_observed" for item in usage
                                    )
                                    break
                            blackboard.set(task_id, "rag_recall_log", rag_log)
                    except Exception:
                        logger.warning(
                            f"[{task_id[:8]}] RAG Writer 利用率记录失败 "
                            f"(第{section_num}.{sub_num}节)，fallback=offline-eval",
                            exc_info=True,
                        )
                handover_note, handover_observation = (
                    self._extract_handover_with_observation(
                        sub_text,
                        section_num,
                        sub_num,
                        character_context=character_context,
                        event_graph=event_graph,
                        current_subsection=sub,
                        next_subsection=(
                            next(
                                (
                                    item
                                    for item in subsections
                                    if int(item.get("subsection", 0))
                                    == sub_num + 1
                                ),
                                None,
                            )
                            if settings.WRITER_HANDOVER_CONTRACT_VERSION
                            in _HANDOVER_NEXT_BOUNDARY_VERSIONS
                            else None
                        ),
                        task_id=task_id,
                    )
                )
                backref = handover_note.get("found_contradictions", "") if handover_note else ""
                state_committer.commit_handover_effects(
                    idempotency_key=f"handover-effects:{task_id}:{section_num}:{sub_num}",
                    handover_note=handover_note,
                    event_graph=event_graph,
                    world_state=world_state,
                    world_state_enabled=settings.ENABLE_WORLD_STATE,
                    task_id=task_id,
                    section=section_num,
                    subsection=sub_num,
                    logger=logger,
                )

                # --- 写作后规则检查 ---
                validation_result = {"complete": True, "warnings": []}
                if required_events:
                    pc = post_check(sub_text, required_events)
                    validation_result = {
                        "complete": True,
                        "warnings": list(pc.get("warnings", [])),
                    }
                    if pc["warnings"]:
                        for w in pc["warnings"]:
                            logger.warning(f"[{task_id[:8]}] 第{section_num}.{sub_num}小节: {w}")
                        # 注入下小节 warning
                        if blackboard:
                            blackboard.xadd_event(task_id, {"event": "rule_warning", "section": section_num, "subsection": sub_num, "warnings": pc["warnings"]})

                # --- 长度与句尾控制（R1：调用参数和顺序保持原样） ---
                adjusted_artifact = self._adjust_generated_length(
                    sub_text,
                    target_words=target_words,
                    call_max_tokens=call_max_tokens,
                    stream_callback=stream_callback,
                    section_num=section_num,
                    sub_num=sub_num,
                    task_id=task_id,
                )
                initial_artifact = self._last_generation_artifact
                sub_text = adjusted_artifact.draft
                generation_artifact = GenerationArtifact(
                    raw_output=initial_artifact.raw_output,
                    draft=sub_text,
                    generation_attempts=(
                        initial_artifact.generation_attempts
                        + adjusted_artifact.generation_attempts
                    ),
                    finish_reason=adjusted_artifact.finish_reason,
                    latency_ms=initial_artifact.latency_ms + adjusted_artifact.latency_ms,
                    output_hash=adjusted_artifact.output_hash,
                )
                subsection_pipeline.record_generation(generation_artifact)
                subsection_pipeline.record_validation(validation_result)
                execution_contract_controller.observe_output(
                    execution_contract_application,
                    output=sub_text,
                    mandatory_observation=getattr(
                        self, "_last_mandatory_observation", None
                    ),
                )

                if settings.WRITER_STYLE_EVALUATION:
                    try:
                        style_evaluation = style_drift_tracker.observe(
                            sub_text,
                            section=section_num,
                            subsection=sub_num,
                            beat=beat,
                        )
                        section_timings[-1]["style_status"] = style_evaluation[
                            "status"
                        ]
                        if blackboard:
                            blackboard.set(
                                task_id,
                                "style_evaluation_v1",
                                style_drift_tracker.reports,
                            )
                            blackboard.xadd_event(
                                task_id,
                                {
                                    "event": "style_evaluation",
                                    "section": section_num,
                                    "subsection": sub_num,
                                    "status": style_evaluation["status"],
                                    "text_hash": style_evaluation["text_hash"],
                                },
                            )
                    except Exception:
                        logger.warning(
                            f"[{task_id[:8]}] 风格漂移评测失败 "
                            f"(第{section_num}.{sub_num}节)，fallback=offline-eval",
                            exc_info=True,
                        )

                # Objective-reality checks are deliberately observation-only:
                # no prompt injection, rewrite, retry, or production gating.
                try:
                    reality_record = narrative_reality_checker.observe(
                        sub_text,
                        section=section_num,
                        subsection=sub_num,
                        known_context="\n".join(
                            part for part in (
                                world_setting,
                                world_facts_str,
                                handover_context,
                                section_outline,
                                sub_desc,
                                "、".join(str(item) for item in key_points),
                            )
                            if part
                        ),
                    )
                    if reality_record is not None and blackboard:
                        blackboard.set(
                            task_id,
                            "narrative_reality_warnings_v0",
                            narrative_reality_checker.records,
                        )
                        blackboard.xadd_event(
                            task_id,
                            {
                                "event": "narrative_reality_check",
                                "section": section_num,
                                "subsection": sub_num,
                                "warning_count": reality_record["warning_count"],
                                "warning_codes": [
                                    item["code"]
                                    for item in reality_record["warnings"]
                                ],
                                "text_hash": reality_record["text_hash"],
                                "production_effect": False,
                            },
                        )
                except Exception:
                    logger.warning(
                        f"[{task_id[:8]}] Narrative Reality Checker failed "
                        f"(section={section_num}.{sub_num}, fallback=continue)",
                        exc_info=True,
                    )

                # --- 累积 ---
                section_text += f"【{sub_title}】\n{sub_text}\n\n"
                full_draft += f"【{sub_title}】\n{sub_text}\n\n"
                previous_sub_texts.append(sub_text)  # P2: 追踪用于重复检测

                state_committer.commit_local_handover(
                    idempotency_key=f"handover-local:{task_id}:{section_num}:{sub_num}",
                    handover_note=handover_note,
                    section_handover_parts=section_handover_parts,
                    backref=backref,
                    backref_suggestions=backref_suggestions,
                )
                # The next subsection needs the latest local end-state now;
                # waiting until section end leaves same-section scenes with
                # only raw prose and no structured continuity boundary.
                prev_handover = self._advance_local_handover(
                    prev_handover, handover_note
                )

                # --- 进度更新 ---
                if blackboard:
                    blackboard.set(task_id, "progress",
                        f"第{section_num}节第{sub_num}/{len(subsections)}小节 ✓ "
                        f"({count_chinese_chars(sub_text)} 字)")

                # --- AI 痕迹检测（每小节，同步，正则零成本） ---
                try:
                    from ..ai_artifact_detector import analyze_text
                    ai_result = analyze_text(sub_text)
                    if blackboard:
                        bb_key = f"ai_detect_log"
                        log = blackboard.get(task_id, bb_key) or []
                        if isinstance(log, str):
                            log = []
                        log.append({
                            "section": section_num, "subsection": sub_num,
                            "ai_score": ai_result.get("ai_score", 10),
                            "pattern_count": ai_result.get("pattern_count", 0),
                            "total_chars": ai_result.get("total_chars", 0),
                            "category_counts": ai_result.get("category_counts", {}),
                        })
                        blackboard.set(task_id, bb_key, log)
                except Exception:
                    logger.warning(f"[{task_id[:8]}] AI 痕迹检测失败，跳过", exc_info=True)

                # --- 可选增量审阅；最终同步审阅仍由 coordinator 独立执行 ---
                self._maybe_start_incremental_section_review(
                    task_id=task_id,
                    section_num=section_num,
                    sub_num=sub_num,
                    topic=topic,
                    style=style,
                    full_draft=full_draft,
                    section_text=section_text,
                    sub_title=sub_title,
                    sub_text=sub_text,
                    blackboard=blackboard,
                )

                # --- 有序提交小节副作用（R1：顺序与 fallback 保持原样） ---
                def _token_usage_provider():
                    from ..utils.llm_client import get_cumulative_tokens
                    return get_cumulative_tokens()

                rag_metadata = None
                if rag_metadata_provider is not None:
                    try:
                        rag_metadata = rag_metadata_provider(section_num, sub_num)
                    except Exception as exc:
                        logger.warning(
                            f"[{task_id[:8]}] rag_metadata provider failed "
                            f"(section={section_num}.{sub_num}), fallback=legacy",
                            exc_info=True,
                        )
                commit_artifact = state_committer.commit_subsection(
                    idempotency_key=f"{task_id}:{section_num}:{sub_num}",
                    source_hash=prompt_artifact.messages_hash,
                    draft=sub_text,
                    validation_complete=True,
                    vector_store=vector_store,
                    context_manager=cm,
                    blackboard=blackboard,
                    task_id=task_id,
                    section=section_num,
                    subsection=sub_num,
                    title=sub_title,
                    topic=topic,
                    stream_callback=stream_callback,
                    token_usage_provider=_token_usage_provider,
                    rag_metadata=rag_metadata,
                )
                if commit_artifact.warnings:
                    logger.warning(f"[{task_id[:8]}] token 消耗写入黑板失败")
                subsection_pipeline.record_commit(commit_artifact)
                shadow_boundary_validator.observe_committed(
                    task_id=task_id,
                    section=section_num,
                    subsection=sub_num,
                    text=sub_text,
                    output_hash=commit_artifact.output_hash,
                    source_manifest=prompt_artifact.source_manifest,
                    scene_spec=(
                        scene_spec_application.spec
                        if scene_spec_application is not None
                        else None
                    ),
                )
                shadow_post_write_extractor.observe_committed(
                    task_id=task_id,
                    section=section_num,
                    subsection=sub_num,
                    text=sub_text,
                    output_hash=commit_artifact.output_hash,
                    source_manifest=prompt_artifact.source_manifest,
                    known_context=post_write_extraction_context,
                )
                if state_frame_history is not None:
                    state_frame_history.capture_after(
                        section=section_num,
                        subsection=sub_num,
                        prompt_messages_hash=prompt_artifact.messages_hash,
                        output_sha256=commit_artifact.output_hash,
                        checkpoint_version=commit_artifact.checkpoint_version,
                        commit_idempotency_key=commit_artifact.idempotency_key,
                        before_record_id=state_frame_before_id,
                    )
                if subsection_handover_history is not None:
                    subsection_handover_history.capture_committed(
                        section=section_num,
                        subsection=sub_num,
                        output_sha256=commit_artifact.output_hash,
                        prompt_messages_hash=prompt_artifact.messages_hash,
                        commit_idempotency_key=commit_artifact.idempotency_key,
                        handover_note=handover_note,
                        observation=handover_observation,
                    )

            # B2: 子节循环内检测到停止信号，跳出外层 while
            if should_stop:
                logger.info(f"[{task_id[:8]}] 应停止信号退出写作循环")
                break

            # --- 节尾汇总 ---
            section_texts[section_num] = section_text
            if section_handover_parts:
                prev_handover, _ = state_committer.commit_section_handover(
                    idempotency_key=f"handover-section:{task_id}:{section_num}",
                    section=section_num,
                    section_handover_parts=section_handover_parts,
                    handover_notes=handover_notes,
                    stream_callback=stream_callback,
                )

            # --- 角色状态更新 ---
            if character_arcs:
                cm_char = CharacterManager()
                prior_character_arcs = copy_character_arcs(character_arcs)
                input_state_hash = character_arcs_hash(prior_character_arcs)
                source = "writer_updated"
                fallback_reason = None
                try:
                    candidate_arcs = cm_char.update_states(
                        characters or [], prior_character_arcs, section_text, section_num
                    )
                    if not is_valid_character_arcs(candidate_arcs):
                        source = "legacy_input_fallback"
                        fallback_reason = "invalid_character_state_update"
                        character_arcs = prior_character_arcs
                    else:
                        character_arcs = copy_character_arcs(candidate_arcs)
                except Exception as exc:
                    source = "legacy_input_fallback"
                    fallback_reason = f"character_state_update_error:{type(exc).__name__}"
                    character_arcs = prior_character_arcs
                    logger.warning(
                        "[%s] character state update failed; retaining prior state (%s)",
                        task_id[:8],
                        type(exc).__name__,
                    )
                updated_state_hash = character_arcs_hash(character_arcs)
                character_state_propagation = build_character_state_propagation_event(
                    task_id=task_id,
                    section=section_num,
                    subsection=None,
                    source=source,
                    input_state_hash=input_state_hash,
                    updated_state_hash=updated_state_hash,
                    checkpoint_state_hash=updated_state_hash,
                    update_applied=updated_state_hash != input_state_hash,
                    fallback_reason=fallback_reason,
                    checkpoint_version=state_committer.CHECKPOINT_VERSION,
                )
                logger.info(
                    "character_state_propagation %s",
                    json.dumps(character_state_propagation, ensure_ascii=True, sort_keys=True),
                )
                if blackboard:
                    blackboard.set(task_id, "character_arcs", copy_character_arcs(character_arcs))

            # --- AI 提取角色关系变化 ---
            try:
                from ..character_relation_store import extract_relations_from_text
                char_names = [c.get("name", "") for c in (characters or []) if c.get("name")]
                if len(char_names) >= 2 and section_text.strip():
                    def _llm(prompt, system="", max_tokens=800):
                        msgs = [
                            {"role": "system", "content": system},
                            {"role": "user", "content": prompt},
                        ]
                        return self.llm.chat_completion(msgs, temperature=0.3, max_tokens=max_tokens)
                    extracted = extract_relations_from_text(
                        section_text, task_id, char_names, section_num, llm_call=_llm
                    )
                    if extracted:
                        logger.info(f"[{task_id[:8]}] 提取到 {len(extracted)} 个角色关系变化")
            except Exception as e:
                logger.warning(f"[{task_id[:8]}] 关系提取非致命错误: {e}", exc_info=True)

            # --- 自动提取经历事件（fire-and-forget，不阻塞写作） ---
            try:
                section_text_snapshot = section_texts.get(section_num, "")
                if section_text_snapshot and len(section_text_snapshot) > 500:
                    from ..experience_timeline import extract_from_section

                    def _run_experience_extraction():
                        try:
                            extract_from_section(task_id, section_num, section_text_snapshot)
                        except Exception:
                            logger.warning(f"[{task_id[:8]}] 第{section_num}节经历提取失败",
                                           exc_info=True)

                    t = threading.Thread(target=_run_experience_extraction, daemon=True)
                    t.start()
            except Exception:
                logger.warning(f"[{task_id[:8]}] 启动经历提取线程失败", exc_info=True)

            # --- 停止检查（所有模式） ---
            if blackboard and blackboard.get(task_id, "status") == "stopped":
                logger.info(f"[{task_id[:8]}] 检测到停止信号，退出写作")
                break

            # --- 自动模式检查点 (v0.9.2: 每节存档，不挂起) ---
            if not interactive and blackboard:
                try:
                    state_committer.save_checkpoint(blackboard, task_id, {
                        "task_id": task_id,
                        "phase": "writing",
                        "section_texts": dict(section_texts),
                        "handover_chain": list(handover_notes),
                        "backref_suggestions": list(backref_suggestions),
                        "current_section": section_num,
                        "draft": full_draft,
                        "character_arcs": copy_character_arcs(character_arcs),
                        "_character_state_propagation": dict(character_state_propagation),
                    })
                except Exception:
                    logger.warning(f"[{task_id[:8]}] 检查点保存失败 (第{section_num}节)，继续写作", exc_info=True)

            # --- 交互模式检查点 ---
            if interactive and on_section_done:
                should_continue = on_section_done(
                    section_num,
                    section_texts=dict(section_texts),
                    handover_notes=list(handover_notes),
                    backref_suggestions=list(backref_suggestions),
                    character_arcs=copy_character_arcs(character_arcs),
                    character_state_propagation=dict(character_state_propagation),
                )
                if not should_continue:
                    sec_idx += 1
                    break

            full_draft += "\n"
            sec_idx += 1

            # P11: 检查后续是否还有 queued 的节点
            remaining_queued = 0
            for s in outline[sec_idx:]:
                for sub in s.get("subsections", []):
                    if sub.get("status", "queued") == "queued":
                        remaining_queued += 1
            if remaining_queued == 0 and sec_idx < len(outline):
                logger.info(f"[{task_id[:8]}] 无排队章节，等待用户切换 draft→queued")
                if blackboard:
                    blackboard.set(task_id, "status", "awaiting_queue")
                    blackboard.set(task_id, "progress", f"已完成第{section_num}节，等待用户勾选后续章节")
                    blackboard.xadd_event(task_id, {
                        "event": "awaiting_queue",
                        "section": section_num,
                        "message": f"已写完所有排队章节({section_num}节)，请勾选后续章节继续"
                    })
                    # 阻塞等待大纲更新（最多 10 分钟），用户切换 draft→queued 后自动继续
                    _should_exit = True
                    while True:
                        notified = blackboard.wait_for_notification(
                            task_id, "outline_updated", timeout=60)
                        if blackboard.get(task_id, "status") == "stopped":
                            should_stop = True; break
                        if not notified:
                            # 每 60s 超时检查一次，累计 10 分钟后退出
                            _waited = getattr(self, '_wait_deadline', 0) or 0
                            if _waited == 0:
                                self._wait_deadline = time.time() + 600
                            if time.time() > self._wait_deadline:
                                logger.info(f"[{task_id[:8]}] 等待超时，退出写作")
                                break
                            continue
                        updated_outline = blackboard.get(task_id, "outline")
                        if updated_outline and isinstance(updated_outline, list):
                            _new_queued = 0
                            for _us in updated_outline[sec_idx:]:
                                for _usub in _us.get("subsections", []):
                                    if _usub.get("status", "queued") == "queued":
                                        _new_queued += 1
                            if _new_queued > 0:
                                outline = updated_outline
                                self._wait_deadline = 0
                                blackboard.set(task_id, "status", "writing")
                                logger.info(f"[{task_id[:8]}] 检测到 {_new_queued} 个新排队章节，继续写作")
                                _should_exit = False; break
                    if _should_exit: break

        return {
            "draft": full_draft.strip(),
            "handover_notes": handover_notes,
            "backref_suggestions": backref_suggestions,
            "section_texts": section_texts,
            "context_state": cm.serialize(),
            "section_timings": section_timings,
            "style_evaluations": style_drift_tracker.reports,
            "style_policy_observations": style_policy_observations,
            "anti_ai_expression_observations": anti_ai_expression_observations,
            "commercial_harness_observations": commercial_harness_observations,
            "narrative_integrity_observations": narrative_integrity_observations,
            "world_pressure_observations": world_pressure_observations,
            "narrative_reality_warnings": narrative_reality_checker.records,
            "character_arcs": copy_character_arcs(character_arcs),
            "character_state_propagation": dict(character_state_propagation),
        }

    # ═══ P0: 硬约束构建 ═══

    @staticmethod
    def _build_shadow_boundary_validation_runner():
        return ShadowBoundaryValidationRunner(
            enabled=settings.WRITER_BOUNDARY_VALIDATOR_SHADOW,
        )

    def _build_shadow_post_write_extraction_runner(self, *, blackboard, task_id: str):
        enabled = settings.WRITER_POST_WRITE_EXTRACTION_MODE == "shadow"
        if not enabled:
            return ShadowPostWriteExtractionRunner(enabled=False)
        from ..writing.shadow_post_write_extraction import BlackboardPostWriteExtractionSink
        sink = (
            BlackboardPostWriteExtractionSink(blackboard, task_id)
            if blackboard is not None
            else None
        )
        return ShadowPostWriteExtractionRunner(
            enabled=True,
            extractor=SharedPostWriteExtractor(self.llm),
            sink=sink,
        )

    @staticmethod
    def _build_post_write_extraction_context(
        *, characters, character_arcs, event_graph, section: int, subsection: int,
    ) -> dict:
        character_refs = [
            {"character_id": str(item.get("id", "")), "name": str(item.get("name", ""))}
            for item in (characters or [])
            if item.get("id") or item.get("name")
        ]
        arc_refs = [
            {
                "character_id": str(item.get("character_id", "")),
                "current_state": str(item.get("current_state", "")),
                "ending_state": str(item.get("ending_state", "")),
            }
            for item in (character_arcs or [])
            if item.get("character_id")
        ]
        event_refs = []
        if event_graph is not None:
            try:
                event_refs = [
                    {
                        "event_id": str(item.event_id),
                        "description": str(item.description),
                        "status": str(getattr(item, "status", "")),
                    }
                    for item in event_graph.get_arc_events(section, subsection)[:10]
                ]
            except Exception:
                event_refs = []
        return {
            "characters": character_refs,
            "character_arcs": arc_refs,
            "open_events": event_refs,
        }

    @staticmethod
    def _collect_mandatory_event_sources(
        key_points, section_key_points, sub_desc, section_num, sub_num
    ) -> list[dict[str, str]]:
        values: list[tuple[str, str]] = []
        for index, value in enumerate(key_points or [], 1):
            values.append(
                (f"outline:S{section_num}.{sub_num}:key_point:{index}", str(value))
            )
        for index, value in enumerate(section_key_points or [], 1):
            values.append(
                (f"outline:S{section_num}:key_point:{index}", str(value))
            )
        if sub_desc:
            values.append(
                (f"outline:S{section_num}.{sub_num}:description", str(sub_desc))
            )

        result: list[dict[str, str]] = []
        seen_text: set[str] = set()
        for source_id, raw in values:
            text = raw.strip()
            if not text or text in seen_text:
                continue
            seen_text.add(text)
            result.append({
                "source_id": source_id,
                "text": text,
                "text_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            })
        return result

    @staticmethod
    def _build_mandatory_events(key_points, section_key_points, sub_desc,
                                 section_num, sub_num) -> str:
        """构建【硬约束】块 —— 本节必须包含的事件列表。"""
        events = list(key_points) if key_points else []
        # 加入节级要点
        for kp in (section_key_points or []):
            if kp not in events:
                events.append(kp)
        # 加入梗概
        if sub_desc and sub_desc not in events:
            events.append(sub_desc)

        if not events:
            return "（本节无硬性事件约束）"

        lines = ["本节必须包含以下事件（缺失任一事件则本小节不合格，将强制重写）："]
        for i, ev in enumerate(events, 1):
            lines.append(f"  {i}. 【必须】{ev}")
        return "\n".join(lines)

    @staticmethod
    def _build_progress_context(outline, current_section, current_sub, n_subs,
                                  key_points=None, sub_desc="") -> str:
        """构建【进度追踪】块 —— 已完成章节 + 当前任务 + 子目标链。"""
        completed = []
        for sec in outline:
            sn = sec.get("section", 0)
            if sn < current_section:
                completed.append(f"第{sn}节({sec.get('title', '')})")
        total_sections = len(outline)

        lines = [f"全书共 {total_sections} 节。"]

        if completed:
            lines.append(f"已完成: {' → '.join(completed)}")
        else:
            lines.append("已完成: 无（故事开头）")

        sec_title = ""
        for sec in outline:
            if sec.get("section") == current_section:
                sec_title = sec.get("title", "")
                break

        lines.append(f"当前任务: 第{current_section}节({sec_title}) "
                     f"第{current_sub}/{n_subs}小节")

        # 子目标链
        goals = list(key_points) if key_points else []
        if sub_desc and sub_desc not in goals:
            goals.append(sub_desc)
        if goals:
            lines.append(f"本节子目标: {' → '.join(goals)}")

        # 进度推进点
        if current_sub < n_subs:
            lines.append(f"本节结束后应推进到: 第{current_section}节第{current_sub+1}小节")
        elif current_section < total_sections:
            next_title = ""
            for sec in outline:
                if sec.get("section") == current_section + 1:
                    next_title = sec.get("title", "")
                    break
            lines.append(f"本节结束后应推进到: 第{current_section+1}节({next_title})")
        else:
            lines.append("本节是全书的最后一节。")

        return "\n".join(lines)

    # ═══ P1: 角色行为约束 ═══

    FORBIDDEN_PATTERNS = {
        "退缩": ["退缩", "后退一步", "转身逃走", "不敢上前", "掉头就跑", "逃之夭夭"],
        "哭泣": ["流泪", "哭泣", "泪流满面", "哽咽", "嚎啕大哭", "泪如雨下"],
        "软弱": ["吓得发抖", "瑟瑟发抖", "哆嗦", "两腿发软", "浑身颤抖"],
        "崩溃": ["崩溃大哭", "精神崩溃", "瘫倒在地", "跪倒在地", "泣不成声"],
    }

    @staticmethod
    def _build_character_constraints(characters) -> str:
        """从角色卡生成禁止行为列表 + 身份感知规则（硬约束）。"""
        if not characters:
            return ""
        lines = []
        for ch in characters:
            name = ch.get("name", "")
            if not name:
                continue
            forbidden = []
            weaknesses = ch.get("weaknesses", [])
            weakness_text = " ".join(weaknesses) if weaknesses else ""
            if not any(w in weakness_text for w in ["懦弱", "软弱", "胆小", "恐惧", "怯懦"]):
                forbidden.append(f"{name}不能表现出恐惧以外的软弱（不允许哭泣、发抖、崩溃）")
            if not any(w in weakness_text for w in ["退缩", "逃跑", "畏惧"]):
                personality = ch.get("personality", [])
                pers_text = " ".join(personality) if isinstance(personality, list) else str(personality)
                if "懦弱" not in pers_text and "胆小" not in pers_text:
                    forbidden.append(f"{name}不能在危险面前主动退缩或逃跑")
            # 身份感知：魂穿/转世角色
            if ch.get("previous_life") or ch.get("transmigrated"):
                pl = ch.get("previous_life", "")
                pw = ch.get("previous_world", "")
                forbidden.append(
                    f"{name}为魂穿/转世者（前世: {pl or '未知'}，来源世界: {pw or '未知'}）。"
                    f"写作时必须遵守：\n"
                    f"  1) 前世记忆与当前身份是两套体系——回忆前世时须用'前世''穿越前'等明确标记；\n"
                    f"  2) {name}对当前世界的规则应当有陌生感或对比感（除非已完全适应）；\n"
                    f"  3) 当前身体的技能、人际关系属于此世，前世技能可能不适用——不可混淆"
                )
            if ch.get("identity_conflict"):
                forbidden.append(f"{name}的身份冲突: {ch['identity_conflict']}")

            if forbidden:
                lines.append(f"【{name}的禁止行为】")
                for f in forbidden:
                    lines.append(f"  - {f}")
        # 防角色混淆：检测性格重叠
        if len(characters) >= 2:
            protag = next((c for c in characters if c.get("world_position") == "主角"), None)
            if not protag:
                protag = characters[0]  # fallback: 第一个角色当主角
            protag_name = protag.get("name", "主角")
            protag_pers = set(protag.get("personality", []))
            for ch in characters:
                if ch.get("name") == protag_name:
                    continue
                ch_pers = set(ch.get("personality", []))
                overlap = protag_pers & ch_pers
                if len(overlap) >= 2:
                    lines.append(
                        f"【防混淆】{ch['name']}与主角{protag_name}性格重叠（{', '.join(overlap)}）。"
                        f"{ch['name']}的视角和行为应限定为配角身份，不可喧宾夺主。"
                    )
        return "\n".join(lines) if lines else ""

    @staticmethod
    def _incremental_review_observation(
        *, task_id: str, section_num: int, sub_num: int,
        enabled: bool, started: bool, skip_reason: str | None,
    ) -> dict:
        record = {
            "task_id_hash": hashlib.sha256(task_id.encode("utf-8")).hexdigest(),
            "section": section_num,
            "subsection": sub_num,
            "incremental_review_enabled": enabled,
            "incremental_review_started": started,
            "skip_reason": skip_reason,
            "production_effect": False,
        }
        logger.info(
            "incremental_section_review_observation=%s",
            json.dumps(record, ensure_ascii=False, separators=(",", ":")),
        )
        return record

    def _maybe_start_incremental_section_review(
        self, *, task_id: str, section_num: int, sub_num: int,
        topic: str, style: dict, full_draft: str, section_text: str,
        sub_title: str, sub_text: str, blackboard,
    ) -> dict:
        enabled = settings.WRITER_INCREMENTAL_SECTION_REVIEW
        if not enabled:
            return self._incremental_review_observation(
                task_id=task_id,
                section_num=section_num,
                sub_num=sub_num,
                enabled=False,
                started=False,
                skip_reason="disabled_by_config",
            )

        review_subs_done = sum(1 for block in full_draft.split("【") if "】" in block)
        review_chars = len(section_text) + len(sub_text)
        trigger_subs = settings.WRITER_REVIEW_TRIGGER_SUBS
        trigger_chars = settings.WRITER_REVIEW_TRIGGER_CHARS
        triggered = (
            review_subs_done >= trigger_subs
            and review_subs_done % trigger_subs == 0
        ) or (
            review_subs_done >= 1 and review_chars > trigger_chars
        )
        if not triggered:
            return self._incremental_review_observation(
                task_id=task_id,
                section_num=section_num,
                sub_num=sub_num,
                enabled=True,
                started=False,
                skip_reason="trigger_not_reached",
            )

        try:
            from ..agents.reviewer import Reviewer
            reviewer = Reviewer()
            style_for_review = style if isinstance(style, dict) else {}
            review_snapshot = (
                section_text + f"【{sub_title}】\n{sub_text}\n\n"
            )[-8000:]

            def _run_section_review():
                try:
                    result = reviewer.review_section(
                        section_num, topic, style_for_review, review_snapshot
                    )
                    if blackboard and result:
                        reviews = blackboard.get(task_id, "section_reviews") or []
                        if isinstance(reviews, str):
                            reviews = []
                        for item in reviews:
                            if (
                                item.get("section") == section_num
                                and item.get("subsection") == sub_num
                            ):
                                item["status"] = "done"
                                item["score"] = result.get("score")
                                break
                        blackboard.set(task_id, "section_reviews", reviews)
                except Exception:
                    logger.warning(
                        "[%s] 第%s节第%s小节审阅失败",
                        task_id[:8], section_num, sub_num, exc_info=True,
                    )
                    if blackboard:
                        reviews = blackboard.get(task_id, "section_reviews") or []
                        if isinstance(reviews, str):
                            reviews = []
                        for item in reviews:
                            if (
                                item.get("section") == section_num
                                and item.get("subsection") == sub_num
                            ):
                                item["status"] = "failed"
                                break
                        blackboard.set(task_id, "section_reviews", reviews)

            thread = threading.Thread(target=_run_section_review, daemon=True)
            thread.start()
            if blackboard:
                reviews = blackboard.get(task_id, "section_reviews") or []
                if isinstance(reviews, str):
                    reviews = []
                reviews.append({
                    "section": section_num,
                    "subsection": sub_num,
                    "chars": review_chars,
                    "status": "pending",
                })
                blackboard.set(task_id, "section_reviews", reviews)
            return self._incremental_review_observation(
                task_id=task_id,
                section_num=section_num,
                sub_num=sub_num,
                enabled=True,
                started=True,
                skip_reason=None,
            )
        except Exception:
            logger.warning("[%s] 启动审阅线程失败", task_id[:8], exc_info=True)
            return self._incremental_review_observation(
                task_id=task_id,
                section_num=section_num,
                sub_num=sub_num,
                enabled=True,
                started=False,
                skip_reason="reviewer_start_error",
            )

    @staticmethod
    def _build_handover_brief(prev_handover: dict, llm_client=None) -> str:
        """将交接 JSON 翻译为自然语言交接简报 (v0.9.1).

        对标 StyleAnalyzer.build_brief() 的设计模式：
        结构化参数对 LLM 生成几乎无效，需二次 LLM 翻译为自然语言。

        Args:
            prev_handover: 上一节的交接 JSON {foreshadowing, character_state, open_threads, ...}
            llm_client: LLM 客户端，为 None 时回退到纯文本拼接

        Returns:
            120-200 字自然语言交接简报
        """
        if not prev_handover:
            return "（这是第一节，无前文交接笔记）"

        # 检查交接数据是否为空
        has_content = any(
            prev_handover.get(k)
            for k in (
                "foreshadowing",
                "character_state",
                "open_threads",
                "new_facts",
                "next_boundary",
            )
        )
        if not has_content:
            return "（上节无遗留线索）"

        # 尝试 LLM 翻译
        if llm_client:
            try:
                import json as _json
                from ..utils.prompt_templates import HANDOVER_BRIEF_PROMPT
                prompt = HANDOVER_BRIEF_PROMPT.format(
                    handover_json=_json.dumps(prev_handover, ensure_ascii=False, indent=2)
                )
                msgs = [
                    {"role": "system", "content": "你是一位小说编辑。请输出交接简报。"},
                    {"role": "user", "content": prompt},
                ]
                brief = llm_client.chat_completion(
                    msgs, temperature=0.3, max_tokens=300, prompt_name="handover_brief"
                )
                if brief and len(brief) >= 20:
                    boundary = Writer._render_handover_boundary(prev_handover)
                    return f"{brief}\n{boundary}" if boundary else brief
            except Exception:
                import logging
                logging.getLogger("writing_system.writer").warning(
                    "交接简报 LLM 翻译失败，回退到纯文本拼接"
                )

        # 回退：纯文本拼接（保持向后兼容）
        parts = []
        if prev_handover.get("foreshadowing"):
            parts.append(f"伏笔: {prev_handover['foreshadowing']}")
        if prev_handover.get("character_state"):
            parts.append(f"人物状态: {prev_handover['character_state']}")
        if prev_handover.get("open_threads"):
            parts.append(f"待承接: {prev_handover['open_threads']}")
        if prev_handover.get("new_facts"):
            facts = prev_handover["new_facts"]
            if isinstance(facts, list):
                facts = "；".join(str(item) for item in facts if item)
            if facts:
                parts.append(f"已确认事实: {facts}")
        boundary = Writer._render_handover_boundary(prev_handover)
        if boundary:
            parts.append(boundary)
        return "上一节留下的交接笔记：\n  " + "\n  ".join(parts) if parts else "（上节无遗留线索）"

    @staticmethod
    def _advance_local_handover(
        previous: dict | None, current: dict | None,
    ) -> dict | None:
        """Promote a valid subsection handover without erasing fail-open state."""
        return current if current else previous

    @staticmethod
    def _render_handover_boundary(prev_handover: dict) -> str:
        boundary = prev_handover.get("next_boundary")
        if not isinstance(boundary, dict):
            return ""
        lines = ["【小节连续性边界】"]
        completed = [
            str(item).strip()
            for item in (boundary.get("must_not_repeat_events") or [])
            if str(item).strip()
        ]
        allowed = [
            str(item).strip()
            for item in (boundary.get("allowed_start_events") or [])
            if str(item).strip()
        ]
        if completed:
            lines.append("已完成、不得重新演一遍：" + "；".join(completed))
        if allowed:
            lines.append("下一小节允许承接：" + "；".join(allowed))
        reason = str(boundary.get("stop_or_transition_reason") or "").strip()
        if reason:
            lines.append("转换要求：" + reason)
        return "\n".join(lines) if len(lines) > 1 else ""

    @classmethod
    def _check_character_violations(cls, sub_text: str, characters) -> list[str]:
        """检查正文是否违反角色行为约束。返回违规描述列表。"""
        violations = []
        if not characters:
            return violations
        for ch in characters:
            name = ch.get("name", "")
            if not name or name not in sub_text:
                continue
            weaknesses = ch.get("weaknesses", [])
            weakness_text = " ".join(weaknesses) if weaknesses else ""
            # 检查软弱行为
            if not any(w in weakness_text for w in ["懦弱", "软弱", "胆小", "恐惧", "怯懦"]):
                for pattern in cls.FORBIDDEN_PATTERNS.get("哭泣", []):
                    if pattern in sub_text and name in sub_text[max(0, sub_text.find(pattern)-50):sub_text.find(pattern)+len(pattern)]:
                        violations.append(f"{name}出现哭泣行为（角色设定不允许）")
                        break
                for pattern in cls.FORBIDDEN_PATTERNS.get("软弱", []):
                    if pattern in sub_text:
                        violations.append(f"{name}出现软弱行为: {pattern}（角色设定不允许）")
                        break
                for pattern in cls.FORBIDDEN_PATTERNS.get("崩溃", []):
                    if pattern in sub_text and name in sub_text[max(0, sub_text.find(pattern)-50):sub_text.find(pattern)+len(pattern)]:
                        violations.append(f"{name}出现崩溃行为（角色设定不允许）")
                        break
        return violations

    # ═══ P0: 带重试的生成 ═══

    def _generate_with_retry(self, messages, call_max_tokens, stream_callback,
                              section_num, sub_num, mandatory_events_text,
                              characters=None, previous_texts=None, prev_sub_text="",
                              target_goal="", task_id=""):
        """生成正文，若不满足硬约束则重试一次。"""
        controller = GenerationController(
            self.llm,
            character_violation_checker=self._check_character_violations,
            fallback_splitter=_split_for_fallback,
        )
        artifact = controller.generate(
            messages=messages,
            call_max_tokens=call_max_tokens,
            stream_callback=stream_callback,
            section_num=section_num,
            sub_num=sub_num,
            mandatory_events_text=mandatory_events_text,
            characters=characters,
            previous_texts=previous_texts,
            prev_sub_text=prev_sub_text,
            target_goal=target_goal,
            task_id=task_id,
        )
        self._last_generation_artifact = artifact
        self._last_retry_count = max(0, len(artifact.generation_attempts) - 1)
        self._last_mandatory_observation = controller.last_mandatory_observation
        return artifact.draft

    def _adjust_generated_length(self, draft, *, target_words, call_max_tokens,
                                 stream_callback, section_num, sub_num, task_id=""):
        controller = GenerationController(
            self.llm,
            character_violation_checker=self._check_character_violations,
            fallback_splitter=_split_for_fallback,
        )
        return controller.adjust_length(
            draft,
            target_words=target_words,
            call_max_tokens=call_max_tokens,
            stream_callback=stream_callback,
            section_num=section_num,
            sub_num=sub_num,
            task_id=task_id,
        )

    def _extract_handover(self, section_text: str, section_num: int, sub_num: int = 0,
                          character_context: str = "",
                          event_graph: EventGraph | None = None,
                          current_subsection: dict | None = None,
                          next_subsection: dict | None = None) -> dict | None:
        """独立 LLM 调用：从纯正文中提取交接信息（伏笔/人物状态/待承接/事实/事件回收）。

        v3: 替代 _parse_output() 的正则切分。Writer 输出纯正文，此方法做结构化提取。
        """
        note, _ = self._extract_handover_with_observation(
            section_text,
            section_num,
            sub_num,
            character_context=character_context,
            event_graph=event_graph,
            current_subsection=current_subsection,
            next_subsection=next_subsection,
        )
        return note

    def _extract_handover_with_observation(
        self,
        section_text: str,
        section_num: int,
        sub_num: int = 0,
        character_context: str = "",
        event_graph: EventGraph | None = None,
        current_subsection: dict | None = None,
        next_subsection: dict | None = None,
        task_id: str = "",
    ) -> tuple[dict | None, HandoverExtractionObservation]:
        """Run the configured extraction once and expose fail-safe status."""
        if settings.WRITER_HANDOVER_CONTRACT_VERSION in {"v2.1", "v2.2", "v2.3"}:
            return self._extract_handover_v21_with_observation(
                section_text=section_text,
                section_num=section_num,
                sub_num=sub_num,
                event_graph=event_graph,
                current_subsection=current_subsection,
                next_subsection=next_subsection,
                task_id=task_id,
            )
        if settings.WRITER_HANDOVER_CONTRACT_VERSION == "v2":
            return self._extract_handover_v2_with_observation(
                section_text=section_text,
                section_num=section_num,
                sub_num=sub_num,
                event_graph=event_graph,
                current_subsection=current_subsection,
                next_subsection=next_subsection,
            )
        open_threads_str = "（无）"
        if event_graph:
            arc_events = event_graph.get_arc_events(section_num, sub_num)
            if arc_events:
                open_threads_str = "\n".join(
                    f"- [{e.event_id[:8]}] {e.description} (第{e.section}节, weight={e.weight})"
                    for e in arc_events[:10]
                )
        prompt = HANDOVER_EXTRACTION_PROMPT.format(
            section_text=section_text[:3000],
            character_context=character_context or "（无）",
            open_threads=open_threads_str,
        )
        try:
            resp = self.llm.chat_completion(
                [{"role": "system", "content": "你是一位文学分析助手。请以 JSON 格式输出。"},
                 {"role": "user", "content": prompt}],
                temperature=0.2, max_tokens=600, json_mode=True,
            )
            result = parse_json(resp)
            if isinstance(result, dict):
                return result, observation_from_note(result)
            return None, HandoverExtractionObservation(
                executed=True,
                execution_status="error",
                error_type="InvalidHandoverPayload",
            )
        except Exception as error:
            logger.warning(f"交接信息提取失败 (第{section_num}.{sub_num}小节)")
            return None, HandoverExtractionObservation(
                executed=True,
                execution_status="error",
                error_type=type(error).__name__,
            )

    def _extract_handover_v21_with_observation(
        self,
        *,
        section_text: str,
        section_num: int,
        sub_num: int,
        event_graph: EventGraph | None,
        current_subsection: dict | None,
        next_subsection: dict | None,
        task_id: str = "",
    ) -> tuple[dict | None, HandoverExtractionObservation]:
        """Run one compact extraction (v2.1 spans / v2.2 quotes) and restore V2 data."""
        started = time.perf_counter()
        # 版本参数化：v2.1/v2.2/v2.3 共享 registry/boundary/fail-open 骨架，
        # 只在 Prompt、恢复函数、输出上限与版本标识上分叉。
        if settings.WRITER_HANDOVER_CONTRACT_VERSION == "v2.3":
            contract_version_label = "v2.3"
            payload_version = HANDOVER_COMPACT_V23_VERSION
            producer_version = "writer-handover-contract-v2.3"
            prompt_template = HANDOVER_EXTRACTION_PROMPT_V23
            restore_payload = restore_and_validate_v23
            handover_max_output_tokens = HANDOVER_COMPACT_V23_MAX_OUTPUT_TOKENS
        elif settings.WRITER_HANDOVER_CONTRACT_VERSION == "v2.2":
            contract_version_label = "v2.2"
            payload_version = HANDOVER_COMPACT_V22_VERSION
            producer_version = "writer-handover-contract-v2.2"
            prompt_template = HANDOVER_EXTRACTION_PROMPT_V22
            restore_payload = restore_and_validate_v22
            handover_max_output_tokens = HANDOVER_COMPACT_V22_MAX_OUTPUT_TOKENS
        else:
            contract_version_label = "v2.1"
            payload_version = HANDOVER_COMPACT_V21_VERSION
            producer_version = "writer-handover-contract-v2.1"
            prompt_template = HANDOVER_EXTRACTION_PROMPT_V21
            restore_payload = restore_and_validate_v21
            handover_max_output_tokens = HANDOVER_COMPACT_V21_MAX_OUTPUT_TOKENS
        arc_events = (
            event_graph.get_arc_events(section_num, sub_num)
            if event_graph is not None
            else []
        )
        current_outline = dict(current_subsection or {})
        current_outline["_section"] = section_num
        following_outline = (
            dict(next_subsection) if isinstance(next_subsection, dict) else None
        )
        if following_outline is not None:
            following_outline["_section"] = section_num
        sources = build_handover_sources(
            section=section_num,
            subsection=sub_num,
            generated_text=section_text,
            current_outline=current_outline,
            next_outline=following_outline,
            arc_milestones=arc_events,
        )
        registry = build_compact_source_registry(
            sources, arc_milestones=arc_events
        )
        boundary = compile_next_boundary(
            section=section_num,
            subsection=sub_num,
            current_outline=current_outline,
            next_outline=following_outline,
        )
        prompt = prompt_template.format(
            **render_v21_prompt_context(registry)
        )
        metadata: dict[str, object] = {}
        compact_payload_hash = None
        persisted_payload: dict | None = None
        raw_output_tokens = None
        finish_reason = "unavailable"
        truncation_status = "not_truncated"
        try:
            response = self.llm.chat_completion(
                [
                    {
                        "role": "system",
                        "content": "你是文学事实提取助手。只输出紧凑 JSON。",
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.2,
                max_tokens=handover_max_output_tokens,
                json_mode=True,
                completion_metadata_sink=metadata.update,
            )
            finish_reason = str(metadata.get("finish_reason") or "unavailable")
            raw_output_tokens = metadata.get("output_tokens")
            if not isinstance(raw_output_tokens, int):
                raw_output_tokens = estimate_llm_tokens(response)
            if finish_reason == "length":
                truncation_status = "output_truncated"
                raise ValueError("CompactHandoverOutputTruncated")
            payload = parse_json(response)
            if not isinstance(payload, dict):
                raise ValueError("InvalidCompactHandoverPayload")
            compact_payload_hash = sha256_handover_json(payload)
            persisted_payload = payload_for_persistence(payload)
            validation = restore_payload(
                payload,
                registry=registry,
                next_boundary=boundary,
            )
            note = adapt_v2_to_legacy_handover_note(validation)
            note["next_boundary"] = {
                "next_section": boundary.next_section,
                "next_subsection": boundary.next_subsection,
                "next_title": boundary.next_title,
                "allowed_start_events": list(boundary.allowed_start_events),
                "must_not_repeat_events": list(boundary.must_not_repeat_events),
                "stop_or_transition_reason": boundary.stop_or_transition_reason,
                "boundary_status": boundary.boundary_status,
                "conflict_reasons": list(boundary.conflict_reasons),
            }
            local_rejections = sum(
                item.item_id.startswith(("state:", "fact:", "open:", "arc:"))
                for item in validation.rejections
            )
            total_items = sum(
                len(payload.get(name) or []) for name in ("s", "o", "f", "a")
            )
            restored_count = max(0, total_items - local_rejections)
            observation = observation_from_note(note).model_copy(
                update={
                    "producer_version": producer_version,
                    "contract_version": contract_version_label,
                    "typed_contract_hash": validation.contract.contract_hash,
                    "accepted_claim_count": validation.accepted_claim_count,
                    "rejected_claim_count": validation.rejected_claim_count,
                    "rejection_counts": validation.rejection_counts,
                    "rejection_shape_skeletons": validation.rejection_shape_skeletons,
                    "next_boundary_hash": sha256_handover_json(
                        boundary.model_dump(mode="json")
                    ),
                    "source_manifest": tuple(
                        source.public_manifest() for source in sources.values()
                    ),
                    "payload_version": payload_version,
                    "source_registry_hash": registry.registry_hash,
                    "compact_payload_hash": compact_payload_hash,
                    "compact_payload": persisted_payload,
                    "raw_output_tokens": raw_output_tokens,
                    "finish_reason": finish_reason,
                    "truncation_status": truncation_status,
                    "restored_claim_count": restored_count,
                    "locally_rejected_claim_count": local_rejections,
                }
            )
            logger.info(
                "handover_v21_observation=%s",
                json.dumps(
                    {
                        "task_id_hash": handover_task_id_hash(task_id) if task_id else None,
                        "section": section_num,
                        "subsection": sub_num,
                        "version": payload_version,
                        "source_registry_count": len(registry.entries),
                        "source_registry_hash": registry.registry_hash,
                        "finish_reason": finish_reason,
                        "raw_output_tokens": raw_output_tokens,
                        "output_truncated": False,
                        "compact_payload_hash": compact_payload_hash,
                        "restored_claim_count": restored_count,
                        "locally_rejected_claim_count": local_rejections,
                        "rejection_shape_skeletons": validation.rejection_shape_skeletons,
                        "typed_contract_hash": validation.contract.contract_hash,
                        "fallback_reason": None,
                        "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
                        "production_effect": "validated_handover_only",
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            )
            return note, observation
        except Exception as error:
            logger.warning(
                "%s handover extraction failed (S%s.%s); preserving fail-open behavior",
                contract_version_label,
                section_num,
                sub_num,
            )
            logger.info(
                "handover_v21_observation=%s",
                json.dumps(
                    {
                        "task_id_hash": handover_task_id_hash(task_id) if task_id else None,
                        "section": section_num,
                        "subsection": sub_num,
                        "version": payload_version,
                        "source_registry_count": len(registry.entries),
                        "source_registry_hash": registry.registry_hash,
                        "finish_reason": finish_reason,
                        "raw_output_tokens": raw_output_tokens,
                        "output_truncated": truncation_status == "output_truncated",
                        "compact_payload_hash": compact_payload_hash,
                        "restored_claim_count": 0,
                        "locally_rejected_claim_count": 0,
                        "typed_contract_hash": None,
                        "fallback_reason": type(error).__name__,
                        "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
                        "production_effect": False,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            )
            return None, HandoverExtractionObservation(
                executed=True,
                execution_status="error",
                error_type=type(error).__name__,
                producer_version=producer_version,
                contract_version=contract_version_label,
                next_boundary_hash=sha256_handover_json(
                    boundary.model_dump(mode="json")
                ),
                source_manifest=tuple(
                    source.public_manifest() for source in sources.values()
                ),
                payload_version=payload_version,
                source_registry_hash=registry.registry_hash,
                compact_payload_hash=compact_payload_hash,
                compact_payload=persisted_payload,
                raw_output_tokens=raw_output_tokens,
                finish_reason=finish_reason,
                truncation_status=truncation_status,
                restored_claim_count=0,
                locally_rejected_claim_count=0,
            )
    def _extract_handover_v2_with_observation(
        self,
        *,
        section_text: str,
        section_num: int,
        sub_num: int,
        event_graph: EventGraph | None,
        current_subsection: dict | None,
        next_subsection: dict | None,
    ) -> tuple[dict | None, HandoverExtractionObservation]:
        """Run one V2 extraction and adapt only validated claims for V1 consumers."""
        arc_events = (
            event_graph.get_arc_events(section_num, sub_num)
            if event_graph is not None
            else []
        )
        current_outline = dict(current_subsection or {})
        current_outline["_section"] = section_num
        following_outline = (
            dict(next_subsection) if isinstance(next_subsection, dict) else None
        )
        if following_outline is not None:
            following_outline["_section"] = section_num
        sources = build_handover_sources(
            section=section_num,
            subsection=sub_num,
            generated_text=section_text,
            current_outline=current_outline,
            next_outline=following_outline,
            arc_milestones=arc_events,
        )
        boundary = compile_next_boundary(
            section=section_num,
            subsection=sub_num,
            current_outline=current_outline,
            next_outline=following_outline,
        )
        prompt = HANDOVER_EXTRACTION_PROMPT_V2.format(
            **render_v2_prompt_context(sources, boundary)
        )
        try:
            response = self.llm.chat_completion(
                [
                    {
                        "role": "system",
                        "content": "你是文学事实提取助手。只输出带精确来源证据的 JSON。",
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.2,
                max_tokens=600,
                json_mode=True,
            )
            payload = parse_json(response)
            if not isinstance(payload, dict):
                raise ValueError("InvalidHandoverPayload")
            validation = HandoverContractValidatorV2().validate(
                payload,
                sources=sources,
                next_boundary=boundary,
            )
            note = adapt_v2_to_legacy_handover_note(validation)
            observation = observation_from_note(note).model_copy(
                update={
                    "producer_version": "writer-handover-contract-v2",
                    "contract_version": "v2",
                    "typed_contract_hash": validation.contract.contract_hash,
                    "accepted_claim_count": validation.accepted_claim_count,
                    "rejected_claim_count": validation.rejected_claim_count,
                    "rejection_counts": validation.rejection_counts,
                    "rejection_shape_skeletons": validation.rejection_shape_skeletons,
                    "next_boundary_hash": sha256_handover_json(
                        boundary.model_dump(mode="json")
                    ),
                    "source_manifest": tuple(
                        source.public_manifest() for source in sources.values()
                    ),
                }
            )
            return note, observation
        except Exception as error:
            logger.warning(
                "V2 handover extraction failed (S%s.%s); preserving fail-open behavior",
                section_num,
                sub_num,
            )
            return None, HandoverExtractionObservation(
                executed=True,
                execution_status="error",
                error_type=type(error).__name__,
                producer_version="writer-handover-contract-v2",
                contract_version="v2",
                next_boundary_hash=sha256_handover_json(
                    boundary.model_dump(mode="json")
                ),
                source_manifest=tuple(
                    source.public_manifest() for source in sources.values()
                ),
            )

    def _parse_backrefs(self, text: str, from_section: int) -> list[dict]:
        """从回溯修正文本中提取结构化建议。"""
        refs = []
        pattern = r'第(\d+)节(?:第(\d+)小节)?[：:]*\s*(.*?)(?=第\d+节|$)'
        for m in re.finditer(pattern, text, re.DOTALL):
            target_sec = int(m.group(1))
            target_sub = int(m.group(2)) if m.group(2) else None
            desc = m.group(3).strip()
            if desc:
                refs.append({
                    "from_section": from_section,
                    "target_section": target_sec,
                    "target_subsection": target_sub,
                    "severity": "minor",
                    "description": desc,
                })
        return refs

    def revise_subsection(self, original_text: str, instruction: str) -> str:
        """定向修订某段文本。"""
        prompt = TARGETED_REVISE_PROMPT.format(
            original_text=original_text,
            instruction=instruction,
        )
        messages = [
            {"role": "system", "content": "你是一位编辑。请根据指令修改文本，只输出修改后的完整文本。"},
            {"role": "user", "content": prompt},
        ]
        return self.llm.chat_completion(messages, temperature=0.4, max_tokens=4096)
