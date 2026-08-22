from __future__ import annotations

import re
from collections import Counter
from typing import Any

from app.utils.llm_client import estimate_tokens

from .metrics import overlap_metrics
from .models import StyleDemonstration, StyleDemonstrations


_NORMALISE_RE = re.compile(r"[\s，。！？；：、、“”‘’（）()—…·,.!?;:'\"]+")
_CJK_RE = re.compile(r"[\u3400-\u9fff]")


def _normalise(text: str) -> str:
    return _NORMALISE_RE.sub("", text).lower()


def _ngrams(text: str, size: int) -> Counter[str]:
    value = _normalise(text)
    if len(value) < size:
        return Counter()
    return Counter(value[index : index + size] for index in range(len(value) - size + 1))


def _term_hits(text: str, protected_terms: list[str]) -> list[str]:
    normalised = _normalise(text)
    return sorted(
        {
            term
            for term in protected_terms
            if len(_normalise(term)) >= 2 and _normalise(term) in normalised
        }
    )


def inspect_overlap(
    text: str,
    reference: str,
    protected_terms: list[str] | None = None,
) -> dict[str, Any]:
    base = overlap_metrics(text, reference)
    common_8 = sorted(set(_ngrams(text, 8)).intersection(_ngrams(reference, 8)))
    return {
        **base,
        "shared_8gram_unique_count": len(common_8),
        "shared_8gram_examples": common_8[:10],
        "protected_term_hits": _term_hits(text, protected_terms or []),
    }


def validate_demonstration(
    demonstration: StyleDemonstration,
    *,
    reference: str,
    protected_terms: list[str],
    require_40_to_100_cjk: bool,
    max_longest_common_chars: int = 10,
) -> dict[str, Any]:
    metrics = inspect_overlap(demonstration.text, reference, protected_terms)
    cjk_chars = len(_CJK_RE.findall(demonstration.text))
    failures: list[str] = []
    if require_40_to_100_cjk and not 40 <= cjk_chars <= 100:
        failures.append("positive_demo_cjk_length_outside_40_100")
    if metrics["exact_copied_sentence_count"]:
        failures.append("exact_sentence_copy")
    if metrics["shared_12gram_unique_count"]:
        failures.append("shared_12gram")
    if metrics["longest_common_contiguous_chars"] > max_longest_common_chars:
        failures.append("longest_common_contiguous_chars")
    if metrics["protected_term_hits"]:
        failures.append("protected_term")
    return {
        "demonstration_id": demonstration.demonstration_id,
        "mechanism": demonstration.mechanism,
        "cjk_chars": cjk_chars,
        "usable": not failures,
        "failures": failures,
        "metrics": metrics,
    }


def validate_demonstrations(
    demonstrations: StyleDemonstrations,
    *,
    reference: str,
    protected_terms: list[str],
) -> dict[str, Any]:
    positive = [
        validate_demonstration(
            item,
            reference=reference,
            protected_terms=protected_terms,
            require_40_to_100_cjk=True,
        )
        for item in demonstrations.positive_demonstrations
    ]
    negative = [
        validate_demonstration(
            item,
            reference=reference,
            protected_terms=protected_terms,
            require_40_to_100_cjk=False,
        )
        for item in demonstrations.negative_demonstrations
    ]
    demonstration_tokens = estimate_tokens(
        "\n".join(
            item.text
            for item in (
                demonstrations.positive_demonstrations
                + demonstrations.negative_demonstrations
            )
        )
    )
    failures = [
        item["demonstration_id"]
        for item in positive + negative
        if not item["usable"]
    ]
    if demonstration_tokens > demonstrations.max_demonstration_tokens:
        failures.append("demonstration_token_cap")
    return {
        "usable": not failures,
        "failures": failures,
        "estimated_demonstration_tokens": demonstration_tokens,
        "max_demonstration_tokens": demonstrations.max_demonstration_tokens,
        "positive": positive,
        "negative": negative,
    }


def require_safe_demonstrations(
    demonstrations: StyleDemonstrations,
    *,
    reference: str,
    protected_terms: list[str],
) -> dict[str, Any]:
    result = validate_demonstrations(
        demonstrations,
        reference=reference,
        protected_terms=protected_terms,
    )
    if not result["usable"]:
        raise ValueError(
            "unsafe style demonstrations; regenerate before planning: "
            + ", ".join(result["failures"])
        )
    return result
