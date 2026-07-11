"""抽卡Agent —— 为指定步骤生成3-5个不同走向的方案卡片。"""

import uuid
import json as _json
import logging
from .base import BaseAgent
from ..utils.prompt_templates import CARD_DRAW_PROMPTS
from ..utils.json_parser import parse_json

logger = logging.getLogger("writing_system.card_drawer")


class CardDrawer(BaseAgent):
    """抽卡助手：为创作各环节提供多方案选择。"""

    def run(self, **kwargs) -> dict:
        """兼容 BaseAgent 抽象接口"""
        cards = self.draw_cards(
            step=kwargs.get("step", "generic"),
            context=kwargs.get("context", {}),
            num_cards=kwargs.get("num_cards", 4),
            user_requirement=kwargs.get("user_requirement", ""),
        )
        return {"cards": cards}

    def draw_cards(
        self,
        step: str,
        context: dict | None = None,
        num_cards: int = 4,
        user_requirement: str = "",
    ) -> list[dict]:
        """为指定步骤生成多张卡片。

        Args:
            step: 步骤标识 (world_setting | protagonist | outline | outline_refine | writing)
            context: 当前上下文，结构随step不同
            num_cards: 生成卡片数量 (3-5)
            user_requirement: 用户额外要求

        Returns:
            [{id, step, title, summary, content, highlights, quality_score, is_adopted}]
        """
        context = context or {}
        num = max(3, min(5, num_cards))

        # 选择对应步骤的prompt
        prompt_key = step
        if step == "plot_development":
            sub = context.get("sub_type", "next_station")
            prompt_key = f"plot_{sub}"
        prompt_template = CARD_DRAW_PROMPTS.get(prompt_key)
        if not prompt_template:
            logger.warning(f"未知步骤 {step}/{prompt_key}，使用通用prompt")
            prompt_template = CARD_DRAW_PROMPTS.get("generic", CARD_DRAW_PROMPTS.get("world_setting", ""))

        try:
            prompt = self._build_prompt(prompt_template, step, context, num, user_requirement)
        except KeyError as e:
            logger.error(f"Prompt变量缺失: {e}", exc_info=True)
            return self._fallback_cards(step, num)

        messages = [
            {"role": "system", "content": f"你是一位创意写作助手。请为「{self._step_label(step)}」步骤生成{num}个不同走向的方案。输出JSON数组。"},
            {"role": "user", "content": prompt},
        ]

        try:
            max_tokens = {"outline": 8000, "writing": 6000}.get(step, 4000)
            response = self.llm.chat_completion(
                messages, temperature=0.8, max_tokens=max_tokens, json_mode=True
            )
            result = parse_json(response)
            if isinstance(result, list):
                return self._normalize_cards(result, step, num)
            if isinstance(result, dict):
                # 尝试从 dict 中提取卡片数组
                for key in ("cards", "options", "results", "items", "data"):
                    if key in result and isinstance(result[key], list):
                        return self._normalize_cards(result[key], step, num)
                # dict 且无数组字段 → 重试一次，明确要求数组
                logger.warning(f"抽卡返回 dict 而非数组，重试: step={step}")
                retry_msgs = messages + [
                    {"role": "assistant", "content": response[:300]},
                    {"role": "user", "content": "请输出 JSON 数组格式！必须以 [ 开头，包含所有方案。不要输出单个对象。"},
                ]
                response = self.llm.chat_completion(
                    retry_msgs, temperature=0.4, max_tokens=max_tokens, json_mode=True
                )
                result = parse_json(response)
                if isinstance(result, dict):
                    for key in ("cards", "options", "results", "items", "data"):
                        if key in result and isinstance(result[key], list):
                            return self._normalize_cards(result[key], step, num)
                if isinstance(result, list):
                    return self._normalize_cards(result, step, num)
                logger.warning(f"重试后仍无法解析为数组: step={step}")
                return self._fallback_cards(step, num)
            logger.warning(f"抽卡返回格式异常: {type(result)}")
            return self._fallback_cards(step, num)
        except Exception as e:
            logger.error(f"抽卡失败: {e}", exc_info=True)
            return self._fallback_cards(step, num)

    def draw_cards_streaming(
        self, step: str, context: dict | None = None,
        num_cards: int = 4, user_requirement: str = "",
    ):
        """流式版本的抽卡——逐token返回。"""
        context = context or {}
        num = max(3, min(5, num_cards))
        prompt_template = CARD_DRAW_PROMPTS.get(step, CARD_DRAW_PROMPTS.get("generic", ""))
        prompt = self._build_prompt(prompt_template, step, context, num, user_requirement)
        messages = [
            {"role": "system", "content": f"你是一位创意写作助手。请为「{self._step_label(step)}」生成{num}个方案。输出JSON数组。"},
            {"role": "user", "content": prompt},
        ]
        full = ""
        try:
            for token in self.llm.chat_completion_stream(messages, temperature=0.8, max_tokens=4000):
                full += token
                yield token
        except Exception:
            # fallback to non-streaming
            full = self.llm.chat_completion(messages, temperature=0.8, max_tokens=4000)
        try:
            result = parse_json(full)
            cards = result if isinstance(result, list) else result.get("cards", [])
            yield self._normalize_cards(cards, step, num)
        except Exception:
            yield self._fallback_cards(step, num)

    def redraw_card(
        self, step: str, context: dict, card_index: int,
        user_feedback: str = "",
    ) -> dict:
        """根据用户反馈重新生成单张卡片。"""
        cards = self.draw_cards(step, context, num_cards=3, user_requirement=user_feedback)
        idx = min(card_index, len(cards) - 1)
        return cards[idx] if cards else self._fallback_cards(step, 1)[0]

    def _build_prompt(self, template: str, step: str, context: dict, num: int, requirement: str) -> str:
        """构建prompt，安全处理缺失变量。自动注入链式约束和题材上下文。"""
        chain_constraints = self._build_chain_constraint_text(context, step)

        # P8: 题材注入
        genre = context.get("genre", "")
        genre_context = ""
        if genre:
            from ..utils.genre_presets import get_genre_context
            genre_context = get_genre_context(genre)

        defaults = {
            "num_cards": str(num),
            "topic": context.get("topic", ""),
            "world_setting": context.get("world_setting", "（无）"),
            "story_synopsis": context.get("story_synopsis", "（无）"),
            "current_setting": context.get("current_setting", "（无）"),
            "outline_text": context.get("outline_text", "（无）"),
            "chapter_context": context.get("chapter_context", "（无）"),
            "character_context": context.get("character_context", "（无）"),
            "step_context": context.get("step_context", "（无）"),
            "user_requirement": f"\n用户额外要求：{requirement}" if requirement else "",
            "chapter_num": str(context.get("chapter_num", 0)),
            "previous_content": context.get("previous_content", "（无前文）"),
            "chain_constraints": chain_constraints,
            "genre_context": genre_context,
            # 支线抽卡专用上下文
            "characters": context.get("characters", "（无）"),
            "factions": context.get("factions", "（无）"),
            "existing_subplots": context.get("existing_subplots", "（无）"),
            "outline_summary": context.get("outline_summary", "（无）"),
            # 剧情抽卡专用上下文
            "node_title": context.get("node_title", ""),
            "node_key_points": context.get("node_key_points", "（无）"),
            "parent_volume": context.get("parent_volume", ""),
            "sibling_titles": context.get("sibling_titles", ""),
            "sibling_descriptions": context.get("sibling_descriptions", "（无）"),
            "existing_chars": context.get("existing_chars", "（无）"),
            "existing_factions": context.get("existing_factions", "（无）"),
            "existing_locations": context.get("existing_locations", "（无）"),
            "existing_foreshadowings": context.get("existing_foreshadowings", "（无）"),
        }
        # 安全格式化: 缺失的变量用空字符串替代
        from string import Formatter
        tpl_keys = {fn for _, fn, _, _ in Formatter().parse(template) if fn}
        return template.format(**{k: defaults.get(k, "") for k in tpl_keys})

    @staticmethod
    def _build_chain_constraint_text(context: dict, current_step: str) -> str:
        """从上下文中提取 chain constraint 字段, 构建硬约束文本。"""
        constraints = []
        step_order = ["genre", "world_setting", "protagonist", "supporting_characters", "factions_card", "locations_card", "outline"]
        labels = {
            "genre": "题材", "world_setting": "世界观", "protagonist": "主角设定",
            "supporting_characters": "配角阵容", "factions_card": "势力格局",
            "locations_card": "地图节点", "outline": "大纲规划",
        }
        for key, value in sorted(context.items()):
            if key.startswith("constraint_") and value:
                step_name = key.replace("constraint_", "")
                label = labels.get(step_name, step_name)
                try:
                    obj = _json.loads(value) if isinstance(value, str) else value
                    # 格式化为易读的硬约束
                    if isinstance(obj, dict):
                        lines = [f"【硬约束】以下{label}是前序步骤已确定的结果，当前方案必须基于此设定，不得偏离："]
                        for k, v in obj.items():
                            if k == "volumes" or k == "chapters":
                                continue  # 跳过过长的嵌套
                            lines.append(f"  - {k}: {str(v)[:200]}")
                        constraints.append("\n".join(lines))
                    else:
                        constraints.append(f"【硬约束】已确定的{label}（不得偏离）:\n{str(obj)[:500]}")
                except (_json.JSONDecodeError, TypeError):
                    constraints.append(f"【硬约束】已确定的{label}:\n{str(value)[:500]}")
        return "\n\n".join(constraints) if constraints else "（无前序约束，可自由发挥）"


    def _normalize_cards(self, raw_cards: list, step: str, expected_num: int) -> list[dict]:
        """标准化卡片格式。"""
        cards = []
        for i, raw in enumerate(raw_cards[:expected_num]):
            if isinstance(raw, str):
                cards.append({
                    "id": str(uuid.uuid4()),
                    "step": step,
                    "title": f"方案{chr(65 + i)}",
                    "summary": raw[:150],
                    "content": {"text": raw},
                    "highlights": [],
                    "quality_score": None,
                    "is_adopted": False,
                })
            elif isinstance(raw, dict):
                cards.append({
                    "id": raw.get("id", str(uuid.uuid4())),
                    "step": step,
                    "title": raw.get("title", f"方案{chr(65 + i)}"),
                    "summary": raw.get("summary", str(raw.get("content", ""))[:150]),
                    "content": raw.get("content", raw),
                    "highlights": raw.get("highlights", raw.get("tags", [])),
                    "quality_score": raw.get("quality_score"),
                    "is_adopted": False,
                })
        return cards

    def _fallback_cards(self, step: str, num: int) -> list[dict]:
        """当LLM调用失败时返回占位卡片。"""
        return [
            {
                "id": str(uuid.uuid4()),
                "step": step,
                "title": f"方案{chr(65 + i)}（生成失败，请重试）",
                "summary": "AI生成超时或返回格式错误，请点击重抽。",
                "content": {},
                "highlights": [],
                "quality_score": None,
                "is_adopted": False,
            }
            for i in range(num)
        ]

    def _step_label(self, step: str) -> str:
        return {
            "genre": "题材选择",
            "world_setting": "世界观设定",
            "protagonist": "主角设定",
            "supporting_characters": "配角设计",
            "factions_card": "势力格局",
            "locations_card": "地图设计",
            "outline": "大纲规划",
            "outline_refine": "大纲完善",
            "writing": "正文方向",
            "subplot": "支线故事",
            "foreshadowing": "伏笔设计",
            "generic": "创作方案",
        }.get(step, step)
