from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any

from .experiment import DEFAULT_OUTPUT, FIXTURE, ROOT, load_json, write_json
from .review import RealizationPolicyBlindReview, validate_against_public


METRICS = (
    "naturalness",
    "less_template",
    "character_credibility",
    "emotional_residue",
    "overall_quality",
    "mechanicalness",
)
PRIMARY = ("naturalness", "less_template", "overall_quality")


def _fractional_winners(review, key_by_id):
    result = {
        metric: {"A": 0.0, "B": 0.0}
        for metric in (*PRIMARY, "most_mechanical")
    }
    for block in review.blocks:
        winners = block.winners.model_dump()
        for metric in result:
            share = 1 / len(winners[metric])
            for public_id in winners[metric]:
                result[metric][key_by_id[public_id]["arm"]] += share
    return result


def aggregate(output_dir: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    public = load_json(output_dir / "public/blind-review-material.json")
    key = load_json(output_dir / "private/blind-key.json")
    key_by_id = {item["public_text_id"]: item for item in key["entries"]}
    paths = sorted((output_dir / "reviews").glob("reviewer-*.private.json"))
    if len(paths) != 3:
        raise ValueError("exactly three independent reviews are required")
    reviews = []
    for path in paths:
        review = RealizationPolicyBlindReview.model_validate(load_json(path))
        validate_against_public(review, public)
        reviews.append(review)
    if len({item.reviewer_id for item in reviews}) != 3:
        raise ValueError("reviewer IDs must be distinct")

    scores = {
        arm: {metric: [] for metric in METRICS}
        for arm in ("A", "B")
    }
    hard_votes: dict[str, list[bool]] = defaultdict(list)
    violation_votes: dict[str, list[bool]] = defaultdict(list)
    winners = {
        metric: {"A": 0.0, "B": 0.0}
        for metric in (*PRIMARY, "most_mechanical")
    }
    text_scores = defaultdict(lambda: defaultdict(list))
    for review in reviews:
        fractional = _fractional_winners(review, key_by_id)
        for metric, arms in fractional.items():
            for arm, value in arms.items():
                winners[metric][arm] += value
        for block in review.blocks:
            for assessment in block.assessments:
                public_id = assessment.public_text_id
                arm = key_by_id[public_id]["arm"]
                hard_votes[public_id].append(assessment.hard_task_complete)
                violation_votes[public_id].append(
                    assessment.unauthorized_event_detected
                )
                for metric in METRICS:
                    value = getattr(assessment, metric)
                    scores[arm][metric].append(value)
                    text_scores[public_id][metric].append(value)

    score_means = {
        arm: {
            metric: round(mean(values), 3)
            for metric, values in metrics.items()
        }
        for arm, metrics in scores.items()
    }
    primary_wins = sum(
        score_means["B"][metric] > score_means["A"][metric]
        for metric in PRIMARY
    )
    hard_by_arm = {
        arm: {"majority_pass": 0, "majority_violation": 0}
        for arm in ("A", "B")
    }
    for public_id, item in key_by_id.items():
        arm = item["arm"]
        hard_by_arm[arm]["majority_pass"] += int(
            sum(hard_votes[public_id]) >= 2
        )
        hard_by_arm[arm]["majority_violation"] += int(
            sum(violation_votes[public_id]) >= 2
        )

    scene_wins = 0
    scene_detail = []
    for scene_id in sorted({item["scene_id"] for item in key["entries"]}):
        ids = [
            item["public_text_id"]
            for item in key["entries"]
            if item["scene_id"] == scene_id
        ]
        metric_means = {}
        for metric in ("naturalness", "less_template"):
            metric_means[metric] = {}
            for arm in ("A", "B"):
                values = [
                    mean(text_scores[public_id][metric])
                    for public_id in ids
                    if key_by_id[public_id]["arm"] == arm
                ]
                metric_means[metric][arm] = round(mean(values), 3)
        won = any(
            metric_means[metric]["B"] > metric_means[metric]["A"]
            for metric in metric_means
        )
        scene_wins += int(won)
        scene_detail.append(
            {"scene_id": scene_id, "metric_means": metric_means, "b_win": won}
        )

    manifest = load_json(output_dir / "private/locked-manifest.json")
    lengths = {"A": [], "B": []}
    pair_confound = []
    deterministic_gate_failures = []
    for sample in manifest["samples"]:
        record = load_json(
            output_dir / f"private/texts/{sample['sample_id']}.json"
        )
        checks = record["checks"]
        lengths[sample["arm"]].append(checks["visible_characters"])
        if not (
            checks["nonempty"]
            and checks["within_preregistered_band"]
            and not checks["truncated"]
            and checks["all_required_term_groups_pass"]
            and checks["unauthorized_content_proxy_pass"]
            and not checks["field_leakage_detected"]
            and checks["exact_copied_sentence_count"] == 0
        ):
            deterministic_gate_failures.append(sample["sample_id"])
    for scene_id in sorted({item["scene_id"] for item in manifest["samples"]}):
        for repeat in (1, 2):
            pair = [
                item
                for item in manifest["samples"]
                if item["scene_id"] == scene_id
                and item["repeat"] == repeat
            ]
            values = {}
            for sample in pair:
                record = load_json(
                    output_dir / f"private/texts/{sample['sample_id']}.json"
                )
                values[sample["arm"]] = record["checks"][
                    "visible_characters"
                ]
            fraction = abs(values["A"] - values["B"]) / mean(values.values())
            if fraction > 0.2:
                pair_confound.append(
                    {
                        "scene_id": scene_id,
                        "repeat": repeat,
                        "fraction": round(fraction, 4),
                    }
                )
    arm_mean = {arm: mean(values) for arm, values in lengths.items()}
    arm_mean_ratio = max(arm_mean.values()) / min(arm_mean.values())
    length_confound = bool(pair_confound) or arm_mean_ratio > 1.1
    human_hard_pass = all(
        item["majority_pass"] == 8
        and item["majority_violation"] == 0
        for item in hard_by_arm.values()
    )
    mechanical_not_worse = (
        winners["most_mechanical"]["B"]
        <= winners["most_mechanical"]["A"]
    )
    promoted = (
        primary_wins >= 2
        and scene_wins >= 3
        and mechanical_not_worse
        and human_hard_pass
        and not deterministic_gate_failures
        and not length_confound
    )
    result = {
        "schema_version": "realization-policy-canary-aggregate-v1",
        "reviewer_count": 3,
        "score_means_by_arm": score_means,
        "fractional_winner_counts": winners,
        "primary_mean_wins_b_vs_a": primary_wins,
        "scene_directional_wins_b_vs_a": scene_wins,
        "scene_detail": scene_detail,
        "hard_outcomes_by_arm": hard_by_arm,
        "deterministic_gate_failures": deterministic_gate_failures,
        "length": {
            "mean_by_arm": {
                arm: round(value, 2) for arm, value in arm_mean.items()
            },
            "arm_mean_ratio": round(arm_mean_ratio, 4),
            "pair_confounds": pair_confound,
            "confounded": length_confound,
        },
        "decision_components": {
            "b_beats_a_on_two_primary_means": primary_wins >= 2,
            "b_wins_at_least_three_scenes": scene_wins >= 3,
            "b_not_more_often_most_mechanical": mechanical_not_worse,
            "all_human_hard_gates_pass": human_hard_pass,
            "all_deterministic_gates_pass": not deterministic_gate_failures,
            "length_not_confounded": not length_confound,
        },
        "conclusion": (
            "promote_to_writer_canary"
            if promoted
            else "do_not_promote"
        ),
        "production_default_changed": False,
        "limitations": [
            "Four scenes and two repeats per arm are directional evidence only.",
            "Three independent blind reviews are required; no single total score is used.",
            "The B arm adds prompt tokens and Sparse Kernel together with Realization Policy."
        ],
    }
    write_json(output_dir / "aggregate.json", result)
    return result


if __name__ == "__main__":
    print(json.dumps(aggregate(), ensure_ascii=False, indent=2))
