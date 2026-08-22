"""Deterministic retrieval diagnostics that never call an LLM."""

from __future__ import annotations

import re
from typing import Any


def _normalize(text: str) -> str:
    return re.sub(r"\s+|[^0-9A-Za-z\u4e00-\u9fff]", "", text).lower()


def _shingles(text: str, width: int) -> set[str]:
    normalized = _normalize(text)
    if len(normalized) < width:
        return {normalized} if normalized else set()
    return {normalized[index:index + width] for index in range(len(normalized) - width + 1)}


def estimate_candidate_usage(candidate_text: str, output_text: str) -> dict[str, Any]:
    """Estimate whether retrieved evidence appears in Writer output.

    ``possible_paraphrase`` is deliberately labelled as a heuristic.  It is
    useful for separating obvious non-use from likely use, but is not a truth
    label and must not replace human evaluation.
    """

    candidate = _normalize(candidate_text)
    output = _normalize(output_text)
    if len(candidate) < 8 or len(output) < 8:
        return {"classification": "not_observed", "overlap_score": 0.0, "heuristic": True}

    exact_width = min(16, len(candidate))
    exact = any(shingle in output for shingle in _shingles(candidate, exact_width))
    candidate_shingles = _shingles(candidate, 6)
    output_shingles = _shingles(output, 6)
    overlap = (
        len(candidate_shingles & output_shingles) / len(candidate_shingles)
        if candidate_shingles else 0.0
    )
    if exact:
        classification = "exact_or_near_exact"
    elif overlap >= 0.08:
        classification = "possible_paraphrase"
    else:
        classification = "not_observed"
    return {
        "classification": classification,
        "overlap_score": round(overlap, 4),
        "heuristic": True,
    }


def measure_retrieval_usage(candidates: list[dict], output_text: str) -> list[dict[str, Any]]:
    """Return one traceable usage estimate for each retrieved candidate."""

    results = []
    for rank, candidate in enumerate(candidates, 1):
        usage = estimate_candidate_usage(candidate.get("text", ""), output_text)
        results.append(
            {
                "id": candidate.get("id") or candidate.get("source_id") or f"rank-{rank}",
                "rank": candidate.get("rank", rank),
                **usage,
            }
        )
    return results
