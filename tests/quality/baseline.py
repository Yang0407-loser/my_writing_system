"""Build a deterministic quality baseline without calling an LLM or Redis."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RAG = ROOT / "tests" / "rag_annotation_07d1391e.json"
DEFAULT_CHARACTER = ROOT / "tests" / "quality" / "character_consistency_annotations.json"
DEFAULT_STYLE = ROOT / "tests" / "quality" / "style_baseline.json"
DEFAULT_RUNTIME = ROOT / "tests" / "quality" / "runtime_baseline.json"

SENSORY_TERMS = (
    "光", "暗", "亮", "颜色", "红", "黄", "白", "黑", "看", "盯", "望",
    "声", "响", "嗡", "滴", "沙沙", "咔嚓", "安静", "味", "香", "酸",
    "甜", "咸", "苦", "冷", "凉", "暖", "热", "温", "硬", "软", "湿",
    "干", "触", "指尖", "掌心",
)


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def compute_rag_metrics(annotation: dict[str, Any]) -> dict[str, Any]:
    entries = annotation.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ValueError("RAG annotation must contain non-empty entries")

    required = {
        "query_intent", "gold_sections", "gold_chunks",
        "must_recall_facts", "requires_causal_retrieval",
    }
    relevant = 0
    retrieved = 0
    recall_hits = 0
    recall_gold = 0
    causal_queries = 0
    late_relevant = 0
    late_retrieved = 0

    for entry in entries:
        missing = sorted(required - set(entry))
        if missing:
            raise ValueError(f"query {entry.get('query_index')} missing fields: {missing}")
        if not entry["must_recall_facts"]:
            raise ValueError(f"query {entry.get('query_index')} has no must_recall_facts")

        items = entry.get("items", [])[: int(annotation.get("k", 5))]
        retrieved += len(items)
        relevant_items = [item for item in items if item.get("human_relevant") == "相关"]
        relevant += len(relevant_items)

        retrieved_relevant_sections = {
            int(item["section"])
            for item in relevant_items
            if str(item.get("section", "")).isdigit()
        }
        gold_sections = {int(section) for section in entry["gold_sections"]}
        recall_hits += len(retrieved_relevant_sections & gold_sections)
        recall_gold += len(gold_sections)
        causal_queries += int(bool(entry["requires_causal_retrieval"]))

        if int(entry.get("section", 0)) >= 13:
            late_relevant += len(relevant_items)
            late_retrieved += len(items)

    return {
        "schema_version": annotation.get("schema_version", 1),
        "queries": len(entries),
        "k": int(annotation.get("k", 5)),
        "precision_at_5": round(relevant / retrieved, 4) if retrieved else None,
        "recall_at_5": round(recall_hits / recall_gold, 4) if recall_gold else None,
        "relevant_candidates": relevant,
        "retrieved_candidates": retrieved,
        "gold_section_hits": recall_hits,
        "gold_sections": recall_gold,
        "late_chapter_precision_at_5": (
            round(late_relevant / late_retrieved, 4) if late_retrieved else None
        ),
        "causal_query_count": causal_queries,
    }


def style_stats(text: str) -> dict[str, float | int]:
    text = text.strip()
    total = max(len(text), 1)
    sentences = [
        sentence.strip()
        for sentence in re.split(r"[。！？!?]", text)
        if sentence.strip()
    ]
    sentence_lengths = [len(sentence) for sentence in sentences]
    sentence_count = max(len(sentences), 1)
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    paragraph_lengths = [len(part) for part in paragraphs]
    dialogue = re.findall(r'["“「][^"”」]{2,}["”」]', text)
    sensory_hits = sum(text.count(term) for term in SENSORY_TERMS)

    return {
        "short_sentence_ratio": round(
            sum(length < 15 for length in sentence_lengths) / sentence_count, 4
        ),
        "medium_sentence_ratio": round(
            sum(15 <= length <= 30 for length in sentence_lengths) / sentence_count, 4
        ),
        "long_sentence_ratio": round(
            sum(length > 30 for length in sentence_lengths) / sentence_count, 4
        ),
        "dialogue_ratio": round(sum(len(part) for part in dialogue) / total, 4),
        "paragraph_length_avg": round(mean(paragraph_lengths), 2) if paragraph_lengths else 0,
        "paragraph_length_median": round(median(paragraph_lengths), 2) if paragraph_lengths else 0,
        "sensory_terms_per_1k": round(sensory_hits * 1000 / total, 2),
        "characters": len(text),
        "sentences": len(sentences),
        "paragraphs": len(paragraphs),
    }


def split_numbered_sections(text: str) -> dict[int, str]:
    matches = list(re.finditer(r"(?m)^第(\d+)节：.*$", text))
    sections: dict[int, str] = {}
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections[int(match.group(1))] = text[start:end].strip()
    return sections


def _range_deviation(value: float, low: float, high: float) -> float:
    width = max(high - low, 1e-9)
    if value < low:
        return (low - value) / width
    if value > high:
        return (value - high) / width
    return 0.0


def compute_style_metrics(config: dict[str, Any]) -> dict[str, Any]:
    source_path = ROOT / config["source_file"]
    source = source_path.read_text(encoding="utf-8")
    digest = hashlib.sha256(source_path.read_bytes()).hexdigest().upper()
    expected_hash = config.get("sha256", "").upper()
    if expected_hash and digest != expected_hash:
        raise ValueError(f"golden story hash mismatch: {digest} != {expected_hash}")

    sections = split_numbered_sections(source)
    sample_results = []
    deviations = []
    range_checks = 0
    range_violations = 0
    expected_ranges = config["expected_ranges"]

    for sample in config["samples"]:
        section_number = int(sample["section"])
        if section_number not in sections:
            raise ValueError(f"style sample section {section_number} not found")
        actual = style_stats(sections[section_number])
        per_metric = {}
        for metric, bounds in expected_ranges.items():
            value = float(actual[metric])
            deviation = _range_deviation(value, float(bounds[0]), float(bounds[1]))
            per_metric[metric] = round(deviation, 4)
            deviations.append(deviation)
            range_checks += 1
            range_violations += int(deviation > 0)
        sample_results.append(
            {
                "id": sample["id"],
                "section": section_number,
                "label": sample["label"],
                "stats": actual,
                "range_deviation": per_metric,
            }
        )

    return {
        "source_file": config["source_file"],
        "sha256": digest,
        "sample_count": len(sample_results),
        "full_story_stats": style_stats(source),
        "range_violation_rate": round(range_violations / range_checks, 4),
        "mean_normalized_range_deviation": round(mean(deviations), 4),
        "samples": sample_results,
    }


def compute_character_metrics(annotation: dict[str, Any]) -> dict[str, Any]:
    constraints = annotation.get("constraints", [])
    if not constraints:
        raise ValueError("character annotation must contain constraints")

    characters = {item["character"] for item in constraints}
    counts = {name: 0 for name in characters}
    for item in constraints:
        counts[item["character"]] += 1
    if len(characters) < 3 or any(count < 10 for count in counts.values()):
        raise ValueError("character baseline requires 3 characters and 10 constraints each")

    applicable = [item for item in constraints if item.get("observed_status") != "not_applicable"]
    hard = [item for item in applicable if item.get("hardness") == "hard"]
    violations = [item for item in hard if item.get("observed_status") == "violated"]
    confirmed_hard = [item for item in hard if item.get("review_status") == "human_confirmed"]
    flagged_hard = [item for item in hard if item.get("review_status") == "human_flagged_issue"]
    reviewed_hard = confirmed_hard + flagged_hard
    human_violations = [
        item for item in reviewed_hard if item.get("observed_status") == "violated"
    ]

    return {
        "characters": sorted(characters),
        "constraints": len(constraints),
        "constraints_per_character": counts,
        "provisional_hard_violation_rate": round(len(violations) / len(hard), 4) if hard else None,
        "hard_constraints": len(hard),
        "human_confirmed_hard": len(confirmed_hard),
        "human_flagged_issue_hard": len(flagged_hard),
        "human_reviewed_hard": len(reviewed_hard),
        "human_issue_rule_ids": sorted(item["id"] for item in flagged_hard),
        "human_hard_violation_rate": (
            round(len(human_violations) / len(reviewed_hard), 4)
            if reviewed_hard else None
        ),
        "human_label_coverage": round(len(reviewed_hard) / len(hard), 4) if hard else 0.0,
        "release_gate_ready": bool(reviewed_hard) and len(reviewed_hard) == len(hard),
    }


def build_report() -> dict[str, Any]:
    runtime = load_json(DEFAULT_RUNTIME)
    return {
        "report_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "offline_only": True,
        "golden_story": load_json(DEFAULT_STYLE)["golden_story"],
        "rag": compute_rag_metrics(load_json(DEFAULT_RAG)),
        "character_consistency": compute_character_metrics(load_json(DEFAULT_CHARACTER)),
        "style": compute_style_metrics(load_json(DEFAULT_STYLE)),
        "runtime": runtime,
        "test_baseline": {
            "unit": {"passed": 127, "failed": 0, "warnings": 1},
            "integration": {"passed": 7, "failed": 0, "warnings": 3},
        },
        "limitations": [
            "Redis was unavailable, so historical per-subsection input/output/context tokens and rewrite counts could not be recovered.",
            "All 19 hard character rules were human-reviewed: 17 satisfied and 2 current-draft violations.",
            "The qualitative style issues require dedicated metrics and human sampling; this baseline does not attribute them to the four-control style contract.",
            "The RAG set contains 10 queries; metrics are descriptive and not statistically significant.",
        ],
    }


def render_markdown(report: dict[str, Any]) -> str:
    rag = report["rag"]
    character = report["character_consistency"]
    style = report["style"]
    runtime = report["runtime"]
    unit = report["test_baseline"]["unit"]
    integration = report["test_baseline"]["integration"]
    runtime_coverage = runtime.get("per_subsection_coverage", 0)
    return f"""# Context consistency baseline — 2026-07-17

