"""Deterministic prose-style observability metrics with no LLM dependency."""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter, defaultdict
from statistics import mean, median
from typing import Any, Iterable


SENSORY_TERMS = (
    "光", "暗", "亮", "颜色", "红", "黄", "白", "黑", "绿", "蓝",
    "声音", "响", "嗡", "沙沙", "咔哒", "安静", "气味", "香", "酸",
    "甜", "咸", "苦", "冷", "凉", "暖", "热", "温", "硬", "软",
    "湿", "干", "触", "指尖", "掌心",
)
PSYCHOLOGICAL_EXPOSITION_TERMS = (
    "意识到", "明白", "知道", "觉得", "认为", "想起", "想道", "心想",
    "心里", "内心", "感到", "感觉", "不由得", "忍不住",
)
TIME_START_PATTERN = re.compile(
    r"^(?:凌晨|清晨|早晨|上午|中午|下午|傍晚|晚上|深夜|今天|当天|次日|翌日|"
    r"第二天|昨天|明天|周[一二三四五六日天]|星期[一二三四五六日天]|"
    r"第[一二三四五六七八九十百\d]+(?:天|周|个月|年)|"
    r"[零〇一二三四五六七八九十百两\d]+点(?:半|[零一二三四五六七八九十\d]+分)?)"
)
ORDINAL_START_PATTERN = re.compile(r"^第[零〇一二三四五六七八九十百千万两\d]+(?:个|次|天|周|节|章|轮|遍)")
NUMERIC_START_PATTERN = re.compile(r"^[零〇一二三四五六七八九十百千万两\d]+(?:点|分|秒|天|周|次|个|年|月|日)")
QUOTE_PATTERN = re.compile(r"[“「『\"]([^”」』\"]{1,})[”」』\"]")
SENTENCE_PATTERN = re.compile(r"[^。！？!?\n]+(?:[。！？!?]+[”」』\"]*)?")


def _visible_length(text: str) -> int:
    return len(re.sub(r"\s+", "", text))


def _normalise(text: str) -> str:
    return re.sub(r"[\s，。！？!?；;：:、“”「」『』\"‘’（）()—…·,.]+", "", text).lower()


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def split_sentences(text: str) -> list[str]:
    return [match.group(0).strip() for match in SENTENCE_PATTERN.finditer(text) if match.group(0).strip()]


def split_paragraphs(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]


