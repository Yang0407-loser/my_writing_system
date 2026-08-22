import json
from .base import BaseAgent
from ..utils.prompt_templates import STYLE_ANALYSIS_PROMPT, OUTLINE_REVIEW_PROMPT
from ..utils.json_parser import parse_json


STYLE_PRESETS = {
    "中性": {
        "emotion_intensity": 50, "dialogue_ratio": 0.3,
        "sentence_preference": "balanced", "sensory_density": "medium",
        "narrative_density": 0.5, "adjective_density": 0.15,
        "paragraph_length_avg": 200, "dialogue_tag_style": "稀疏标记", "pacing": "中等",
    },
    "热血": {
        "emotion_intensity": 85, "dialogue_ratio": 0.2,
        "sentence_preference": "short", "sensory_density": "medium",
        "narrative_density": 0.5, "adjective_density": 0.12,
        "paragraph_length_avg": 120, "dialogue_tag_style": "稀疏标记", "pacing": "紧凑",
    },
    "冷峻": {
        "emotion_intensity": 35, "dialogue_ratio": 0.15,
        "sentence_preference": "long", "sensory_density": "medium",
        "narrative_density": 0.85, "adjective_density": 0.18,
        "paragraph_length_avg": 300, "dialogue_tag_style": "稀疏标记", "pacing": "舒缓",
    },
    "治愈": {
        "emotion_intensity": 50, "dialogue_ratio": 0.4,
        "sentence_preference": "balanced", "sensory_density": "rich",
        "narrative_density": 0.3, "adjective_density": 0.12,
        "paragraph_length_avg": 180, "dialogue_tag_style": "动作替代", "pacing": "舒缓",
    },
    "压抑": {
        "emotion_intensity": 70, "dialogue_ratio": 0.1,
        "sentence_preference": "long", "sensory_density": "rich",
        "narrative_density": 0.6, "adjective_density": 0.2,
        "paragraph_length_avg": 250, "dialogue_tag_style": "零标记", "pacing": "舒缓",
    },
    "紧迫": {
        "emotion_intensity": 90, "dialogue_ratio": 0.1,
        "sentence_preference": "short", "sensory_density": "sparse",
        "narrative_density": 0.75, "adjective_density": 0.05,
        "paragraph_length_avg": 80, "dialogue_tag_style": "零标记", "pacing": "急促",
    },
    "荒诞": {
        "emotion_intensity": 60, "dialogue_ratio": 0.25,
        "sentence_preference": "balanced", "sensory_density": "medium",
        "narrative_density": 0.4, "adjective_density": 0.2,
        "paragraph_length_avg": 200, "dialogue_tag_style": "稀疏标记", "pacing": "变速",
    },
}


class StyleAnalyzer(BaseAgent):
    """风格分析器：从参考文本提取风格简报 + 4 维旋钮。"""

    def analyze(self, reference_text: str = "") -> dict:
        """分析参考文本，返回 4 维风格参数。"""
        from ..utils.llm_client import set_cost_label
        set_cost_label("style_analyst")

        text = reference_text[:6000] if reference_text else ""
        if not text:
            return dict(STYLE_PRESETS["中性"])

        prompt = STYLE_ANALYSIS_PROMPT.format(reference_text=text)
        messages = [
            {"role": "system", "content": "你是一位文学编辑。请分析参考文本的写作风格，输出 JSON。"},
            {"role": "user", "content": prompt},
        ]
        response = self.llm.chat_completion(messages, temperature=0.3, max_tokens=1500)
        self.last_raw_response = response

        try:
            data = parse_json(response)
        except ValueError:
            data = {}

        return self._fill_defaults(data)

    def _fill_defaults(self, data: dict) -> dict:
        """用中性预设补齐缺失字段。"""
        defaults = STYLE_PRESETS["中性"]
        return {
            "emotion_intensity": int(data.get("emotion_intensity", defaults["emotion_intensity"])),
            "dialogue_ratio": float(data.get("dialogue_ratio", defaults["dialogue_ratio"])),
            "sentence_preference": data.get("sentence_preference", defaults["sentence_preference"]),
            "sensory_density": data.get("sensory_density", defaults["sensory_density"]),
            "narrative_density": float(data.get("narrative_density", defaults["narrative_density"])),
            "adjective_density": float(data.get("adjective_density", defaults["adjective_density"])),
            "paragraph_length_avg": int(data.get("paragraph_length_avg", defaults["paragraph_length_avg"])),
            "dialogue_tag_style": data.get("dialogue_tag_style", defaults["dialogue_tag_style"]),
            "pacing": data.get("pacing", defaults["pacing"]),
        }

    @staticmethod
    def get_preset(name: str) -> dict:
        return dict(STYLE_PRESETS.get(name, STYLE_PRESETS["中性"]))

    @staticmethod
    def list_presets() -> list[str]:
        return list(STYLE_PRESETS.keys())

    # ── 大纲审查 ──────────────────────────────────────────────────

    def review_outline(self, topic: str, style: dict, outline: list[dict]) -> dict:
        """从风格一致性角度审查大纲。"""
        style_summary = f"情感{style.get('emotion_intensity', 50)}/100 句长{style.get('sentence_preference', 'balanced')}" if isinstance(style, dict) else ""
        outline_text = json.dumps(outline, ensure_ascii=False, indent=2)

        prompt = OUTLINE_REVIEW_PROMPT.format(
            reviewer_role="风格分析师",
            review_perspective="风格一致性",
            topic=topic,
            style_summary=style_summary[:500],
            outline_text=outline_text,
        )
        messages = [
            {"role": "system", "content": "你是一位风格分析师。请审查大纲是否与参考文本的风格一致。"},
            {"role": "user", "content": prompt},
        ]
        response = self.llm.chat_completion(messages, temperature=0.3, max_tokens=800)
        self.last_raw_response = response
        try:
            result = parse_json(response)
            return {
                "reviewer": "style_analyst",
                "approved": result.get("approved", True),
                "criticism": result.get("criticism", ""),
                "suggestion": result.get("suggestion", ""),
            }
        except ValueError:
            return {
                "reviewer": "style_analyst", "approved": True,
                "criticism": "解析失败", "suggestion": "",
            }

    def run(self, topic: str, style: dict, target_words: int = 10000) -> list[dict]:
        """兼容 BaseAgent 抽象接口，实际不用于 StyleAnalyzer。"""
        return []
