"""对话模式Agent —— 上下文感知的结构化创作讨论。"""

import json
import logging
from .base import BaseAgent
from ..utils.json_parser import parse_json

logger = logging.getLogger("writing_system.dialogue")


QUICK_PROMPTS = {
    "analyze_direction": "请分析当前剧情的走向，指出可能存在的问题和机会",
    "suggest_foreshadowing": "请建议在当前章节可以安插哪些伏笔",
    "evaluate_pacing": "请评估当前章节的节奏和情绪曲线是否合理",
    "recommend_climax": "请推荐适合当前章节的爽点或高潮设计",
    "check_character_consistency": "请检查当前章节中角色的行为是否符合其设定",
    "brainstorm_twist": "请为当前章节设计1-2个反转或意外发展",
    "suggest_dialogue": "请为当前场景中的角色设计2-3段有张力的对话",
    "assess_readability": "请评估当前文本的可读性和读者体验",
}


class DialogueAgent(BaseAgent):
    """对话助手：在特定创作上下文中进行结构化讨论。"""

    def run(self, **kwargs) -> dict:
        """兼容 BaseAgent 抽象接口"""
        reply = self.chat(
            session_context=kwargs.get("session_context", {}),
            user_message=kwargs.get("user_message", ""),
        )
        return {"reply": reply}

    def chat(self, session_context: dict, user_message: str) -> str:
        """在给定上下文中回复用户的创作讨论。

        Args:
            session_context: {chapter, topic, world_setting, outline_context, character_context, ...}
            user_message: 用户的问题或讨论内容
        """
        ctx_str = self._build_context(session_context)
        messages = [
            {"role": "system", "content": f"""你是一位资深的创作顾问。你正在帮助作者讨论小说的创作细节。
当前创作上下文：
{ctx_str}

讨论规则：
1. 提供具体、可操作的建议，而非泛泛而谈
2. 如果作者的想法有逻辑漏洞，温和地指出
3. 提供多个选项供作者选择，而非只给一个答案
4. 保持中文交流，风格专业但不冷硬"""},
            {"role": "user", "content": user_message},
        ]
        try:
            return self.llm.chat_completion(messages, temperature=0.7, max_tokens=2000)
        except Exception as e:
            logger.error(f"对话失败: {e}", exc_info=True)
            return "抱歉，AI暂时无法回复。请稍后重试。"

    def summarize(self, session_context: dict, conversation_history: list[dict]) -> dict:
        """将讨论结果总结并转换为结构化数据。

        Args:
            session_context: 同chat()
            conversation_history: [{role, content}, ...]

        Returns:
            {summary, actions: [{action_type, target, description}]}
        """
        history_text = "\n".join(
            f"{'作者' if m['role'] == 'user' else 'AI'}: {m['content'][:300]}"
            for m in conversation_history[-10:]  # 取最近10轮
        )
        ctx_str = self._build_context(session_context)
        prompt = f"""请将以下创作讨论总结为结构化结果。

创作上下文：
{ctx_str}

讨论记录：
{history_text}

请以 JSON 格式输出：
{{
    "summary": "讨论的核心结论（200字以内）",
    "actions": [
        {{
            "action_type": "update_outline|add_character|add_foreshadowing|modify_scene|add_item|adjust_pacing|other",
            "target": "目标章节号或对象名称",
            "description": "具体要执行的操作描述"
        }}
    ],
    "key_decisions": ["本次讨论中做出的关键决策"]
}}

如果讨论没有产生明确的行动项，actions 返回空数组。"""
        try:
            resp = self.llm.chat_completion(
                [{"role": "system", "content": "你是一位创作讨论总结助手。请输出JSON。"},
                 {"role": "user", "content": prompt}],
                temperature=0.3, max_tokens=1000, json_mode=True,
            )
            return parse_json(resp) or {"summary": "", "actions": [], "key_decisions": []}
        except Exception:
            return {"summary": "", "actions": [], "key_decisions": []}

    def _build_context(self, ctx: dict) -> str:
        parts = []
        if ctx.get("chapter"):
            parts.append(f"当前章节：第{ctx['chapter']}章")
        if ctx.get("topic"):
            parts.append(f"小说主题：{ctx['topic']}")
        if ctx.get("world_setting"):
            parts.append(f"世界观：{ctx['world_setting'][:300]}")
        if ctx.get("outline_context"):
            parts.append(f"大纲上下文：{ctx['outline_context'][:500]}")
        if ctx.get("character_context"):
            parts.append(f"角色信息：{ctx['character_context'][:500]}")
        if ctx.get("foreshadowing_context"):
            parts.append(f"伏笔状态：{ctx['foreshadowing_context'][:300]}")
        return "\n".join(parts) if parts else "（无特定上下文）"


def get_quick_prompts() -> dict:
    return QUICK_PROMPTS
