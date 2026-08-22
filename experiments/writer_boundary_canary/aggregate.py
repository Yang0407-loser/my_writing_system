from __future__ import annotations

from collections import Counter
from typing import Any

from .models import BoundaryReview

PAIR_FIELDS = ["naturalness", "less_template", "character_credibility", "emotional_residue", "overall_quality", "more_mechanical"]


def majority(values: list[bool]) -> bool:
    return sum(values) >= 2


def aggregate_reviews(public: dict[str, Any], private: dict[str, Any], raw: list[dict[str, Any]]) -> dict[str, Any]:
    if len(raw) != 3:
        raise ValueError("exactly three reviews required")
    reviews = [BoundaryReview.model_validate(x) for x in raw]
    if len({x.reviewer_id for x in reviews}) != 3:
        raise ValueError("reviewer IDs must be unique")
    tids, pids = [x["text_id"] for x in public["texts"]], [x["pair_id"] for x in public["pairs"]]
    for review in reviews:
        if [x.text_id for x in review.samples] != tids or [x.pair_id for x in review.pairs] != pids:
            raise ValueError("review coverage/order mismatch")
    hard, original, structural = {"W0": [], "W2": []}, {"W0": Counter(), "W2": Counter()}, {"W0": Counter(), "W2": Counter()}
    for tid in tids:
        arm = private["mapping"][tid]["arm"]
        samples = [next(x for x in r.samples if x.text_id == tid) for r in reviews]
        hard[arm].append({k: majority([getattr(x.hard_checks, k) for x in samples]) for k in samples[0].hard_checks.__class__.model_fields})
        for attr, target in (("original_witnesses", original), ("structural_diagnostics", structural)):
            for w in getattr(samples[0], attr):
                target[arm][w.category] += majority([next(x for x in getattr(s, attr) if x.category == w.category).detected for s in samples])
    prefs = {k: Counter() for k in PAIR_FIELDS}
    pairmap = {x["pair_id"]: x for x in public["pairs"]}
    repeat_winners = {"W0": 0, "W2": 0, "tie": 0}
    for review in reviews:
        for pair in review.pairs:
            pub = pairmap[pair.pair_id]
            for field in PAIR_FIELDS:
                choice = getattr(pair, field)
                arm = "tie" if choice == "tie" else private["mapping"][pub[choice]]["arm"]
                prefs[field][arm] += 1
    shares = {k: round(v["W2"] / max(1, v["W0"] + v["W2"]), 4) for k, v in prefs.items()}
    hard_ok = all(x["mandatory_events_complete"] and not x["new_character"] and not x["new_solution"] and not x["relationship_change"] and x["temporary_ending"] and x["boundary_fidelity"] for x in hard["W2"])
    witness_ok = all(original["W2"][k] <= original["W0"][k] for k in ("process_log", "direct_explanation", "event_overengineering"))
    pref_ok = shares["naturalness"] >= .5 and shares["less_template"] >= .5 and shares["overall_quality"] >= .5 and shares["more_mechanical"] <= .5
    split = any(v["W0"] and v["W2"] for v in prefs.values())
    conclusion = "uncertain" if split else ("expand_to_more_unseen_scenes" if hard_ok and witness_ok and pref_ok else "do_not_expand_yet")
    return {
        "schema_version": "1.1", "reviewer_count": 3,
        "hard_checks_by_arm": hard,
        "original_witness_majority_counts": {k: dict(v) for k, v in original.items()},
        "structural_diagnostic_majority_counts": {k: dict(v) for k, v in structural.items()},
        "pair_preferences": {k: dict(v) for k, v in prefs.items()},
        "w2_preference_shares_excluding_ties": shares,
        "acceptance": {"w2_hard_100_percent": hard_ok, "key_witness_not_worse": witness_ok, "preference_thresholds": pref_ok},
        "conclusion": conclusion, "single_total_score": None,
    }

