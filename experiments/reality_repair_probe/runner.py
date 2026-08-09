"""One-pass A/B probe for deterministic narrative-reality warnings.

This module is intentionally outside the production Writer path. It preserves
the original text, performs at most one revision call per warned subsection,
and measures whether the revision removed known warnings without broad edits.
"""

from __future__ import annotations

from collections import Counter
from difflib import SequenceMatcher
import hashlib
from typing import Callable, Iterable

from app.writing.narrative_reality_checks import NarrativeRealityChecker


REALITY_REPAIR_PROMPT_VERSION = "reality-repair-probe-v0"


def build_repair_instruction(record: dict) -> str:
    warnings = record.get("warnings") or []
    lines = [
        "只修复下列可验证的叙事真实性冲突。",
        "不得润色全文，不得改变叙事风格、事件顺序、人物目标或视角。",
        "优先修改直接造成冲突的最少句子；不要增加抒情、比喻、总结或主题升华。",
        "如果必须补充原因，只补充能够闭合因果链的最短事实。",
        "修订完成后直接输出完整正文，不解释修改过程。",
        "",
        "确定性 warning：",
    ]
    for index, warning in enumerate(warnings, 1):
        lines.extend(
            [
                f"{index}. [{warning['code']}] {warning['message']}",
                f"   证据：{warning['evidence']}",
            ]
        )
    return "\n".join(lines)


def evaluate_sections(
    sections: Iterable[str],
    *,
    known_context: str = "",
    allowed_names: list[str] | None = None,
) -> list[dict]:
    checker = NarrativeRealityChecker(allowed_names=allowed_names)
    return [
        checker.observe(
            text,
            section=1,
            subsection=index,
            known_context=known_context,
        )
        for index, text in enumerate(sections, 1)
    ]


def _warning_codes(record: dict) -> list[str]:
    return [item["code"] for item in record.get("warnings") or []]


def _changed_ratio(original: str, revised: str) -> float:
    return round(1.0 - SequenceMatcher(None, original, revised).ratio(), 6)


def run_repair_probe(
    sections: list[str],
    *,
    revise: Callable[[str, str], str],
    known_context: str = "",
    allowed_names: list[str] | None = None,
    minimum_resolution_rate: float = 6 / 7,
    maximum_changed_ratio: float = 0.10,
) -> dict:
    """Run exactly one targeted revision for each warned subsection."""
    originals = list(sections)
    before_records = evaluate_sections(
        originals, known_context=known_context, allowed_names=allowed_names
    )
    revised_sections: list[str] = []
    revision_calls = 0
    for original, record in zip(originals, before_records):
        if record["warning_count"]:
            revision_calls += 1
            revised = revise(original, build_repair_instruction(record)).strip()
            revised_sections.append(revised or original)
        else:
            revised_sections.append(original)

    after_records = evaluate_sections(
        revised_sections, known_context=known_context, allowed_names=allowed_names
    )
    cases: list[dict] = []
    before_counter: Counter[str] = Counter()
    after_counter: Counter[str] = Counter()
    for index, (original, revised, before, after) in enumerate(
        zip(originals, revised_sections, before_records, after_records), 1
    ):
        before_codes = _warning_codes(before)
        after_codes = _warning_codes(after)
        before_counter.update(before_codes)
        after_counter.update(after_codes)
        cases.append(
            {
                "subsection": index,
                "original": original,
                "revised": revised,
                "original_hash": hashlib.sha256(original.encode("utf-8")).hexdigest(),
                "revised_hash": hashlib.sha256(revised.encode("utf-8")).hexdigest(),
                "before_warning_codes": before_codes,
                "after_warning_codes": after_codes,
                "resolved_warning_codes": sorted(set(before_codes) - set(after_codes)),
                "new_warning_codes": sorted(set(after_codes) - set(before_codes)),
                "changed_ratio": _changed_ratio(original, revised),
            }
        )

    resolved_count = sum(
        max(0, count - after_counter.get(code, 0))
        for code, count in before_counter.items()
    )
    new_count = sum(
        max(0, count - before_counter.get(code, 0))
        for code, count in after_counter.items()
    )
    original_warning_count = sum(before_counter.values())
    resolution_rate = (
        resolved_count / original_warning_count if original_warning_count else 1.0
    )
    combined_change_ratio = _changed_ratio("\n".join(originals), "\n".join(revised_sections))
    automatic_criteria_pass = (
        original_warning_count > 0
        and resolution_rate >= minimum_resolution_rate
        and new_count == 0
        and combined_change_ratio <= maximum_changed_ratio
    )
    return {
        "version": REALITY_REPAIR_PROMPT_VERSION,
        "production_effect": False,
        "revision_calls": revision_calls,
        "original_warning_count": original_warning_count,
        "remaining_warning_count": sum(after_counter.values()),
        "resolved_warning_count": resolved_count,
        "new_warning_count": new_count,
        "resolution_rate": round(resolution_rate, 6),
        "combined_changed_ratio": combined_change_ratio,
        "thresholds": {
            "minimum_resolution_rate": minimum_resolution_rate,
            "maximum_changed_ratio": maximum_changed_ratio,
        },
        "automatic_criteria_pass": automatic_criteria_pass,
        "promotion_status": (
            "pending_human_review" if automatic_criteria_pass else "failed"
        ),
        "human_review_required": True,
        "cases": cases,
    }