This report is deterministic and offline; it does not call an LLM.

## Baseline metrics

| Metric | Value |
|---|---:|
| RAG Precision@5 | {rag['precision_at_5']:.1%} |
| RAG Recall@5 | {rag['recall_at_5']:.1%} |
| Late-chapter Precision@5 | {rag['late_chapter_precision_at_5']:.1%} |
| Observed character hard-constraint violation rate | {character['provisional_hard_violation_rate']:.1%} |
| Human-reviewed hard-rule violation rate | {character['human_hard_violation_rate']:.1%} |
| Human character-label coverage | {character['human_label_coverage']:.1%} |
| Style range violation rate | {style['range_violation_rate']:.1%} |
| Style normalized range deviation | {style['mean_normalized_range_deviation']:.4f} |
| Per-subsection context-token coverage | {runtime_coverage:.1%} |

## Existing tests

- Unit: {unit['passed']} passed, {unit['failed']} failed, {unit['warnings']} warning.
- Integration: {integration['passed']} passed, {integration['failed']} failed, {integration['warnings']} warnings.

## Golden story

- Source: `{style['source_file']}`
- SHA-256: `{style['sha256']}`
- Fixed style samples: {style['sample_count']}
- Full-story objective stats: `{json.dumps(style['full_story_stats'], ensure_ascii=False)}`

## Known gaps

""" + "\n".join(f"- {item}" for item in report["limitations"]) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=ROOT / "reports")
    args = parser.parse_args()
    report = build_report()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.output_dir / "quality-baseline-2026-07-17.json"
    markdown_path = args.output_dir / "quality-baseline-2026-07-17.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    print(json_path)
    print(markdown_path)


if __name__ == "__main__":
    main()
