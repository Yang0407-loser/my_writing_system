"""Deterministic review signals for the five targeted expression habits."""

from __future__ import annotations

from collections import Counter
import re


METRICS_VERSION = "anti-ai-expression-metrics-v0"

_SIMILE = re.compile(r"(?:(?<![图肖画影不])像(?!素|片|机)|仿佛|宛如|如同|好似)")
_ABSTRACT = re.compile(r"(?:某种|说不清|好像|忽然觉得|突然觉得|不知为什么|仿佛)")
_EXPLANATION = re.compile(
    r"(?:她|他)(?:忽然|突然)?(?:意识到|明白|懂得|觉得)|"
    r"(?:这让|这使)[^。！？]{0,18}(?:明白|意识到|觉得)|"
    r"(?:原来|也许这就是|这就是)"
)
_RHYTHMIC_TEMPLATE = re.compile(
    r"一下[，、]?又一下|一遍[，、]?又一遍|展开又收拢|"
    r"推出去[，、]?收回来|由远及近[，、]?又由近及远|"
    r"一盏一盏|一点一点"
)
_UPLIFT = re.compile(
    r"(?:有些(?:东西|事情)|也许这就是|这就是|原来|终于|"
    r"忽然觉得|把时间.{0,12}(?:揉|放|留)|找到.{0,10}(?:位置|答案|生活))"
)

_MOTIFS = {
    "light": re.compile(r"(?:暖黄|灯光|光线|一线光|路灯|屏幕的光)"),
    "sound": re.compile(r"(?:低鸣|沙沙|闷响|心跳|声音.{0,8}(?:填满|响起))"),
    "temperature": re.compile(r"(?:温热|温的|暖意|凉意|冷空气|冰凉|烫)"),
    "flour": re.compile(r"(?:面粉.{0,12}(?:飞|浮|扬|雪)|白雾|细小的雪)"),
}


def _tail(text: str, characters: int = 180) -> str:
    return text[-characters:] if len(text) > characters else text


def _excerpts(text: str, pattern: re.Pattern[str], limit: int = 5) -> list[str]:
    results = []
    for match in pattern.finditer(text):
        start = max(0, match.start() - 18)
        end = min(len(text), match.end() + 30)
        results.append(re.sub(r"\s+", " ", text[start:end]))
        if len(results) >= limit:
            break
    return results


def evaluate_expression_signals(text: str) -> dict:
    text = str(text or "")
    chars = max(len(re.sub(r"\s+", "", text)), 1)
    simile_count = len(_SIMILE.findall(text))
    motif_counts = Counter({name: len(pattern.findall(text)) for name, pattern in _MOTIFS.items()})
    repeated_motifs = {name: count for name, count in motif_counts.items() if count > 1}
    tail = _tail(text)
    closure_count = len(_UPLIFT.findall(tail))
    result = {
        "version": METRICS_VERSION,
        "characters": chars,
        "simile_count": simile_count,
        "similes_per_500": round(simile_count * 500 / chars, 4),
        "simile_budget_exceeded": simile_count > max(1, chars // 500),
        "abstract_placeholder_count": len(_ABSTRACT.findall(text)),
        "emotion_explanation_count": len(_EXPLANATION.findall(text)),
        "rhythmic_template_count": len(_RHYTHMIC_TEMPLATE.findall(text)),
        "repeated_motif_categories": repeated_motifs,
        "repeated_motif_excess": sum(count - 1 for count in repeated_motifs.values()),
        "uplift_closure_count": closure_count,
        "evidence": {
            "simile": _excerpts(text, _SIMILE),
            "abstract_placeholder": _excerpts(text, _ABSTRACT),
            "emotion_explanation": _excerpts(text, _EXPLANATION),
            "rhythmic_template": _excerpts(text, _RHYTHMIC_TEMPLATE),
            "uplift_closure": _excerpts(tail, _UPLIFT),
        },
    }
    result["targeted_signal_total"] = (
        simile_count
        + result["abstract_placeholder_count"]
        + result["emotion_explanation_count"]
        + result["rhythmic_template_count"]
        + result["repeated_motif_excess"]
        + closure_count
    )
    result["automatic_rewrite_recommended"] = False
    result["human_blind_review_required"] = True
    return result
