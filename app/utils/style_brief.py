"""StyleSummarizer — 将 50 维风格参数转化为不同 Agent 的结构化 prompt 片段。"""


class StyleSummarizer:
    """为不同下游 Agent 生成风格注入文本。

    策略：关键维度（句长、段落、对话、情感、修饰）精确参数化；
    其余维度融入自然语言描述。
    """

    # ── 关键维度提取 ──

    @staticmethod
    def _get(v: dict, key, default=None):
        if not isinstance(v, dict):
            return default
        val = v.get(key)
        return val if val is not None and val != "" else default

    @classmethod
    def for_writer(cls, style: dict) -> str:
        """生成 Writer 用的风格约束块（~300字）。"""
        parts = []

        # 句法
        short_r = cls._get(style, "short_sentence_ratio")
        long_r = cls._get(style, "long_sentence_ratio")
        sent_pat = cls._get(style, "sentence_pattern", "长短交替")
        if short_r is not None and long_r is not None:
            parts.append(
                f"句长分布：短句约占 {int(short_r * 100)}%，长句约占 {int(long_r * 100)}%，句式偏好「{sent_pat}」。"
            )

        # 段落节奏
        par_rhythm = cls._get(style, "paragraph_rhythm", "渐进式")
        par_len = cls._get(style, "paragraph_length_avg")
        if par_len:
            parts.append(f"段落节奏：平均 {par_len} 字/段，节奏「{par_rhythm}」。")
        else:
            parts.append(f"段落节奏偏好「{par_rhythm}」。")

        # 对话
        dia_ratio = cls._get(style, "dialogue_ratio")
        dia_tag = cls._get(style, "dialogue_tag_style", "动作替代")
        if dia_ratio is not None:
            parts.append(
                f"对话占比约 {int(dia_ratio * 100)}%，标记风格「{dia_tag}」。"
            )

        # 情感语域
        emo_reg = cls._get(style, "emotional_registry", "文学抒情")
        show_tell = cls._get(style, "show_vs_tell", "平衡")
        parts.append(f"情感语域「{emo_reg}」，叙事方式偏「{show_tell}」。")

        # 感官
        sen_density = cls._get(style, "sensory_density", "适度")
        sen_spec = cls._get(style, "sensory_spectrum", "多感官平衡")
        color = cls._get(style, "color_use", "")
        img = cls._get(style, "imagery_domain", "")
        sensory = f"感官密度「{sen_density}」，侧重「{sen_spec}」。"
        if color:
            sensory += f" 色调「{color}」。"
        if img:
            sensory += f" 意象倾向「{img}」。"
        parts.append(sensory)

        # 修饰
        adj_d = cls._get(style, "adjective_density")
        adv_p = cls._get(style, "adverb_policy", "适度")
        meta_f = cls._get(style, "metaphor_frequency", "适度")
        if adj_d is not None:
            parts.append(
                f"形容词密度 {adj_d:.2f}，副词「{adv_p}」，比喻「{meta_f}」。"
            )

        # 节奏
        pacing = cls._get(style, "pacing", "中等")
        tension = cls._get(style, "tension_curve", "波浪起伏")
        scene_t = cls._get(style, "scene_transition", "过渡铺垫")
        parts.append(f"整体节奏「{pacing}」，张力曲线「{tension}」，场景过渡「{scene_t}」。")

        return "\n".join(f"- {p}" for p in parts)

    @classmethod
    def for_planner(cls, style: dict) -> str:
        """生成 Planner 用的风格摘要（~200字）。"""
        emo = cls._get(style, "primary_emotion", "中性")
        intensity = cls._get(style, "emotion_intensity", 50)
        curve = cls._get(style, "emotion_curve", "渐强")
        peaks = cls._get(style, "emotional_peaks", "均匀分布")
        pacing = cls._get(style, "pacing", "中等")
        tension = cls._get(style, "tension_curve", "波浪起伏")

        return (
            f"情感基调「{emo}」，强度 {intensity}/100，"
            f"曲线呈「{curve}」，高潮分布「{peaks}」。"
            f"整体节奏「{pacing}」，张力曲线「{tension}」。"
        )

    @classmethod
    def for_reviewer(cls, style: dict) -> dict:
        """生成 Reviewer 用的维度评分参考。"""
        return {
            "dialogue_ratio": cls._get(style, "dialogue_ratio"),
            "adjective_density": cls._get(style, "adjective_density"),
            "emotion_intensity": cls._get(style, "emotion_intensity", 50),
            "emotion_subtlety": cls._get(style, "emotion_subtlety", "含蓄"),
            "sentence_pattern": cls._get(style, "sentence_pattern", "长短交替"),
            "paragraph_rhythm": cls._get(style, "paragraph_rhythm", "渐进式"),
            "pacing": cls._get(style, "pacing", "中等"),
            "tension_curve": cls._get(style, "tension_curve", "波浪起伏"),
        }
