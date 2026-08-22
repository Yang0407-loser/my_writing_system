"""Compile four user style controls into a sparse narrative realization policy.

The policy intentionally avoids paragraph plans, action sequences, numeric prose
targets, and the internal six-dimension diagnostic taxonomy.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


REALIZATION_POLICY_VERSION = "realization-policy-v1.1"


@dataclass(frozen=True)
class RealizationPolicy:
    version: str
    normalized_profile: dict[str, Any]
    narrative_stance: str
    organizing_principle: str
    freedom_permission: str
    prohibitions: tuple[str, ...]


def _number(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def normalize_style_profile(style: dict | None) -> dict[str, Any]:
    source = style if isinstance(style, dict) else {}
    sentence = source.get("sentence_preference", "balanced")
    sensory = source.get("sensory_density", "medium")
    return {
        "emotion_intensity": int(max(0, min(100, _number(source.get("emotion_intensity"), 50)))),
        "dialogue_ratio": round(max(0.0, min(1.0, _number(source.get("dialogue_ratio"), 0.3))), 3),
        "sentence_preference": sentence if sentence in {"short", "balanced", "long"} else "balanced",
        "sensory_density": sensory if sensory in {"sparse", "medium", "rich"} else "medium",
    }


def _stance(profile: dict[str, Any]) -> str:
    emotion = profile["emotion_intensity"]
    sentence = profile["sentence_preference"]
    sensory = profile["sensory_density"]

    emotion_text = (
        "情绪措辞保持克制，减少直接命名"
        if emotion <= 40
        else "情绪措辞保持可感知但不过度渲染"
        if emotion <= 70
        else "情绪措辞可以鲜明外露"
    )
    sentence_text = {
        "short": "句子整体利落，必要处可以用较长句缓冲",
        "balanced": "句子长短随注意力和动作自然变化",
        "long": "句子可以舒展铺陈，关键转折仍可突然收短",
    }[sentence]
    sensory_text = {
        "sparse": "感官细节宁少勿匀，优先保留一个主导感官",
        "medium": "感官细节适量穿插，不平均铺满段落",
        "rich": "可以调动多种感官，但避免每段使用相同组合",
    }[sensory]
    return f"{emotion_text}；{sentence_text}；{sensory_text}。"


def compile_realization_policy(
    style: dict | None,
    *,
    beat: dict | None = None,
) -> RealizationPolicy:
    profile = normalize_style_profile(style)
    beat = beat if isinstance(beat, dict) else {}
    focus = str(beat.get("character_focus", "")).strip()
    intensity = int(max(0, min(10, _number(beat.get("intensity"), 5))))

    if focus:
        organizing = f"表达重心围绕“{focus[:80]}”，保持当前视角的用词和感官范围。"
    else:
        organizing = "保持当前叙述视角的用词、观察范围和情绪距离。"

    if intensity >= 8:
        permission = "高强度处可以突然收短句子，缓冲处仍允许恢复较长句群。"
    elif intensity <= 3:
        permission = "低强度处允许句子舒展，但不必为每段补充感官描写。"
    else:
        permission = "句群节奏可以随对白、动作和观察自然变化，不维持机械匀速。"

    return RealizationPolicy(
        version=REALIZATION_POLICY_VERSION,
        normalized_profile=profile,
        narrative_stance=_stance(profile),
        organizing_principle=organizing,
        freedom_permission=permission,
        prohibitions=(
            "不要把句长、对白比例或感官密度机械平均到每个段落。",
            "不要为了显得符合风格而连续复用同一种句法、比喻结构或感官组合。",
            "不要在正文中输出风格参数、写作规则或控制标签。",
        ),
    )


def render_realization_policy(policy: RealizationPolicy) -> str:
    prohibitions = " ".join(policy.prohibitions)
    return (
        f"## 叙述姿态（{policy.version}）\n"
        f"{policy.narrative_stance}\n"
        f"{policy.organizing_principle}\n"
        f"{policy.freedom_permission}\n"
        f"{prohibitions}\n"
        "开场位置、段落长短、对白数量、停留细节和结束反应由你自行决定；只输出正文。"
    )
