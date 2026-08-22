from __future__ import annotations

from collections import Counter
from typing import Any

from .models import CanaryReview


PREFERENCE_FIELDS = ["naturalness", "less_template", "overall_quality"]


def _majority(values: list[bool]) -> bool | None:
    yes = sum(values)
    if yes > len(values) / 2:
        return True
    if yes < len(values) / 2:
        return False
    return None


def aggregate_reviews(
    public: dict[str, Any], private: dict[str, Any], raw_reviews: list[dict[str, Any]]
) -> dict[str, Any]:
    if len(raw_reviews) != 3:
        raise ValueError("exactly three independent reviewer files are required")
    reviews = [CanaryReview.model_validate(item) for item in raw_reviews]
    ids = [item.reviewer_id for item in reviews]
    if len(set(ids)) != 3:
        raise ValueError("reviewer IDs must be unique")
    expected_texts = [item["text_id"] for item in public["texts"]]
    expected_pairs = [item["pair_id"] for item in public["pairs"]]
    for review in reviews:
        if [item.text_id for item in review.samples] != expected_texts:
            raise ValueError("review sample order/coverage mismatch")
        if [item.pair_id for item in review.pairs] != expected_pairs:
            raise ValueError("review pair order/coverage mismatch")

    mapping = private["mapping"]
    witness_majority: dict[str, Counter] = {"W0": Counter(), "W1": Counter()}
    hard_by_arm: dict[str, list[Any]] = {"W0": [], "W1": []}
    for text_id in expected_texts:
        arm = mapping[text_id]["arm"]
        rows = [next(x for x in r.samples if x.text_id == text_id) for r in reviews]
        hard_by_arm[arm].append({
            field: _majority([getattr(row.hard_checks, field) for row in rows])
            for field in rows[0].hard_checks.__class__.model_fields
        })
        for kind in rows[0].witnesses:
            votes = [
                next(x for x in row.witnesses if x.category == kind.category).detected
                for row in rows
            ]
            witness_majority[arm][kind.category] += _majority(votes) is True

    preferences: dict[str, dict[str, int]] = {
        field: {"W0": 0, "W1": 0, "tie": 0} for field in PREFERENCE_FIELDS
    }
    pair_by_id = {row["pair_id"]: row for row in public["pairs"]}
    for review in reviews:
        for pair_review in review.pairs:
            pair = pair_by_id[pair_review.pair_id]
            for field in PREFERENCE_FIELDS:
                choice = getattr(pair_review, field)
                if choice == "tie":
                    preferences[field]["tie"] += 1
                else:
                    text_id = pair[choice]
                    preferences[field][mapping[text_id]["arm"]] += 1

    w1_hard_pass = all(
        row["mandatory_events_complete"] is True
        and row["new_character"] is False
        and row["new_solution"] is False
        and row["relationship_change"] is False
        and row["temporary_ending"] is True
        and row["decision_fidelity"] is True
        for row in hard_by_arm["W1"]
    )
    comparisons = {
        kind: witness_majority["W1"][kind] <= witness_majority["W0"][kind]
        for kind in ("process_log", "direct_explanation", "event_overengineering")
    }
    shares = {
        field: round(values["W1"] / max(1, values["W0"] + values["W1"]), 4)
        for field, values in preferences.items()
    }
    directional = (
        w1_hard_pass and all(comparisons.values())
        and all(shares[field] >= 0.5 for field in PREFERENCE_FIELDS)
    )
    split_pairs = any(
        values["W0"] > 0 and values["W1"] > 0 for values in preferences.values()
    )
    conclusion = (
        "uncertain"
        if split_pairs
        else ("expand_to_more_unseen_scenes" if directional else "do_not_expand_yet")
    )
    return {
        "schema_version": "1.0",
        "reviewer_count": 3,
        "hard_checks_by_arm": hard_by_arm,
        "witness_majority_counts": {k: dict(v) for k, v in witness_majority.items()},
        "pair_preferences": preferences,
        "w1_preference_shares_excluding_ties": shares,
        "acceptance": {
            "w1_hard_obligations_100_percent": w1_hard_pass,
            "witness_not_higher": comparisons,
            "preference_thresholds": {k: v >= 0.5 for k, v in shares.items()},
        },
        "conclusion": conclusion,
        "single_total_score": None,
    }
