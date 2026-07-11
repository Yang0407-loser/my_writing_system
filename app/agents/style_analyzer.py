import json
from .base import BaseAgent
from ..utils.prompt_templates import STYLE_ANALYSIS_PROMPT, OUTLINE_REVIEW_PROMPT
from ..utils.json_parser import parse_json


# ══════════════════════════════════════════════════════════════════
# 6 个内置预设 —— 完整 50 维
# ══════════════════════════════════════════════════════════════════

STYLE_PRESETS = {
    "中性": {
        "narrative_density": 0.7,
        "primary_emotion": "中性", "emotion_intensity": 50, "emotion_subtlety": "含蓄",
        "emotion_blend": {},
        "emotion_curve": "平稳", "emotional_peaks": "均匀分布", "catharsis_style": "渐进式",
        "narrative_empathy": "适度共情", "inner_monologue_ratio": 0.2, "show_vs_tell": "平衡",
        "emotional_registry": "文学抒情", "sensory_anchoring": True, "emotional_contrast": "渐进演变",
        "short_sentence_ratio": 0.3, "medium_sentence_ratio": 0.5, "long_sentence_ratio": 0.2,
        "sentence_length_variance": "适度波动", "sentence_pattern": "长短交替", "sentence_opening_style": "变化丰富",
        "complex_sentence_ratio": "平衡", "paragraph_rhythm": "均匀块状", "paragraph_length_avg": 200,
        "paragraph_opening_style": "混合", "dialogue_ratio": 0.3, "dialogue_mixing": "混合",
        "dialogue_tag_style": "稀疏标记", "pacing": "中等", "scene_transition": "过渡铺垫",
        "time_dilation": "实时", "tension_curve": "波浪起伏",
        "metaphor_frequency": "适度", "simile_metaphor_ratio": "平衡", "personification": "适度",
        "synesthesia": "极少", "rhetorical_devices": [],
        "rhetorical_density": 0.1, "vocabulary_register": "文学化", "vocabulary_richness": "中等",
        "chengyu_frequency": "适度", "dialect_flavor": "无", "foreign_loanwords": "偶尔",
        "adjective_density": 0.15, "adverb_policy": "适度", "modifier_position": "平衡",
        "sensory_density": "适度", "sensory_spectrum": "视觉为主", "color_use": "暖色调", "imagery_domain": "自然",
    },
    "热血": {
        "narrative_density": 0.5,
        "primary_emotion": "激昂", "emotion_intensity": 85, "emotion_subtlety": "直白",
        "emotion_blend": {"激昂": 0.7, "悲凉": 0.3},
        "emotion_curve": "渐强", "emotional_peaks": "每节2-3次", "catharsis_style": "爆发式",
        "narrative_empathy": "深度代入", "inner_monologue_ratio": 0.1, "show_vs_tell": "动作驱动",
        "emotional_registry": "文学抒情", "sensory_anchoring": True, "emotional_contrast": "高频切换",
        "short_sentence_ratio": 0.6, "medium_sentence_ratio": 0.3, "long_sentence_ratio": 0.1,
        "sentence_length_variance": "剧烈波动", "sentence_pattern": "紧凑句", "sentence_opening_style": "变化丰富",
        "complex_sentence_ratio": "简单句为主", "paragraph_rhythm": "长→短交替", "paragraph_length_avg": 120,
        "paragraph_opening_style": "动作起头", "dialogue_ratio": 0.2, "dialogue_mixing": "独立成段",
        "dialogue_tag_style": "稀疏标记", "pacing": "紧凑", "scene_transition": "直接切",
        "time_dilation": "加速", "tension_curve": "持续上升",
        "metaphor_frequency": "密集", "simile_metaphor_ratio": "暗喻为主", "personification": "适度",
        "synesthesia": "极少", "rhetorical_devices": ["排比", "反复", "反问"],
        "rhetorical_density": 0.25, "vocabulary_register": "文学化", "vocabulary_richness": "中等",
        "chengyu_frequency": "适度", "dialect_flavor": "无", "foreign_loanwords": "偶尔",
        "adjective_density": 0.12, "adverb_policy": "克制", "modifier_position": "前置为主",
        "sensory_density": "适度", "sensory_spectrum": "视觉为主", "color_use": "暖色调", "imagery_domain": "自然",
    },
    "冷峻": {
        "narrative_density": 0.85,
        "primary_emotion": "冷峻", "emotion_intensity": 35, "emotion_subtlety": "含蓄",
        "emotion_blend": {"冷峻": 0.7, "压抑": 0.3},
        "emotion_curve": "平稳", "emotional_peaks": "集中在结尾", "catharsis_style": "内敛式",
        "narrative_empathy": "冷漠旁观", "inner_monologue_ratio": 0.15, "show_vs_tell": "平衡",
        "emotional_registry": "冷峻克制", "sensory_anchoring": False, "emotional_contrast": "稳定持续",
        "short_sentence_ratio": 0.2, "medium_sentence_ratio": 0.4, "long_sentence_ratio": 0.4,
        "sentence_length_variance": "稳定", "sentence_pattern": "松散句", "sentence_opening_style": "主语开头为主",
        "complex_sentence_ratio": "复合句为主", "paragraph_rhythm": "均匀块状", "paragraph_length_avg": 300,
        "paragraph_opening_style": "场景描写", "dialogue_ratio": 0.15, "dialogue_mixing": "嵌入叙述",
        "dialogue_tag_style": "稀疏标记", "pacing": "舒缓", "scene_transition": "过渡铺垫",
        "time_dilation": "减速", "tension_curve": "缓慢释放",
        "metaphor_frequency": "极少", "simile_metaphor_ratio": "暗喻为主", "personification": "适度",
        "synesthesia": "极少", "rhetorical_devices": ["对比", "反语"],
        "rhetorical_density": 0.05, "vocabulary_register": "文学化", "vocabulary_richness": "丰富",
        "chengyu_frequency": "适度", "dialect_flavor": "无", "foreign_loanwords": "偶尔",
        "adjective_density": 0.18, "adverb_policy": "克制", "modifier_position": "前置为主",
        "sensory_density": "适度", "sensory_spectrum": "视觉为主", "color_use": "冷色调", "imagery_domain": "城市",
    },
    "治愈": {
        "narrative_density": 0.3,
        "primary_emotion": "温暖", "emotion_intensity": 50, "emotion_subtlety": "直白",
        "emotion_blend": {"温暖": 0.8, "宁静": 0.2},
        "emotion_curve": "波浪", "emotional_peaks": "均匀分布", "catharsis_style": "渐进式",
        "narrative_empathy": "深度代入", "inner_monologue_ratio": 0.35, "show_vs_tell": "心理驱动",
        "emotional_registry": "日常口语", "sensory_anchoring": True, "emotional_contrast": "渐进演变",
        "short_sentence_ratio": 0.25, "medium_sentence_ratio": 0.55, "long_sentence_ratio": 0.2,
        "sentence_length_variance": "适度波动", "sentence_pattern": "长短交替", "sentence_opening_style": "变化丰富",
        "complex_sentence_ratio": "平衡", "paragraph_rhythm": "渐进式", "paragraph_length_avg": 180,
        "paragraph_opening_style": "独白起头", "dialogue_ratio": 0.4, "dialogue_mixing": "混合",
        "dialogue_tag_style": "动作替代", "pacing": "舒缓", "scene_transition": "过渡铺垫",
        "time_dilation": "减速", "tension_curve": "波浪起伏",
        "metaphor_frequency": "适度", "simile_metaphor_ratio": "明喻为主", "personification": "密集",
        "synesthesia": "适度", "rhetorical_devices": ["排比", "拟人"],
        "rhetorical_density": 0.12, "vocabulary_register": "口语化", "vocabulary_richness": "基础",
        "chengyu_frequency": "极少", "dialect_flavor": "无", "foreign_loanwords": "偶尔",
        "adjective_density": 0.12, "adverb_policy": "丰富", "modifier_position": "平衡",
        "sensory_density": "丰富", "sensory_spectrum": "多感官平衡", "color_use": "暖色调", "imagery_domain": "自然",
    },
    "压抑": {
        "narrative_density": 0.6,
        "primary_emotion": "压抑", "emotion_intensity": 70, "emotion_subtlety": "隐晦",
        "emotion_blend": {"压抑": 0.5, "恐惧": 0.3, "悲凉": 0.2},
        "emotion_curve": "下沉", "emotional_peaks": "集中在结尾", "catharsis_style": "内敛式",
        "narrative_empathy": "适度共情", "inner_monologue_ratio": 0.5, "show_vs_tell": "心理驱动",
        "emotional_registry": "冷峻克制", "sensory_anchoring": True, "emotional_contrast": "稳定持续",
        "short_sentence_ratio": 0.2, "medium_sentence_ratio": 0.4, "long_sentence_ratio": 0.4,
        "sentence_length_variance": "稳定", "sentence_pattern": "松散句", "sentence_opening_style": "主语开头为主",
        "complex_sentence_ratio": "复合句为主", "paragraph_rhythm": "均匀块状", "paragraph_length_avg": 250,
        "paragraph_opening_style": "独白起头", "dialogue_ratio": 0.1, "dialogue_mixing": "嵌入叙述",
        "dialogue_tag_style": "零标记", "pacing": "舒缓", "scene_transition": "过渡铺垫",
        "time_dilation": "减速", "tension_curve": "缓慢释放",
        "metaphor_frequency": "适度", "simile_metaphor_ratio": "暗喻为主", "personification": "密集",
        "synesthesia": "适度", "rhetorical_devices": ["反复", "对比"],
        "rhetorical_density": 0.08, "vocabulary_register": "文学化", "vocabulary_richness": "丰富",
        "chengyu_frequency": "适度", "dialect_flavor": "无", "foreign_loanwords": "偶尔",
        "adjective_density": 0.2, "adverb_policy": "克制", "modifier_position": "前置为主",
        "sensory_density": "丰富", "sensory_spectrum": "触觉突出", "color_use": "低饱和", "imagery_domain": "身体",
    },
    "紧迫": {
        "narrative_density": 0.75,
        "primary_emotion": "恐惧", "emotion_intensity": 90, "emotion_subtlety": "直白",
        "emotion_blend": {"恐惧": 0.6, "激昂": 0.4},
        "emotion_curve": "突转", "emotional_peaks": "集中在结尾", "catharsis_style": "爆发式",
        "narrative_empathy": "深度代入", "inner_monologue_ratio": 0.05, "show_vs_tell": "动作驱动",
        "emotional_registry": "日常口语", "sensory_anchoring": True, "emotional_contrast": "高频切换",
        "short_sentence_ratio": 0.7, "medium_sentence_ratio": 0.25, "long_sentence_ratio": 0.05,
        "sentence_length_variance": "剧烈波动", "sentence_pattern": "短句群", "sentence_opening_style": "变化丰富",
        "complex_sentence_ratio": "简单句为主", "paragraph_rhythm": "短→长交替", "paragraph_length_avg": 80,
        "paragraph_opening_style": "动作起头", "dialogue_ratio": 0.1, "dialogue_mixing": "独立成段",
        "dialogue_tag_style": "零标记", "pacing": "急促", "scene_transition": "直接切",
        "time_dilation": "加速", "tension_curve": "持续上升",
        "metaphor_frequency": "适度", "simile_metaphor_ratio": "明喻为主", "personification": "极少",
        "synesthesia": "极少", "rhetorical_devices": ["反复", "反问"],
        "rhetorical_density": 0.15, "vocabulary_register": "口语化", "vocabulary_richness": "基础",
        "chengyu_frequency": "极少", "dialect_flavor": "无", "foreign_loanwords": "偶尔",
        "adjective_density": 0.05, "adverb_policy": "克制", "modifier_position": "前置为主",
        "sensory_density": "极少", "sensory_spectrum": "视觉为主", "color_use": "高饱和", "imagery_domain": "城市",
    },
    "荒诞": {
        "narrative_density": 0.4,
        "primary_emotion": "荒诞", "emotion_intensity": 60, "emotion_subtlety": "隐晦",
        "emotion_blend": {"荒诞": 0.5, "好奇": 0.3, "冷峻": 0.2},
        "emotion_curve": "波浪", "emotional_peaks": "均匀分布", "catharsis_style": "内敛式",
        "narrative_empathy": "冷漠旁观", "inner_monologue_ratio": 0.25, "show_vs_tell": "平衡",
        "emotional_registry": "冷峻克制", "sensory_anchoring": False, "emotional_contrast": "渐进演变",
        "short_sentence_ratio": 0.3, "medium_sentence_ratio": 0.4, "long_sentence_ratio": 0.3,
        "sentence_length_variance": "适度波动", "sentence_pattern": "松散句", "sentence_opening_style": "变化丰富",
        "complex_sentence_ratio": "平衡", "paragraph_rhythm": "跳跃式", "paragraph_length_avg": 200,
        "paragraph_opening_style": "场景描写", "dialogue_ratio": 0.25, "dialogue_mixing": "混合",
        "dialogue_tag_style": "稀疏标记", "pacing": "变速", "scene_transition": "蒙太奇",
        "time_dilation": "非线性", "tension_curve": "波浪起伏",
        "metaphor_frequency": "密集", "simile_metaphor_ratio": "暗喻为主", "personification": "密集",
        "synesthesia": "适度", "rhetorical_devices": ["反语", "夸张", "双关"],
        "rhetorical_density": 0.2, "vocabulary_register": "文学化", "vocabulary_richness": "丰富",
        "chengyu_frequency": "适度", "dialect_flavor": "无", "foreign_loanwords": "频繁",
        "adjective_density": 0.2, "adverb_policy": "适度", "modifier_position": "平衡",
        "sensory_density": "适度", "sensory_spectrum": "多感官平衡", "color_use": "金属色", "imagery_domain": "机械",
    },
}


