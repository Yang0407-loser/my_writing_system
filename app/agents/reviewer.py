from .base import BaseAgent
from .character_formatter import CharacterFormatter
from ..utils.prompt_templates import SECTION_REVIEW_PROMPT, GLOBAL_REVIEW_PROMPT
from ..utils.json_parser import parse_json
from ..utils.word_counter import count_chinese_chars
import json
from ..utils.style_brief import StyleSummarizer


class Reviewer(BaseAgent):
    """审阅者：分节审阅 + 全局终审（含交接笔记洞察）。"""

    def review_section(
        self, section_num: int, topic: str, style: dict, section_draft: str
    ) -> dict:
        style_brief = style.get("style_brief", "") if isinstance(style, dict) else ""
        style_summary = style_brief if style_brief else (
            f"情感强度{style.get('emotion_intensity', 50)}/100"
        )
        style_structured = StyleSummarizer.for_reviewer(style) if isinstance(style, dict) else {}
        prompt = SECTION_REVIEW_PROMPT.format(
            section=section_num,
            topic=topic,
            style_summary=style_summary,
            style_structured=json.dumps(style_structured, ensure_ascii=False),
            word_count=count_chinese_chars(section_draft),
            draft=section_draft[:8000],
        )
        messages = [
            {"role": "system", "content": "你是一位专业审阅编辑，擅长发现文字亮点并给出建设性意见。请以 JSON 格式输出审阅结果。"},
            {"role": "user", "content": prompt},
        ]
        response = self.llm.chat_completion(messages, temperature=0.3, max_tokens=1500)
        self.last_raw_response = response
        try:
            result = parse_json(response)
            # 确保新字段有默认值
            result.setdefault("scores", {})
            result.setdefault("highlight", {})
            result.setdefault("lowlight", {})
            result.setdefault("rewrite_target", "")
            return result
        except ValueError:
            return {
                "score": 6, "scores": {}, "highlight": {}, "lowlight": {},
                "consistency_notes": "格式解析失败", "improvement": "",
                "rewrite_target": "",
            }

    def review_global(
        self,
        topic: str,
        style: dict,
        section_summaries: str,
        total_words: int,
        handover_chain: str = "",
        fix_summary: str = "",
        characters: list[dict] | None = None,
        character_arcs: list[dict] | None = None,
        subplot_context: str = "",
        relation_context: str = "",
        section_scores: str = "",
    ) -> dict:
        style_brief = style.get("style_brief", "") if isinstance(style, dict) else ""
        style_summary = style_brief if style_brief else (
            f"情感强度{style.get('emotion_intensity', 50)}/100"
        )
        style_structured = StyleSummarizer.for_reviewer(style) if isinstance(style, dict) else {}

        character_consistency_context = CharacterFormatter.build_context(characters, character_arcs)

        prompt = GLOBAL_REVIEW_PROMPT.format(
            topic=topic,
            style_summary=style_summary,
            style_structured=json.dumps(style_structured, ensure_ascii=False),
            style_brief=style_brief,
            total_words=total_words,
            section_summaries=section_summaries,
            section_scores=section_scores or "（无分节评分数据）",
            handover_chain=handover_chain or "（无交接笔记）",
            fix_summary=fix_summary or "（无修正）",
            character_consistency_context=character_consistency_context,
            subplot_context=subplot_context or "（无支线数据）",
            relation_context=relation_context or "（无关系数据）",
        )
        messages = [
            {"role": "system", "content": "你是一位资深文学编辑，擅长全局评估并给出具体可行的改进建议。请以 JSON 格式输出审阅结果。"},
            {"role": "user", "content": prompt},
        ]
        response = self.llm.chat_completion(messages, temperature=0.4, max_tokens=3000)
        self.last_raw_response = response
        try:
            result = parse_json(response)
            for field in ("chapter_scores", "pacing_issues", "subplot_health",
                          "character_arc_health", "top_3_actions"):
                result.setdefault(field, [])
            result.setdefault("tension_curve", "")
            result.setdefault("style_adherence", "")
            return result
        except ValueError:
            return {
                "global_score": 6,
                "chapter_scores": [], "tension_curve": "", "pacing_issues": [],
                "style_adherence": "", "subplot_health": [], "character_arc_health": [],
                "top_3_actions": [],
                "strength": "", "weakness": "", "suggestion": "",
                "handover_insight": "", "character_consistency": "", "character_arc_progress": "",
            }

    def review_continuity(
        self,
        prev_section_text: str,
        next_section_text: str,
        prev_handover: dict | None = None,
        characters: list[dict] | None = None,
        character_arcs: list[dict] | None = None,
    ) -> dict:
        """评估两个相邻章节之间的衔接质量。

        Args:
            prev_section_text: 前一节正文（截取末尾 3000 字）
            next_section_text: 当前节正文（截取开头 3000 字）
            prev_handover: 前一节的交接笔记
            characters: 角色卡列表
            character_arcs: 角色弧线

        Returns:
            {"continuity_score": 7, "issues": [...], "connections": [...], "suggestion": ""}
        """
        prev_text = prev_section_text[-3000:] if len(prev_section_text) > 3000 else prev_section_text
        next_text = next_section_text[:3000] if len(next_section_text) > 3000 else next_section_text

        handover_str = ""
        if prev_handover:
            handover_str = (
                f"前一节交接笔记：\n"
                f"  伏笔: {prev_handover.get('foreshadowing', '无')}\n"
                f"  人物状态: {prev_handover.get('character_state', '无')}\n"
                f"  待承接: {prev_handover.get('open_threads', '无')}\n"
            )

        char_str = CharacterFormatter.build_context(characters, character_arcs)

        prompt = f"""你是一位严格的编辑，专门检查故事章节之间的衔接质量。

## 前一节末尾
{prev_text}

## 后一节开头
{next_text}

{handover_str}

## 角色设定
{char_str}

请从以下维度评估两节之间的承续程度，并以 JSON 格式输出：

1. **情节连贯性**：情节是否自然顺畅地从前一节过渡到后一节？有没有突兀的跳跃？
2. **角色状态一致性**：角色的情绪、处境是否合理延续？前一节结束时的状态和下一节开始是否匹配？
3. **伏笔承接**：前一节埋下的伏笔在下一节是否有回应？待承接的线索是否被忽略？
4. **时间线合理性**：时间推进是否清晰合理？
5. **语气一致性**：两节之间的写作语气是否有不协调的突变？

请输出：
{{
    "continuity_score": 1-10,
    "issues": [
        {{"severity": "high|medium|low", "dimension": "情节|角色|伏笔|时间|语气", "description": "具体问题"}}
    ],
    "connections": ["两个章节之间承接得好的地方，如'前一节结尾的孤独感在下一节开头通过环境描写自然延续'"],
    "suggestion": "整体改进建议（1-2句话）"
}}"""

        messages = [
            {"role": "system", "content": "你是一位严格的章节衔接编辑。请以 JSON 格式输出。"},
            {"role": "user", "content": prompt},
        ]
        resp = self.llm.chat_completion(messages, temperature=0.3, max_tokens=1200)
        self.last_raw_response = resp
        try:
            return parse_json(resp)
        except ValueError:
            return {"continuity_score": 6, "issues": [], "connections": [], "suggestion": ""}

    def review_continuity_chain(
        self,
        section_texts: dict[int, str],
        handover_chain: list[dict],
        characters: list[dict] | None = None,
        character_arcs: list[dict] | None = None,
    ) -> list[dict]:
        """评估全篇所有相邻章节之间的衔接质量。

        Returns:
            [{"from_section": 1, "to_section": 2, "continuity_score": 8, "issues": [...], ...}, ...]
        """
        results = []
        sorted_keys = sorted(section_texts.keys())
        for i in range(len(sorted_keys) - 1):
            prev_key = sorted_keys[i]
            next_key = sorted_keys[i + 1]
            prev_handover = next(
                (h for h in handover_chain if h.get("from_section") == prev_key), None
            )
            result = self.review_continuity(
                section_texts[prev_key], section_texts[next_key],
                prev_handover=prev_handover,
                characters=characters, character_arcs=character_arcs,
            )
            result["from_section"] = prev_key
            result["to_section"] = next_key
            results.append(result)
        return results

    # 兼容旧接口
    def run(self, topic: str, style: dict, draft: str) -> dict:
        return self.review_global(
            topic=topic, style=style, section_summaries=draft[:8000],
            total_words=count_chinese_chars(draft),
        )