def percentile(values: Iterable[int], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(ordered[lower])
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def distribution(values: list[int]) -> dict[str, float | int]:
    if not values:
        return {"count": 0, "min": 0, "p25": 0, "median": 0, "p75": 0, "p90": 0, "max": 0, "mean": 0}
    return {
        "count": len(values),
        "min": min(values),
        "p25": round(percentile(values, 0.25), 2),
        "median": round(median(values), 2),
        "p75": round(percentile(values, 0.75), 2),
        "p90": round(percentile(values, 0.90), 2),
        "max": max(values),
        "mean": round(mean(values), 2),
    }


def sentence_start_type(sentence: str, character_names: Iterable[str]) -> str:
    value = sentence.lstrip("“「『\"‘'（(")
    if any(value.startswith(name) for name in character_names):
        return "character_name"
    if TIME_START_PATTERN.match(value):
        return "time_anchor"
    if ORDINAL_START_PATTERN.match(value):
        return "ordinal"
    if NUMERIC_START_PATTERN.match(value):
        return "numeric"
    return "other"


def sentence_signature(sentence: str, character_names: Iterable[str]) -> str:
    """A structural proxy, not a grammatical or semantic parse."""
    length = _visible_length(sentence)
    bucket = "short" if length <= 12 else "medium" if length <= 30 else "long"
    punctuation = "".join(re.findall(r"[，,；;：:—…！？!?]", sentence)) or "none"
    clause_count = len(re.findall(r"[，,；;：:—]", sentence)) + 1
    dialogue = bool(re.search(r"[“「『\"]", sentence))
    return f"{sentence_start_type(sentence, character_names)}|{bucket}|c{min(clause_count, 4)}|d{int(dialogue)}|{punctuation[:6]}"


def _runs(values: list[str], predicate, minimum: int = 3) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    index = 0
    while index < len(values):
        if not predicate(values[index]):
            index += 1
            continue
        end = index + 1
        while end < len(values) and predicate(values[end]):
            end += 1
        if end - index >= minimum:
            result.append({"start_sentence": index + 1, "length": end - index})
        index = end
    return result


def analyse_text(text: str, character_names: Iterable[str]) -> dict[str, Any]:
    names = tuple(character_names)
    sentences = split_sentences(text)
    paragraphs = split_paragraphs(text)
    sentence_lengths = [_visible_length(item) for item in sentences]
    paragraph_lengths = [_visible_length(item) for item in paragraphs]
    visible = max(_visible_length(text), 1)
    dialogue_chars = sum(_visible_length(match) for match in QUOTE_PATTERN.findall(text))
    starts = Counter(sentence_start_type(item, names) for item in sentences)
    sensory_hits = sum(text.count(term) for term in SENSORY_TERMS)
    psychological_hits = {term: text.count(term) for term in PSYCHOLOGICAL_EXPOSITION_TERMS if text.count(term)}
    signatures = [sentence_signature(item, names) for item in sentences]
    short_markers = ["short" if length <= 12 else "other" for length in sentence_lengths]
    short_runs = _runs(short_markers, lambda value: value == "short")
    isomorphic_runs: list[dict[str, Any]] = []
    index = 0
    while index < len(signatures):
        end = index + 1
        while end < len(signatures) and signatures[end] == signatures[index]:
            end += 1
        if end - index >= 3:
            isomorphic_runs.append({"start_sentence": index + 1, "length": end - index, "signature": signatures[index]})
        index = end

    exact_sentences = Counter(_normalise(item) for item in sentences if _visible_length(item) >= 8)
    exact_paragraphs = Counter(_normalise(item) for item in paragraphs if _visible_length(item) >= 20)
    return {
        "characters": _visible_length(text),
        "dialogue_ratio": round(dialogue_chars / visible, 4),
        "sentence_length": distribution(sentence_lengths),
        "sentence_length_buckets": {
            "short_le_12": sum(length <= 12 for length in sentence_lengths),
            "medium_13_30": sum(13 <= length <= 30 for length in sentence_lengths),
            "long_gt_30": sum(length > 30 for length in sentence_lengths),
        },
        "paragraph_length": distribution(paragraph_lengths),
        "sentence_starts": dict(sorted(starts.items())),
        "mechanical_start_ratio": round(sum(starts[k] for k in ("time_anchor", "ordinal", "numeric")) / max(len(sentences), 1), 4),
        "sensory_term_hits": sensory_hits,
        "sensory_terms_per_1k": round(sensory_hits * 1000 / visible, 2),
        "psychological_exposition_hits": sum(psychological_hits.values()),
        "psychological_exposition_per_1k": round(sum(psychological_hits.values()) * 1000 / visible, 2),
        "psychological_terms": psychological_hits,
        "consecutive_short_sentence_runs": short_runs,
        "consecutive_isomorphic_sentence_runs": isomorphic_runs,
        "exact_repeated_sentence_instances": sum(count - 1 for count in exact_sentences.values() if count > 1),
        "exact_repeated_paragraph_instances": sum(count - 1 for count in exact_paragraphs.values() if count > 1),
        "sentence_signatures": signatures,
    }


def _duplicate_groups(items: list[dict[str, Any]], kind: str, minimum_length: int) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        for ordinal, text in enumerate(item[f"{kind}s"], 1):
            normalised = _normalise(text)
            if len(normalised) >= minimum_length:
                grouped[normalised].append({
                    "source_id": item["source_id"],
                    "ordinal": ordinal,
                    "text_hash": _sha(normalised),
                    "evidence": text[:80],
                })
    result = []
    for occurrences in grouped.values():
        if len(occurrences) > 1:
            result.append({"occurrence_count": len(occurrences), "occurrences": occurrences})
    return sorted(result, key=lambda group: (-group["occurrence_count"], group["occurrences"][0]["text_hash"]))


def _outlier_bounds(values: list[float]) -> tuple[float, float]:
    if not values:
        return (0.0, 0.0)
    q1, q3 = percentile(values, 0.25), percentile(values, 0.75)
    spread = q3 - q1
    return (q1 - 1.5 * spread, q3 + 1.5 * spread)


def build_observability_report(sections: dict[int, dict[str, Any]], character_names: Iterable[str]) -> dict[str, Any]:
    names = tuple(character_names)
    units: list[dict[str, Any]] = []
    for section_number in sorted(sections):
        section = sections[section_number]
        for subsection in section["subsections"]:
            text = subsection["text"]
            units.append({
                "section": section_number,
                "section_title": section["title"],
                "subsection": subsection["subsection"],
                "subsection_title": subsection["title"],
                "source_id": subsection["source_id"],
                "text_hash": subsection["text_hash"],
                "sentences": split_sentences(text),
                "paragraphs": split_paragraphs(text),
                "metrics": analyse_text(text, names),
            })

    chapter_results = []
    for section_number in sorted(sections):
        section = sections[section_number]
        combined = "\n\n".join(item["text"] for item in section["subsections"])
        chapter_results.append({
            "section": section_number,
            "title": section["title"],
            "subsection_count": len(section["subsections"]),
            "metrics": analyse_text(combined, names),
        })

    all_text = "\n\n".join(
        subsection["text"]
        for number in sorted(sections)
        for subsection in sections[number]["subsections"]
    )
    corpus_metrics = analyse_text(all_text, names)
    tracked = ("dialogue_ratio", "mechanical_start_ratio", "sensory_terms_per_1k", "psychological_exposition_per_1k")
    bounds = {metric: _outlier_bounds([float(chapter["metrics"][metric]) for chapter in chapter_results]) for metric in tracked}
    for chapter in chapter_results:
        anomalies = []
        for metric in tracked:
            value = float(chapter["metrics"][metric])
            low, high = bounds[metric]
            if value < low:
                anomalies.append({"metric": metric, "direction": "low", "value": value, "corpus_iqr_bound": round(low, 4)})
            elif value > high:
                anomalies.append({"metric": metric, "direction": "high", "value": value, "corpus_iqr_bound": round(high, 4)})
        chapter["anomalies"] = anomalies
        chapter["structural_signals"] = {
            "consecutive_short_sentence_runs": len(chapter["metrics"]["consecutive_short_sentence_runs"]),
            "consecutive_isomorphic_sentence_runs": len(chapter["metrics"]["consecutive_isomorphic_sentence_runs"]),
            "exact_repeated_sentence_instances": chapter["metrics"]["exact_repeated_sentence_instances"],
            "exact_repeated_paragraph_instances": chapter["metrics"]["exact_repeated_paragraph_instances"],
        }

    sentence_duplicates = _duplicate_groups(units, "sentence", 8)
    paragraph_duplicates = _duplicate_groups(units, "paragraph", 20)
    for unit in units:
        unit.pop("sentences")
        unit.pop("paragraphs")
        unit["metrics"].pop("sentence_signatures")
    for chapter in chapter_results:
        chapter["metrics"].pop("sentence_signatures")
    corpus_metrics.pop("sentence_signatures")

    return {
        "corpus": corpus_metrics,
        "chapters": chapter_results,
        "subsections": units,
        "duplicates": {
            "exact_sentence_groups": sentence_duplicates,
            "exact_paragraph_groups": paragraph_duplicates,
        },
        "anomaly_method": {
            "method": "Tukey IQR fences across 18 chapter values",
            "bounds": {key: [round(value[0], 4), round(value[1], 4)] for key, value in bounds.items()},
            "interpretation": "An anomaly is a distribution outlier, not an automatic quality defect.",
        },
        "style_control_mapping": {
            "dialogue_ratio": {"mapping": "direct", "metrics": ["dialogue_ratio"]},
            "sentence_preference": {"mapping": "direct_proxy", "metrics": ["sentence_length", "sentence_length_buckets", "consecutive_short_sentence_runs"]},
            "sensory_density": {"mapping": "lexical_proxy", "metrics": ["sensory_terms_per_1k"], "limitation": "Fixed lexicon does not measure image quality or multi-sensory integration."},
            "emotion_intensity": {"mapping": "human_or_llm_required", "observable_only": ["psychological_exposition_per_1k"], "limitation": "Psychological exposition frequency cannot determine emotional intensity or layering."},
        },
        "known_style_issues": [
            {"id": "mechanical_counting", "status": "baseline_issue", "deterministic_observability": ["mechanical_start_ratio", "sentence_starts"], "judgment": "human_required"},
            {"id": "repetitive_sentence_patterns", "status": "baseline_issue", "deterministic_observability": ["exact_sentence_groups", "exact_paragraph_groups", "consecutive_isomorphic_sentence_runs"], "judgment": "human_required_for_quality_impact"},
            {"id": "insufficient_emotional_layering", "status": "baseline_issue", "deterministic_observability": [], "judgment": "human_or_llm_required", "automation_status": "not_automated"},
        ],
    }
