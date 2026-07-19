"""Deterministic, prose-free signals for Phase 4 shadow generations."""

from __future__ import annotations

import hashlib
import re

from .context_census import estimate_tokens


AI_CLICHES = (
    "在这个充满",
    "不仅是一种",
    "更是一种",
    "随着时间的推移",
    "眼中闪过一丝",
)

PSYCHOLOGICAL_NARRATION = (
    "她想起",
    "她觉得",
    "她知道",
    "她意识到",
    "她以为",
    "她明白",
    "胸口涌",
)


def _occurrences(text: str, phrases: tuple[str, ...]) -> dict[str, int]:
    return {phrase: text.count(phrase) for phrase in phrases if phrase in text}


def deterministic_output_checks(text: str) -> dict:
    """Return stable style/shape signals without retaining generated prose."""
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    normalized = [re.sub(r"\s+", "", part) for part in paragraphs]
    duplicate_paragraphs = len(normalized) - len(set(normalized))
    dialogue_paragraphs = sum(
        1 for part in paragraphs if "“" in part or "”" in part or '"' in part
    )
    return {
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "characters": len(text),
        "estimated_tokens": estimate_tokens(text),
        "paragraph_count": len(paragraphs),
        "dialogue_paragraph_count": dialogue_paragraphs,
        "duplicate_paragraph_count": duplicate_paragraphs,
        "ai_cliche_occurrences": _occurrences(text, AI_CLICHES),
        "psychological_narration_occurrences": _occurrences(
            text, PSYCHOLOGICAL_NARRATION
        ),
    }
