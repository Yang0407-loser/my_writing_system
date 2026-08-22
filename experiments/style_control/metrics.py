from __future__ import annotations

import re
from collections import Counter
from typing import Any

from app.style_observability import analyse_text, split_sentences
from app.style_stats import compute_all_stats


def _normalise(text: str) -> str:
    return re.sub(r"[\s，。！？；：、、“”‘’（）()—…·,.!?;:'\"]+", "", text).lower()


def _ngrams(text: str, size: int) -> Counter[str]:
    value = _normalise(text)
    if len(value) < size:
        return Counter()
    return Counter(value[index : index + size] for index in range(len(value) - size + 1))


def overlap_metrics(text: str, reference: str) -> dict[str, Any]:
    generated_sentences = {
        _normalise(sentence): sentence
        for sentence in split_sentences(text)
        if len(_normalise(sentence)) >= 8
    }
    reference_sentences = {
        _normalise(sentence): sentence
        for sentence in split_sentences(reference)
        if len(_normalise(sentence)) >= 8
    }
    exact = sorted(set(generated_sentences).intersection(reference_sentences))
    generated_12 = _ngrams(text, 12)
    reference_12 = _ngrams(reference, 12)
    common_12 = set(generated_12).intersection(reference_12)

    left = _normalise(text)
    right = _normalise(reference)
    previous = [0] * (len(right) + 1)
    longest = 0
    for char_left in left:
        current = [0]
        for index, char_right in enumerate(right, 1):
            value = previous[index - 1] + 1 if char_left == char_right else 0
            current.append(value)
            longest = max(longest, value)
        previous = current

    return {
        "exact_copied_sentence_count": len(exact),
        "exact_copied_sentence_examples": [generated_sentences[item][:100] for item in exact[:5]],
        "shared_12gram_unique_count": len(common_12),
        "longest_common_contiguous_chars": longest,
    }


def compute_metrics(text: str, reference: str, character_names: list[str]) -> dict[str, Any]:
    observable = analyse_text(text, character_names)
    stats = compute_all_stats(text)
    buckets = observable["sentence_length_buckets"]
    sentence_count = max(sum(buckets.values()), 1)
    return {
        "characters": observable["characters"],
        "dialogue_ratio": observable["dialogue_ratio"],
        "sentence_length_mean": observable["sentence_length"]["mean"],
        "sentence_length_median": observable["sentence_length"]["median"],
        "sentence_length_p90": observable["sentence_length"]["p90"],
        "short_sentence_ratio": round(buckets["short_le_12"] / sentence_count, 4),
        "short_sentence_run_count": len(observable["consecutive_short_sentence_runs"]),
        "paragraph_length_mean": observable["paragraph_length"]["mean"],
        "paragraph_length_median": observable["paragraph_length"]["median"],
        "paragraph_length_p90": observable["paragraph_length"]["p90"],
        "mechanical_start_ratio": observable["mechanical_start_ratio"],
        "character_name_start_count": observable["sentence_starts"].get("character_name", 0),
        "isomorphic_run_count": len(observable["consecutive_isomorphic_sentence_runs"]),
        "exact_repeated_sentence_instances": observable["exact_repeated_sentence_instances"],
        "sensory_terms_per_1k": observable["sensory_terms_per_1k"],
        "psychological_exposition_per_1k": observable["psychological_exposition_per_1k"],
        "adjective_density": stats["adjective_density"],
        "adverb_density": stats["adverb_density"],
        "metaphor_density": stats["metaphor_density"],
        **overlap_metrics(text, reference),
    }

