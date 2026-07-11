import re
import logging
import time
import threading
from typing import Callable
from .base import BaseAgent
from .character_manager import CharacterManager
from .character_formatter import CharacterFormatter
from .context_manager import ContextManager
from ..utils.prompt_templates import (
    WRITING_PROMPT,
    WRITING_SECTION1_PROMPT,
    TARGETED_REVISE_PROMPT,
    HANDOVER_EXTRACTION_PROMPT,
)
from ..utils.text_chunker import chunk_text
from ..utils.word_counter import count_chinese_chars
from ..utils.style_brief import StyleSummarizer
from ..utils.json_parser import parse_json
from ..config import settings
from ..world_state import WorldStateManager
from ..narrative_event import EventGraph, rank_and_fill, format_events_for_prompt
from ..rule_checks import pre_check, post_check
from .. import foreshadowing_store
from .. import rule_store

logger = logging.getLogger("writing_system.writer")


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
        cm = ContextManager(self.llm)
        if resume_context:
            cm.deserialize(resume_context)
        full_draft = ""
        handover_notes = []
        backref_suggestions = []
        section_texts = dict(existing_section_texts) if existing_section_texts else {}
        previous_sub_texts = []  # P2: 累积已生成的小节正文，用于重复检测
        existing_draft = existing_draft or {}

        prev_handover = None
        if prev_handover_list:
            handover_notes = list(prev_handover_list)
            prev_handover = prev_handover_list[-1] if prev_handover_list else None

        style_brief = style.get("style_brief", "") if isinstance(style, dict) else ""
        narrative_density = style.get("narrative_density", 0.7) if isinstance(style, dict) else 0.7
        density_instruction = _narrative_density_instruction(narrative_density)
        style_structured = StyleSummarizer.for_writer(style) if isinstance(style, dict) else ""

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
                    if updated and isinstance(updated, list) and len(updated) > len(outline):
                        logger.info(f"[{task_id[:8]}] 检测到大纲更新: {len(outline)}→{len(updated)}节")
                        existing = {s.get("section") for s in outline}
                        for new_sec in updated:
                            ns = new_sec.get("section", 0)
                            if ns not in existing:
                                outline.append(new_sec)
                                existing.add(ns)
                except Exception:
                    pass
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
                sub_status = sub.get("status", "queued")
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

                # --- 进度 ---
                if blackboard:
                    blackboard.set(task_id, "progress",
                        f"第{section_num}节第{sub_num}/{len(subsections)}小节")

                # --- RAG 检索 ---
                query = f"{topic} {section_title} {sub_title} {' '.join(key_points)}"
                retrieved_chunks = vector_store.search(
                    query, k=settings.RAG_TOP_K, task_id=task_id
                )
                retrieved_context = ""
                if retrieved_chunks:
                    retrieved_context = "已写段落参考：\n"
                    for i, chunk in enumerate(retrieved_chunks, 1):
                        retrieved_context += f"--- {i} ---\n{chunk}\n"

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
                    except Exception: pass
                    try:
                        from ..map_manager import build_location_context
                        lc_ctx = build_location_context(task_id)
                        if lc_ctx: parts.append(lc_ctx)
                    except Exception: pass
                    try:
                        from ..item_manager import build_item_context
                        ic_ctx = build_item_context(task_id)
                        if ic_ctx: parts.append(ic_ctx)
                    except Exception: pass

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
                progress_context = self._build_progress_context(
                    outline=outline,
                    current_section=section_num,
                    current_sub=sub_num,
                    n_subs=n_subs,
                    key_points=key_points,
                    sub_desc=sub_desc,
                )

                # --- 选择 prompt ---
                template = WRITING_SECTION1_PROMPT if (section_num == 1 and sub_num == 1) else WRITING_PROMPT

                prompt = template.format(
                    mandatory_events=mandatory_events,
                    character_constraints=self._build_character_constraints(characters),
                    progress_context=progress_context,
                    rules_context=rules_ctx if rules_ctx else "",
                    topic=topic,
                    section=section_num,
                    subsection=sub_num,
                    subsection_title=sub_title,
                    section_outline=section_outline,
                    key_points="、".join(key_points),
                    sub_description=sub_desc if sub_desc else "（按大意自由发挥）",
                    world_setting=world_setting if world_setting.strip() else "",
                    world_facts=world_facts_str,
                    world_contradictions=world_contradictions_str,
                    style_brief=style_brief,
                    style_structured=style_structured,
                    narrative_density_instruction=density_instruction,
                    ranked_events=ranked_events_str,
                    emotion_intensity=style.get("emotion_intensity", 50) if isinstance(style, dict) else 50,
                    adjective_density=style.get("adjective_density", 0.15) if isinstance(style, dict) else 0.15,
                    paragraph_length_avg=style.get("paragraph_length_avg", 200) if isinstance(style, dict) else 200,
                    character_context=character_context,
                    arc_context=arc_context,
                    handover_context=handover_context,
                    summary_context=summary_context if summary_context else "（故事开头）",
                    retrieved_context=retrieved_context if retrieved_context else "（无相关段落）",
                    target_words=target_words,
                )

                system_msg = WRITER_SYSTEM_PROMPT
                messages = [
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": prompt},
                ]

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
                )
                t_llm = time.time() - t_llm_start
                out_chars = count_chinese_chars(raw_output)
                logger.info(f"[{task_id[:8]}] 第{section_num}.{sub_num}小节 LLM 完成 "
                           f"(耗时 {t_llm:.1f}s, {out_chars} 字)")

                # --- 提取交接信息（独立 LLM 调用，不影响正文纯净度） ---
                sub_text = raw_output  # Writer 只输出纯正文，无需正则切分
                handover_note = self._extract_handover(
                    sub_text, section_num, sub_num,
                    character_context=character_context,
                    event_graph=event_graph,
                )
                backref = handover_note.get("found_contradictions", "") if handover_note else ""
                new_facts = handover_note.get("new_facts", []) if handover_note else []
                # 弧线进度更新（v3.1：从 handover 提取的 arc_progress 回写 EventGraph）
                arc_progress = handover_note.get("arc_progress", {}) if handover_note else {}
                if event_graph and arc_progress and isinstance(arc_progress, dict):
                    for cid, status in arc_progress.items():
                        if status in ("done", "deviated"):
                            n = event_graph.update_arc_status(str(cid), status)
                            if n:
                                logger.info(f"[{task_id[:8]}] 弧线更新: {cid} → {status} ({n} 里程碑)")

                # --- 持久化 new_facts → WorldStateManager ---
                if new_facts and world_state and settings.ENABLE_WORLD_STATE:
                    for fact_text in new_facts:
                        if isinstance(fact_text, str) and fact_text.strip():
                            try:
                                fid = world_state.add_fact(
                                    category="subplot_derived",
                                    fact=fact_text.strip(),
                                    source_section=section_num,
                                    source_subsection=sub_num,
                                )
                                if fid:
                                    logger.debug(f"[{task_id[:8]}] 新事实已写入 WorldState: {fact_text[:50]}")
                            except Exception:
                                pass

                # --- 写作后规则检查 ---
                if required_events:
                    pc = post_check(sub_text, required_events)
                    if pc["warnings"]:
                        for w in pc["warnings"]:
                            logger.warning(f"[{task_id[:8]}] 第{section_num}.{sub_num}小节: {w}")
                        # 注入下小节 warning
                        if blackboard:
                            blackboard.xadd_event(task_id, {"event": "rule_warning", "section": section_num, "subsection": sub_num, "warnings": pc["warnings"]})

                # --- 续写（字数不足时自动触发，最多2次） ---
                sub_words = count_chinese_chars(sub_text)
                expand_attempts = 0
                while sub_words < target_words * settings.WRITER_EXPAND_THRESHOLD and expand_attempts < settings.WRITER_MAX_EXPAND_ATTEMPTS:
                    expand_attempts += 1
                    if stream_callback:
                        stream_callback("", section_num, sub_num, "expand_start")
                    continue_msg = [
                        {"role": "system", "content": "请继续上面的内容往下写，保持风格一致。"},
                        {"role": "user", "content": f"已写 {sub_words} 字，目标 {target_words} 字。继续：\n{sub_text[-200:]}"},
                    ]
                    continuation = ""
                    if stream_callback:
                        try:
                            for token in self.llm.chat_completion_stream(
                                continue_msg, temperature=0.7, max_tokens=call_max_tokens // 2
                            ):
                                continuation += token
                                stream_callback(token, section_num, sub_num, "token")
                        except Exception:
                            continuation = self.llm.chat_completion(
                                continue_msg, temperature=0.7, max_tokens=call_max_tokens // 2
                            )
                            if continuation:
                                for sent_chunk in _split_for_fallback(continuation):
                                    stream_callback(sent_chunk, section_num, sub_num, "token")
                    else:
                        continuation = self.llm.chat_completion(
                            continue_msg, temperature=0.7, max_tokens=call_max_tokens // 2
                        )
                    if continuation:
                        sub_text += "\n" + continuation
                        sub_words = count_chinese_chars(sub_text)
                if sub_words < target_words * settings.WRITER_ACCEPT_THRESHOLD:
                    logger.info(f"[{task_id[:8]}] 第{section_num}.{sub_num}小节续写{expand_attempts}次后仍不足 ({sub_words}/{target_words}字)，接受当前长度")

                # --- 句子完整性补全 ---
                _last_chars = sub_text.rstrip()[-20:]
                _sentence_ends = {'。', '！', '？', '」', '』', '"', '"', '…', '~', '——'}
                if _last_chars and not any(_last_chars.rstrip().endswith(c) for c in _sentence_ends):
                    try:
                        _finish_msg = [
                            {"role": "system", "content": "请完成上一段文字中未写完的最后一句话。只输出剩余部分，不要重复已有内容。"},
                            {"role": "user", "content": f"上文：...{sub_text[-200:]}"},
                        ]
                        _finish = self.llm.chat_completion(
                            _finish_msg, temperature=0.3, max_tokens=200
                        )
                        if _finish and len(_finish) < 200:
                            sub_text += _finish
                    except Exception:
                        pass

                # --- 精简（超出目标 30% 时触发） ---
                if sub_words > target_words * 1.3:
                    condense_msg = [
                        {"role": "system", "content": "请精简以下文本，保持核心情节和风格不变，删除冗余描述。"},
                        {"role": "user", "content": f"目标 {target_words} 字，当前 {sub_words} 字。精简：\n{sub_text}"},
                    ]
                    condensed = self.llm.chat_completion(
                        condense_msg, temperature=0.3, max_tokens=call_max_tokens
                    )
                    if condensed:
                        sub_text = condensed
                        sub_words = count_chinese_chars(sub_text)

                # --- 累积 ---
                section_text += f"【{sub_title}】\n{sub_text}\n\n"
                full_draft += f"【{sub_title}】\n{sub_text}\n\n"
                previous_sub_texts.append(sub_text)  # P2: 追踪用于重复检测

                if handover_note:
                    section_handover_parts.append(handover_note)
                if backref:
                    backref_suggestions.extend(backref)

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
                    pass  # AI 检测失败不影响写作

                # --- 分节审阅检查（由 WRITER_REVIEW_TRIGGER_SUBS/CHARS 配置） ---
                _review_subs_done = sum(
                    1 for b in full_draft.split("【") if "】" in b
                )
                _review_chars = len(section_text) + len(sub_text)
                _trigger_subs = settings.WRITER_REVIEW_TRIGGER_SUBS
                _trigger_chars = settings.WRITER_REVIEW_TRIGGER_CHARS
                if _review_subs_done >= _trigger_subs and _review_subs_done % _trigger_subs == 0 or (
                    _review_subs_done >= 1 and _review_chars > _trigger_chars):
                    try:
                        from ..agents.reviewer import Reviewer
                        _reviewer = Reviewer()
                        _style_for_review = style if isinstance(style, dict) else {}
                        _review_snapshot = (
                            section_text + f"【{sub_title}】\n{sub_text}\n\n"
                        )[-8000:]

                        def _run_section_review():
                            try:
                                result = _reviewer.review_section(
                                    section_num, topic, _style_for_review, _review_snapshot)
                                if blackboard and result:
                                    _reviews = blackboard.get(task_id, "section_reviews") or []
                                    if isinstance(_reviews, str):
                                        _reviews = []
                                    for r in _reviews:
                                        if r.get("section") == section_num and r.get("subsection") == sub_num:
                                            r["status"] = "done"
                                            r["score"] = result.get("score")
                                            break
                                    blackboard.set(task_id, "section_reviews", _reviews)
                            except Exception:
                                logger.warning(f"[{task_id[:8]}] 第{section_num}节第{sub_num}小节审阅失败",
                                               exc_info=True)
                                if blackboard:
                                    _reviews = blackboard.get(task_id, "section_reviews") or []
                                    if isinstance(_reviews, str):
                                        _reviews = []
                                    for r in _reviews:
                                        if r.get("section") == section_num and r.get("subsection") == sub_num:
                                            r["status"] = "failed"
                                            break
                                    blackboard.set(task_id, "section_reviews", _reviews)

                        t = threading.Thread(target=_run_section_review, daemon=True)
                        t.start()
                        if blackboard:
                            _reviews = blackboard.get(task_id, "section_reviews") or []
                            if isinstance(_reviews, str):
                                _reviews = []
                            _reviews.append({
                                "section": section_num, "subsection": sub_num,
                                "chars": _review_chars, "status": "pending",
                            })
                            blackboard.set(task_id, "section_reviews", _reviews)
                    except Exception:
                        logger.warning(f"[{task_id[:8]}] 启动审阅线程失败", exc_info=True)

                # --- 切块入库 ---
                chunks = chunk_text(sub_text, settings.CHUNK_SIZE, settings.CHUNK_OVERLAP)
                for chunk in chunks:
                    vector_store.add_text(text=chunk, metadata={
                        "task_id": task_id,
                        "section": section_num,
                        "subsection": sub_num,
                        "title": sub_title,
                        "topic": topic,
                    })

                cm.add_subsection(sub_text, section_num)

                # --- v0.9.1: 更新 token 消耗到黑板 ---
                try:
                    from ..utils.llm_client import get_cumulative_tokens
                    blackboard.set(task_id, "token_usage", get_cumulative_tokens())
                except Exception:
                    pass

                # --- 小节完成事件 ---
                if stream_callback:
                    stream_callback(sub_text, section_num, sub_num, "section_end")

            # B2: 子节循环内检测到停止信号，跳出外层 while
            if should_stop:
                logger.info(f"[{task_id[:8]}] 应停止信号退出写作循环")
                break

            # --- 节尾汇总 ---
            section_texts[section_num] = section_text
            if section_handover_parts:
                prev_handover = {
                    "from_section": section_num,
                    "to_section": section_num + 1,
                    "foreshadowing": "; ".join(
                        h.get("foreshadowing", "") for h in section_handover_parts if h.get("foreshadowing")
                    ) or "无",
                    "character_state": "; ".join(
                        h.get("character_state", "") for h in section_handover_parts if h.get("character_state")
                    ) or "无",
                    "open_threads": "; ".join(
                        h.get("open_threads", "") for h in section_handover_parts if h.get("open_threads")
                    ) or "无",
                }
                handover_notes.append(prev_handover)
                if stream_callback:
                    stream_callback("", section_num, 0, "handover")

            # --- 角色状态更新 ---
            if character_arcs:
                cm_char = CharacterManager()
                character_arcs = cm_char.update_states(
                    characters, character_arcs, section_text, section_num
                )
                if blackboard:
                    blackboard.set(task_id, "character_arcs", character_arcs)

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

            # --- 交互模式检查点 ---
            if interactive and on_section_done:
                should_continue = on_section_done(
                    section_num,
                    section_texts=dict(section_texts),
                    handover_notes=list(handover_notes),
                    backref_suggestions=list(backref_suggestions),
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
        }

    # ═══ P0: 硬约束构建 ═══

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
            for k in ("foreshadowing", "character_state", "open_threads", "new_facts")
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
                    return brief
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
        return "上一节留下的交接笔记：\n  " + "\n  ".join(parts) if parts else "（上节无遗留线索）"

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
                              target_goal=""):
        """生成正文，若不满足硬约束则重试一次。"""
        import time as _time

        def _do_generate(msgs, temp):
            raw = ""
            if stream_callback:
                stream_callback("", section_num, sub_num, "section_start")
                try:
                    for token in self.llm.chat_completion_stream(
                        msgs, temperature=temp, max_tokens=call_max_tokens, top_p=0.9
                    ):
                        raw += token
                        stream_callback(token, section_num, sub_num, "token")
                except Exception:
                    raw = self.llm.chat_completion(
                        msgs, temperature=temp, max_tokens=call_max_tokens, top_p=0.9
                    )
                    if raw:
                        for sent_chunk in _split_for_fallback(raw):
                            stream_callback(sent_chunk, section_num, sub_num, "token")
            else:
                raw = self.llm.chat_completion(
                    msgs, temperature=temp, max_tokens=call_max_tokens, top_p=0.9
                )
            return raw

        # 第一次尝试 (P4: temperature=0.5)
        raw_output = _do_generate(messages, 0.5)

        # 大纲锁校验 (最多重试2次)
        from ..rule_checks import _extract_lock_keywords
        events = mandatory_events_text
        outline_retries = 0
        while events and events != "（本节无硬性事件约束）" and outline_retries < 2:
            import re
            event_descs = re.findall(r'【必须】(.+)', events)
            if not event_descs:
                break
            violations = []
            for ev in event_descs:
                keywords = _extract_lock_keywords({"title": ev, "description": ev})
                if keywords:
                    hits = sum(1 for kw in keywords if kw in raw_output)
                    if hits < len(keywords) * 0.5:
                        violations.append(ev)
            if not violations:
                break
            logger.warning(
                f"[writer] 第{section_num}.{sub_num}小节硬约束违规 {len(violations)}项，重试{outline_retries+1}/2")
            violation_text = "\n".join(f"  - 【缺失】{v}" for v in violations)
            retry_msg = (
                f"【强制重写】上一版以下事件未出现在正文中：\n"
                f"{violation_text}\n\n"
                f"请严格确保上述所有事件出现在正文中。不要省略。"
            )
            retry_messages = messages + [
                {"role": "assistant", "content": raw_output[:500]},
                {"role": "user", "content": retry_msg},
            ]
            raw_output = _do_generate(retry_messages, 0.3)
            outline_retries += 1

        # 角色行为违规检测 (最多重试1次)
        if characters:
            char_violations = self._check_character_violations(raw_output, characters)
            if char_violations:
                logger.warning(
                    f"[writer] 第{section_num}.{sub_num}小节角色违规 {len(char_violations)}项，重试")
                violation_text = "\n".join(f"  - {v}" for v in char_violations)
                retry_msg = (
                    f"【强制重写】上一版出现以下角色行为违规：\n"
                    f"{violation_text}\n\n"
                    f"请重写本节，严格遵守角色行为约束。"
                )
                retry_messages = messages + [
                    {"role": "assistant", "content": raw_output[:500]},
                    {"role": "user", "content": retry_msg},
                ]
                raw_output = _do_generate(retry_messages, 0.3)

        # P2: 重复检测（jieba TF-IDF + 轻量 LLM 节拍验证，方案 C）
        retry_reasons = []

        if previous_texts and len(previous_texts) > 0:
            from ..repetition_checker import check_subsection_quality
            quality = check_subsection_quality(
                raw_output, previous_texts, prev_sub_text, target_goal)
            if not quality["pass"]:
                reason = (
                    f"与第{quality['repetition']['similar_section']}节高度相似"
                    f"({quality['repetition']['max_similarity']:.2f})，情节无新进展"
                )
                beat_info = quality.get("beat_check", {}) if quality.get("beat_check") else {}
                if beat_info.get("what"):
                    reason += f"（{beat_info['what']}）"
                retry_reasons.append(reason)

        if retry_reasons:
            logger.warning(
                f"[writer] 第{section_num}.{sub_num}小节质量不合格: {'; '.join(retry_reasons)}，重试")
            retry_msg = (
                f"【强制重写】上一版存在以下问题：\n"
                + "\n".join(f"  - {r}" for r in retry_reasons) + "\n\n"
                f"请重写本节，避免重复前面的情节模式，引入新的场景或角色互动。"
            )
            retry_messages = messages + [
                {"role": "assistant", "content": raw_output[:500]},
                {"role": "user", "content": retry_msg},
            ]
            raw_output = _do_generate(retry_messages, 0.3)

        return raw_output

    def _extract_handover(self, section_text: str, section_num: int, sub_num: int = 0,
                          character_context: str = "",
                          event_graph: EventGraph | None = None) -> dict | None:
        """独立 LLM 调用：从纯正文中提取交接信息（伏笔/人物状态/待承接/事实/事件回收）。

        v3: 替代 _parse_output() 的正则切分。Writer 输出纯正文，此方法做结构化提取。
        """
        import json as _json
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
                return result
        except Exception:
            logger.warning(f"交接信息提取失败 (第{section_num}.{sub_num}小节)")
        return None

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
