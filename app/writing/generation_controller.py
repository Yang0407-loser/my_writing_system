"""LLM execution policy extracted from Writer without changing its behavior."""

from __future__ import annotations

import hashlib
import logging
import re
import time
from collections.abc import Callable

from ..config import settings
from ..repetition_checker import check_subsection_quality
from ..rule_checks import _extract_lock_keywords
from ..utils.word_counter import count_chinese_chars
from .contracts import GenerationArtifact


logger = logging.getLogger("writing_system.writer")


class GenerationController:
    def __init__(
        self,
        llm,
        *,
        character_violation_checker: Callable[[str, list[dict]], list[str]],
        fallback_splitter: Callable[[str], list[str]],
    ) -> None:
        self.llm = llm
        self.character_violation_checker = character_violation_checker
        self.fallback_splitter = fallback_splitter

    def generate(
        self,
        *,
        messages,
        call_max_tokens,
        stream_callback,
        section_num,
        sub_num,
        mandatory_events_text,
        characters=None,
        previous_texts=None,
        prev_sub_text="",
        target_goal="",
    ) -> GenerationArtifact:
        started = time.perf_counter()
        attempts: list[dict] = []

        def do_generate(msgs, temperature, reason):
            raw = ""
            if stream_callback:
                stream_callback("", section_num, sub_num, "section_start")
                try:
                    for token in self.llm.chat_completion_stream(
                        msgs, temperature=temperature, max_tokens=call_max_tokens, top_p=0.9
                    ):
                        raw += token
                        stream_callback(token, section_num, sub_num, "token")
                except Exception:
                    raw = self.llm.chat_completion(
                        msgs, temperature=temperature, max_tokens=call_max_tokens, top_p=0.9
                    )
                    if raw:
                        for sent_chunk in self.fallback_splitter(raw):
                            stream_callback(sent_chunk, section_num, sub_num, "token")
            else:
                raw = self.llm.chat_completion(
                    msgs, temperature=temperature, max_tokens=call_max_tokens, top_p=0.9
                )
            attempts.append({"reason": reason, "temperature": temperature, "output_chars": len(raw)})
            return raw

        raw_output = do_generate(messages, 0.5, "initial")
        events = mandatory_events_text
        outline_retries = 0
        while events and events != "（本节无硬性事件约束）" and outline_retries < 2:
            event_descs = re.findall(r"【必须】(.+)", events)
            if not event_descs:
                break
            violations = []
            for event in event_descs:
                keywords = _extract_lock_keywords({"title": event, "description": event})
                if keywords and sum(keyword in raw_output for keyword in keywords) < len(keywords) * 0.5:
                    violations.append(event)
            if not violations:
                break
            logger.warning(
                "[writer] 第%s.%s小节硬约束违规 %s项，重试%s/2",
                section_num, sub_num, len(violations), outline_retries + 1,
            )
            violation_text = "\n".join(f"  - 【缺失】{item}" for item in violations)
            retry_messages = messages + [
                {"role": "assistant", "content": raw_output[:500]},
                {"role": "user", "content": (
                    "【强制重写】上一版以下事件未出现在正文中：\n"
                    f"{violation_text}\n\n请严格确保上述所有事件出现在正文中。不要省略。"
                )},
            ]
            raw_output = do_generate(retry_messages, 0.3, "mandatory_events")
            outline_retries += 1

        if characters:
            char_violations = self.character_violation_checker(raw_output, characters)
            if char_violations:
                logger.warning(
                    "[writer] 第%s.%s小节角色违规 %s项，重试",
                    section_num, sub_num, len(char_violations),
                )
                violation_text = "\n".join(f"  - {item}" for item in char_violations)
                retry_messages = messages + [
                    {"role": "assistant", "content": raw_output[:500]},
                    {"role": "user", "content": (
                        "【强制重写】上一版出现以下角色行为违规：\n"
                        f"{violation_text}\n\n请重写本节，严格遵守角色行为约束。"
                    )},
                ]
                raw_output = do_generate(retry_messages, 0.3, "character_violation")

        retry_reasons = []
        if previous_texts:
            quality = check_subsection_quality(raw_output, previous_texts, prev_sub_text, target_goal)
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
                "[writer] 第%s.%s小节质量不合格: %s，重试",
                section_num, sub_num, "; ".join(retry_reasons),
            )
            retry_messages = messages + [
                {"role": "assistant", "content": raw_output[:500]},
                {"role": "user", "content": (
                    "【强制重写】上一版存在以下问题：\n"
                    + "\n".join(f"  - {reason}" for reason in retry_reasons)
                    + "\n\n请重写本节，避免重复前面的情节模式，引入新的场景或角色互动。"
                )},
            ]
            raw_output = do_generate(retry_messages, 0.3, "repetition")

        return GenerationArtifact(
            raw_output=raw_output,
            draft=raw_output,
            generation_attempts=attempts,
            finish_reason="generated",
            latency_ms=round((time.perf_counter() - started) * 1000, 3),
            output_hash=hashlib.sha256(raw_output.encode("utf-8")).hexdigest(),
        )

    def adjust_length(
        self,
        draft: str,
        *,
        target_words: int,
        call_max_tokens: int,
        stream_callback,
        section_num: int,
        sub_num: int,
        task_id: str = "",
    ) -> GenerationArtifact:
        started = time.perf_counter()
        attempts: list[dict] = []
        sub_text = draft
        sub_words = count_chinese_chars(sub_text)
        expand_attempts = 0
        while (
            sub_words < target_words * settings.WRITER_EXPAND_THRESHOLD
            and expand_attempts < settings.WRITER_MAX_EXPAND_ATTEMPTS
        ):
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
                        for sent_chunk in self.fallback_splitter(continuation):
                            stream_callback(sent_chunk, section_num, sub_num, "token")
            else:
                continuation = self.llm.chat_completion(
                    continue_msg, temperature=0.7, max_tokens=call_max_tokens // 2
                )
            attempts.append({"reason": "expand", "temperature": 0.7, "output_chars": len(continuation)})
            if continuation:
                sub_text += "\n" + continuation
                sub_words = count_chinese_chars(sub_text)
        if sub_words < target_words * settings.WRITER_ACCEPT_THRESHOLD:
            logger.info(
                "[%s] 第%s.%s小节续写%s次后仍不足 (%s/%s字)，接受当前长度",
                task_id[:8] or "writer", section_num, sub_num, expand_attempts, sub_words, target_words,
            )

        last_chars = sub_text.rstrip()[-20:]
        sentence_ends = {"。", "！", "？", "」", "』", '"', "…", "~", "——"}
        if last_chars and not any(last_chars.rstrip().endswith(char) for char in sentence_ends):
            try:
                finish_msg = [
                    {"role": "system", "content": "请完成上一段文字中未写完的最后一句话。只输出剩余部分，不要重复已有内容。"},
                    {"role": "user", "content": f"上文：...{sub_text[-200:]}"},
                ]
                finish = self.llm.chat_completion(finish_msg, temperature=0.3, max_tokens=200)
                attempts.append({"reason": "finish_sentence", "temperature": 0.3, "output_chars": len(finish or "")})
                if finish and len(finish) < 200:
                    sub_text += finish
            except Exception:
                logger.warning(
                    "[%s] 段落收尾补全失败 (第%s.%s节)，跳过补全",
                    task_id[:8] or "writer", section_num, sub_num, exc_info=True,
                )

        if sub_words > target_words * 1.3:
            condense_msg = [
                {"role": "system", "content": "请精简以下文本，保持核心情节和风格不变，删除冗余描述。"},
                {"role": "user", "content": f"目标 {target_words} 字，当前 {sub_words} 字。精简：\n{sub_text}"},
            ]
            condensed = self.llm.chat_completion(
                condense_msg, temperature=0.3, max_tokens=call_max_tokens
            )
            attempts.append({"reason": "condense", "temperature": 0.3, "output_chars": len(condensed or "")})
            if condensed:
                sub_text = condensed

        return GenerationArtifact(
            raw_output=draft,
            draft=sub_text,
            generation_attempts=attempts,
            finish_reason="length_adjusted",
            latency_ms=round((time.perf_counter() - started) * 1000, 3),
            output_hash=hashlib.sha256(sub_text.encode("utf-8")).hexdigest(),
        )
