"""StyleSummarizer — 4 维风格参数 → 下游 Agent prompt 片段。"""


class StyleSummarizer:

    @staticmethod
    def for_writer(style: dict) -> str:
        """Writer 用的结构化风格文本。"""
        if not isinstance(style, dict):
            return ""
        parts = []

        ei = style.get("emotion_intensity", 50)
        if ei <= 30:
            parts.append("情感：极度克制，用环境和动作折射情绪，不直接命名情感")
        elif ei <= 50:
            parts.append("情感：温婉内敛，可以提及情绪但用感官细节传递，不展开抒情")
        elif ei <= 70:
            parts.append("情感：浓郁直白，允许直接叙述内心感受和抒情段落")
        else:
            parts.append("情感：激烈外放，多用感叹和夸张修辞")

        sp = style.get("sentence_preference", "balanced")
        if sp == "short":
            parts.append("句式：以短句为主，节奏明快。动作和对话用短句，描写适度收束")
        elif sp == "long":
            parts.append("句式：以长句为主，层层铺陈。描写段落用复合句缓慢推进")
        else:
            parts.append("句式：长短交替，自然呼吸。短句用于动作对话，长句用于描写和心理")

        sd = style.get("sensory_density", "medium")
        if sd == "sparse":
            parts.append("感官：留白简洁。选一个最准确的细节，让读者自己补全")
        elif sd == "rich":
            parts.append("感官：多感官交织。不只写看到的，也写听到的、闻到的、触碰到的")
        else:
            parts.append("感官：适度描写。关键场景多感官，过渡段落简洁")

        dr = style.get("dialogue_ratio", 0.3)
        parts.append(f"对话：占比约 {int(dr * 100)}%，用动作替代'说'标签")

        return "\n".join(f"- {p}" for p in parts)

    @staticmethod
    def for_planner(style: dict) -> str:
        """Planner 用的精简摘要。"""
        if not isinstance(style, dict):
            return ""
        return (
            f"情感强度 {style.get('emotion_intensity', 50)}/100，"
            f"句长偏好 {style.get('sentence_preference', 'balanced')}，"
            f"对话占比 {int(style.get('dialogue_ratio', 0.3) * 100)}%，"
            f"感官密度 {style.get('sensory_density', 'medium')}"
        )

    @staticmethod
    def for_reviewer(style: dict) -> dict:
        """Reviewer 用的风格对照维度。"""
        if not isinstance(style, dict):
            return {}
        return {
            "emotion_intensity": style.get("emotion_intensity", 50),
            "sentence_preference": style.get("sentence_preference", "balanced"),
            "dialogue_ratio": style.get("dialogue_ratio", 0.3),
            "sensory_density": style.get("sensory_density", "medium"),
        }