class StyleAnalyzer(BaseAgent):
    """风格分析器：分析参考文本 + 预设管理 + 简报生成。"""

    # ── 分析 ──────────────────────────────────────────────────────

    def analyze(self, reference_text: str) -> dict:
        """从参考文本中提取完整 50 维风格特征。"""
        prompt = STYLE_ANALYSIS_PROMPT.format(reference_text=reference_text[:6000])
        messages = [
            {"role": "system", "content": "你是一位资深的文学风格分析专家。请严格以 JSON 格式输出完整的 50 个字段。"},
            {"role": "user", "content": prompt},
        ]
        response = self.llm.chat_completion(messages, temperature=0.3, max_tokens=3000)
        self.last_raw_response = response
        try:
            result = parse_json(response)
            if isinstance(result, dict):
                return self._fill_defaults(result)
            return {}
        except ValueError:
            raise ValueError(f"风格分析 JSON 解析失败。原始响应:\n{response[:800]}")

    def _fill_defaults(self, data: dict) -> dict:
        """用默认值补齐 LLM 未返回的字段。"""
        defaults = StyleAnalyzer.get_preset("中性")
        for key, value in defaults.items():
            if key not in data or data[key] is None:
                data[key] = value
        return data

    # ── 预设 ──────────────────────────────────────────────────────

    @staticmethod
    def get_preset(name: str) -> dict:
        """返回预设的完整 50 维 dict。"""
        return dict(STYLE_PRESETS.get(name, STYLE_PRESETS["中性"]))

    @staticmethod
    def list_presets() -> list[str]:
        return list(STYLE_PRESETS.keys())

    # ── 简报生成 ──────────────────────────────────────────────────

    def build_brief(self, profile: dict) -> str:
        """将 50 维参数转为自然语言风格简报。"""
        prompt = f"""你是一位文学编辑。请将以下 50 维风格参数转为一段流畅自然的风格简报（200-500字），像一个编辑在向作家描述这篇文章应该怎么写。

风格参数：
{json.dumps(profile, ensure_ascii=False, indent=2)}

要求：
1. 用自然流畅的中文写，不要罗列参数
2. 覆盖情感基调、句式节奏、修辞用词三个方面
3. 给出具体可操作的写作建议
4. 语气像编辑在和作家对话，不说"你应该"而说"多用/减少/偏好"等

直接输出风格简报文本，不要加标题。"""

        messages = [
            {"role": "system", "content": "你是一位资深文学编辑。请直接输出风格简报。"},
            {"role": "user", "content": prompt},
        ]
        resp = self.llm.chat_completion(messages, temperature=0.5, max_tokens=800)
        return resp.strip()

    # ── 大纲审查（保留旧功能）─────────────────────────────────────

    def review_outline(self, topic: str, style: dict, outline: list[dict]) -> dict:
        """从风格一致性角度审查大纲。"""
        style_brief = style.get("style_brief", "") if isinstance(style, dict) else ""
        style_summary = style_brief if style_brief else (
            f"情感强度{style.get('emotion_intensity', 50)}/100"
        )
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
            return {"reviewer": "style_analyst", "approved": True, "criticism": "", "suggestion": ""}

    # ── 兼容旧接口 ────────────────────────────────────────────────

    def run(self, reference_text: str) -> dict:
        return self.analyze(reference_text)
