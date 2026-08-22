"""风格硬指标统计 —— 正则 + jieba POS，零 LLM 调用。

计算实际文本的风格统计值，与目标风格 profile 对比，输出偏差报告。
"""

import re
import jieba.posseg as pseg

# ── 正则统计 ──────────────────────────────────────────────────────────

def _regex_stats(text: str) -> dict:
    """句长分布 / 对话占比 / 段落统计 / 感叹号密度。"""
    total = max(len(text), 1)

    sentences = re.split(r'[。！？\n]', text)
    sentences = [s.strip() for s in sentences if s.strip()]
    sent_lens = [len(s) for s in sentences]
    n = max(len(sentences), 1)

    quotes = re.findall(r'"[^"]{2,}"|「[^」]{2,}」|『[^』]{2,}』', text)
    dialogue_chars = sum(len(q) for q in quotes)

    paragraphs = re.split(r'\n{2,}', text)
    paragraphs = [p.strip() for p in paragraphs if p.strip()]
    np = max(len(paragraphs), 1)
    para_lens = [len(p) for p in paragraphs]

    return {
        "char_count": total,
        "short_sentence_ratio": round(sum(1 for l in sent_lens if l < 15) / n, 3),
        "medium_sentence_ratio": round(sum(1 for l in sent_lens if 15 <= l <= 30) / n, 3),
        "long_sentence_ratio": round(sum(1 for l in sent_lens if l > 30) / n, 3),
        "dialogue_ratio": round(dialogue_chars / total, 3),
        "paragraph_length_avg": round(sum(para_lens) / np, 1),
        "paragraph_length_median": round(sorted(para_lens)[np // 2], 1),
        "exclamation_ratio": round(len(re.findall(r'！', text)) / total, 5),
        "n_sentences": n,
        "n_paragraphs": np,
    }


# ── POS 统计 ──────────────────────────────────────────────────────────

def _pos_stats(text: str) -> dict:
    """形容词 / 副词密度（基于 jieba.posseg）。"""
    words = list(pseg.cut(text))
    n = max(len(words), 1)
    adj_count = sum(1 for w, flag in words if flag == 'a')
    adv_count = sum(1 for w, flag in words if flag == 'd')
    return {
        "adjective_density": round(adj_count / n, 3),
        "adverb_density": round(adv_count / n, 3),
    }


# ── 比喻 / 修辞粗略统计 ──────────────────────────────────────────────

_METAPHOR_MARKERS = re.compile(r'像|好像|仿佛|如同|似|犹如|宛若|好比|恰似|似的|一般')
_SIMILE_PATTERN = re.compile(r'(?:像|好像|仿佛|如同|似|犹如|宛若|好比|恰似)(?:.{0,8})(?:一样|似的|一般|那样)')

def _rhetoric_stats(text: str) -> dict:
    """比喻 / 拟人 / 排比 粗略统计。"""
    total = max(len(text), 1)
    metaphor_count = len(_METAPHOR_MARKERS.findall(text))
    simile_count = len(_SIMILE_PATTERN.findall(text))

    # 排比: 连续 3 句以上以相同 2-3 字开头
    sentences = re.split(r'[。！？\n]', text)
    sentences = [s.strip() for s in sentences if len(s.strip()) >= 4]
    parallelism_count = 0
    i = 0
    while i < len(sentences) - 2:
        prefix = sentences[i][:2]
        if sentences[i + 1][:2] == prefix and sentences[i + 2][:2] == prefix:
            parallelism_count += 1
            i += 3
        else:
            i += 1

    return {
        "metaphor_marker_count": metaphor_count,
        "metaphor_density": round(metaphor_count / total, 5),
        "simile_count": simile_count,
        "parallelism_groups": parallelism_count,
    }


# ── 综合统计 ──────────────────────────────────────────────────────────

def compute_all_stats(text: str) -> dict:
    """一次性计算所有风格硬指标。"""
    return {**_regex_stats(text), **_pos_stats(text), **_rhetoric_stats(text)}


# ── 偏差对比 ──────────────────────────────────────────────────────────

def compute_deviation(actual: dict, target: dict) -> dict:
    """对比实际统计值与目标风格 profile，输出偏差报告。

    Returns:
        {
            "deviations": [{"metric": "...", "actual": ..., "target": ..., "delta": ...}],
            "total_deviation": float,  # 平均标准化偏差
            "verdict": "良好" | "轻微偏离" | "明显偏离",
        }
    """
    deviations = []

    def _compare(key: str, actual_val, target_val, tolerance=0.1):
        if target_val is None or actual_val is None:
            return
        if isinstance(target_val, str) or not isinstance(target_val, (int, float)):
            return
        if target_val == 0:
            delta = abs(actual_val)
        else:
            delta = abs(actual_val - target_val) / max(abs(target_val), 0.01)
        if delta > tolerance:
            deviations.append({
                "metric": key,
                "actual": actual_val,
                "target": target_val,
                "delta": round(delta, 3),
            })

    # 句长分布
    _compare("short_sentence_ratio", actual.get("short_sentence_ratio"), target.get("short_sentence_ratio"))
    _compare("medium_sentence_ratio", actual.get("medium_sentence_ratio"), target.get("medium_sentence_ratio"))
    _compare("long_sentence_ratio", actual.get("long_sentence_ratio"), target.get("long_sentence_ratio"))
    # 对话占比
    _compare("dialogue_ratio", actual.get("dialogue_ratio"), target.get("dialogue_ratio"))
    # 段落长度
    _compare("paragraph_length_avg", actual.get("paragraph_length_avg"), target.get("paragraph_length_avg"))
    # 形容词密度
    _compare("adjective_density", actual.get("adjective_density"), target.get("adjective_density"), tolerance=0.2)
    # 副词密度 (用 adverb_policy 映射到数值)
    adv_map = {"克制": 0.02, "适度": 0.05, "丰富": 0.10}
    adv_target = adv_map.get(target.get("adverb_policy"), None)
    if adv_target:
        _compare("adverb_density", actual.get("adverb_density"), adv_target, tolerance=0.3)

    total_dev = sum(d["delta"] for d in deviations) / max(len(deviations), 1) if deviations else 0.0

    if total_dev < 0.15:
        verdict = "良好"
    elif total_dev < 0.35:
        verdict = "轻微偏离"
    else:
        verdict = "明显偏离"

    return {
        "deviations": deviations,
        "total_deviation": round(total_dev, 3),
        "verdict": verdict,
    }


# ── 完整报告 ──────────────────────────────────────────────────────────

def style_report(text: str, target_style: dict | None = None) -> dict:
    """生成完整风格统计报告。

    Args:
        text: 待分析文本
        target_style: 目标风格 profile（4 个主要控制量 + 兼容统计字段），为 None 时只统计不对比

    Returns:
        {"stats": {...}, "deviation": {...} | None}
    """
    stats = compute_all_stats(text)
    deviation = compute_deviation(stats, target_style) if target_style else None
    return {"stats": stats, "deviation": deviation}
