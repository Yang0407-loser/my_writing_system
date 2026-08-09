from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


STYLE_SCORE_FIELDS = (
    "narrative_distance",
    "sentence_rhythm",
    "paragraph_rhythm",
    "dialogue_function",
    "dialogue_texture",
    "emotional_mediation",
)
QUALITY_SCORE_FIELDS = (
    "naturalness",
    "scene_completion",
    "character_credibility",
    "emotional_layers",
    "mechanical_problem",
    "repetition_problem",
    "overall_reading_preference",
)
HARD_FLAG_FIELDS = (
    "plot_or_character_error",
    "core_task_miss",
    "severe_prompt_conflict",
)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _mean(values: list[float]) -> float:
    return round(statistics.mean(values), 3)


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def validate_reviews(
    public: dict[str, Any],
    private: dict[str, Any],
    reviews: list[dict[str, Any]],
) -> dict[str, Any]:
    expected_samples = [item["blind_id"] for item in public["samples"]]
    expected_pairs = [item["pair_id"] for item in public["pairs"]]
    private_samples = [item["blind_id"] for item in private["samples"]]
    private_pairs = [item["pair_id"] for item in private["pairs"]]
    errors: list[str] = []
    if set(expected_samples) != set(private_samples):
        errors.append("public/private sample ID mismatch")
    if set(expected_pairs) != set(private_pairs):
        errors.append("public/private pair ID mismatch")
    reviewer_ids = [str(review.get("reviewer_id", "")) for review in reviews]
    if len(set(reviewer_ids)) != len(reviewer_ids):
        errors.append("reviewer IDs are not unique")

    for review in reviews:
        reviewer_id = str(review.get("reviewer_id", ""))
        scope = review.get("review_scope", {})
        if scope.get("independent_blind_review") is not True:
            errors.append(f"{reviewer_id}: independent_blind_review is not true")
        if scope.get("private_key_accessed") is not False:
            errors.append(f"{reviewer_id}: private_key_accessed is not false")
        if scope.get("other_reviews_accessed") is not False:
            errors.append(f"{reviewer_id}: other_reviews_accessed is not false")
        sample_rows = review.get("samples", [])
        pair_rows = review.get("pairs", [])
        sample_ids = [item.get("blind_id") for item in sample_rows]
        pair_ids = [item.get("pair_id") for item in pair_rows]
        if sample_ids != expected_samples:
            errors.append(f"{reviewer_id}: sample IDs/order differ from public material")
        if pair_ids != expected_pairs:
            errors.append(f"{reviewer_id}: pair IDs/order differ from public material")
        if len(set(sample_ids)) != len(sample_ids):
            errors.append(f"{reviewer_id}: duplicate sample ID")
        if len(set(pair_ids)) != len(pair_ids):
            errors.append(f"{reviewer_id}: duplicate pair ID")

        for row in sample_rows:
            blind_id = row.get("blind_id", "?")
            if row.get("style_choice") not in {"S1", "S2", "S3"}:
                errors.append(f"{reviewer_id}/{blind_id}: invalid style_choice")
            for field, value in [("s3_closeness", row.get("s3_closeness"))]:
                if type(value) is not int or not 1 <= value <= 5:
                    errors.append(f"{reviewer_id}/{blind_id}: invalid {field}")
            for block_name, fields in (
                ("style_scores", STYLE_SCORE_FIELDS),
                ("quality_scores", QUALITY_SCORE_FIELDS),
            ):
                block = row.get(block_name, {})
                for field in fields:
                    value = block.get(field)
                    if type(value) is not int or not 1 <= value <= 5:
                        errors.append(
                            f"{reviewer_id}/{blind_id}: invalid {block_name}.{field}"
                        )
            hard_flags = row.get("hard_flags", {})
            for field in HARD_FLAG_FIELDS:
                if type(hard_flags.get(field)) is not bool:
                    errors.append(f"{reviewer_id}/{blind_id}: invalid hard_flags.{field}")
            if any(hard_flags.get(field) is True for field in HARD_FLAG_FIELDS):
                if not str(row.get("hard_error_evidence", "")).strip():
                    errors.append(f"{reviewer_id}/{blind_id}: hard flag without evidence")

        for row in pair_rows:
            pair_id = row.get("pair_id", "?")
            for field in ("closer_to_s3", "better_quality"):
                if row.get(field) not in {"text_1", "text_2", "tie"}:
                    errors.append(f"{reviewer_id}/{pair_id}: invalid {field}")
            confidence = row.get("confidence")
            if type(confidence) is not int or not 1 <= confidence <= 5:
                errors.append(f"{reviewer_id}/{pair_id}: invalid confidence")

    if errors:
        raise ValueError("review validation failed:\n- " + "\n- ".join(errors))
    return {
        "valid": True,
        "reviewer_ids": reviewer_ids,
        "reviewer_count": len(reviews),
        "sample_reviews_per_reviewer": len(expected_samples),
        "pair_reviews_per_reviewer": len(expected_pairs),
        "independent_blind_review_confirmed": True,
    }


def aggregate_reviews(run_dir: Path, review_dir: Path | None = None) -> dict[str, Any]:
    review_dir = review_dir or run_dir / "reviews"
    public = _read_json(run_dir / "blind-review-public.json")
    private = _read_json(run_dir / "blind-review-key.private.json")
    plan = _read_json(run_dir / "contract_ablation_run_manifest.json")
    source = _read_json(Path(plan["manifest_path"]))
    experiment = source["experiment"]
    arms = tuple(experiment["arms"])
    baseline_arm = experiment.get("pair_baseline_arm", "D0")
    candidate_arms = tuple(
        experiment.get("pair_candidate_arms", ["D1", "D2", "D3", "F0"])
    )
    production_candidate_arms = tuple(
        experiment.get("production_candidate_arms", arms)
    )
    review_paths = sorted(review_dir.glob("reviewer-*.json"))
    reviews = [_read_json(path) for path in review_paths]
    validation = validate_reviews(public, private, reviews)

    sample_key = {item["blind_id"]: item for item in private["samples"]}
    pair_key = {item["pair_id"]: item for item in private["pairs"]}
    sample_reviews: dict[str, list[dict[str, Any]]] = defaultdict(list)
    pair_reviews: dict[str, list[dict[str, Any]]] = defaultdict(list)
    hard_evidence: list[dict[str, Any]] = []

    for review in reviews:
        reviewer_id = str(review["reviewer_id"])
        for row in review["samples"]:
            mapped = sample_key[row["blind_id"]]
            enriched = {**row, "reviewer_id": reviewer_id, **mapped}
            sample_reviews[mapped["arm"]].append(enriched)
            for field in HARD_FLAG_FIELDS:
                if row["hard_flags"][field]:
                    hard_evidence.append(
                        {
                            "reviewer_id": reviewer_id,
                            "blind_id": row["blind_id"],
                            "sample_id": mapped["sample_id"],
                            "arm": mapped["arm"],
                            "scene_id": mapped["scene_id"],
                            "repeat": mapped["repeat"],
                            "flag": field,
                            "evidence": row["hard_error_evidence"],
                            "comment": row["comment"],
                        }
                    )
        for row in review["pairs"]:
            pair_reviews[pair_key[row["pair_id"]]["option_1_arm"]].append(
                {**row, "reviewer_id": reviewer_id, **pair_key[row["pair_id"]]}
            )

    by_arm: dict[str, Any] = {}
    sample_mean_by_id: dict[str, dict[str, float]] = defaultdict(dict)
    for arm in arms:
        rows = sample_reviews[arm]
        style_counts = Counter(item["style_choice"] for item in rows)
        scores = {
            field: _mean([item["style_scores"][field] for item in rows])
            for field in STYLE_SCORE_FIELDS
        }
        quality = {
            field: _mean([item["quality_scores"][field] for item in rows])
            for field in QUALITY_SCORE_FIELDS
        }
        hard_votes = {
            field: sum(item["hard_flags"][field] for item in rows)
            for field in HARD_FLAG_FIELDS
        }
        flagged_samples = {
            field: sorted(
                {
                    item["sample_id"]
                    for item in rows
                    if item["hard_flags"][field]
                }
            )
            for field in HARD_FLAG_FIELDS
        }
        arm_sample_ids = sorted({item["sample_id"] for item in rows})
        sample_s3_means = []
        sample_quality_means = []
        for sample_id in arm_sample_ids:
            sample_rows = [item for item in rows if item["sample_id"] == sample_id]
            s3_value = _mean([item["s3_closeness"] for item in sample_rows])
            quality_value = _mean(
                [item["quality_scores"]["overall_reading_preference"] for item in sample_rows]
            )
            sample_mean_by_id[sample_id] = {
                "s3_closeness": s3_value,
                "overall_reading_preference": quality_value,
            }
            sample_s3_means.append(s3_value)
            sample_quality_means.append(quality_value)
        by_arm[arm] = {
            "sample_count": len(arm_sample_ids),
            "review_vote_count": len(rows),
            "style_identification_votes": dict(style_counts),
            "s3_identification": {
                "correct_votes": style_counts["S3"],
                "total_votes": len(rows),
                "rate": _rate(style_counts["S3"], len(rows)),
            },
            "s3_closeness_mean": _mean([item["s3_closeness"] for item in rows]),
            "style_scores_mean": scores,
            "quality_scores_mean": quality,
            "stability_across_samples": {
                "s3_closeness_sample_mean_sd": round(
                    statistics.pstdev(sample_s3_means), 3
                ),
                "overall_preference_sample_mean_sd": round(
                    statistics.pstdev(sample_quality_means), 3
                ),
                "note": (
                    f"SD of {len(arm_sample_ids)} sample-level means; descriptive only."
                ),
            },
            "hard_flag_votes": hard_votes,
            "flagged_samples": flagged_samples,
            "human_hard_gate_pass": not any(hard_votes.values()),
        }

    paired: dict[str, Any] = {}
    for candidate in candidate_arms:
        relevant: list[dict[str, Any]] = []
        for rows in pair_reviews.values():
            relevant.extend(
                item
                for item in rows
                if candidate in {item["option_1_arm"], item["option_2_arm"]}
            )
        style_votes = Counter()
        quality_votes = Counter()
        confidences = []
        for row in relevant:
            for field, bucket in (
                ("closer_to_s3", style_votes),
                ("better_quality", quality_votes),
            ):
                choice = row[field]
                if choice == "tie":
                    bucket["tie"] += 1
                else:
                    chosen_arm = (
                        row["option_1_arm"] if choice == "text_1" else row["option_2_arm"]
                    )
                    bucket[
                        "candidate" if chosen_arm == candidate else "baseline"
                    ] += 1
            confidences.append(row["confidence"])
        total = len(relevant)
        paired_row = {
            "comparison": f"{candidate} vs {baseline_arm}",
            "vote_count": total,
            "style": {
                "candidate_wins": style_votes["candidate"],
                "baseline_wins": style_votes["baseline"],
                "ties": style_votes["tie"],
                "candidate_preference_share": round(
                    (style_votes["candidate"] + 0.5 * style_votes["tie"]) / total,
                    4,
                ),
            },
            "quality": {
                "candidate_wins": quality_votes["candidate"],
                "baseline_wins": quality_votes["baseline"],
                "ties": quality_votes["tie"],
                "candidate_preference_share": round(
                    (quality_votes["candidate"] + 0.5 * quality_votes["tie"]) / total,
                    4,
                ),
                "no_more_than_10pp_drop_gate": (
                    (quality_votes["candidate"] + 0.5 * quality_votes["tie"]) / total
                    >= 0.4
                ),
            },
            "confidence_mean": _mean(confidences),
        }
        if baseline_arm == "D0":
            paired_row["style"]["d0_wins"] = style_votes["baseline"]
            paired_row["quality"]["d0_wins"] = quality_votes["baseline"]
        paired[candidate] = paired_row

    # Agreement is descriptive; it is not used to override raw votes.
    unanimous_samples = 0
    score_sds: list[float] = []
    for item in private["samples"]:
        rows = [
            row
            for review in reviews
            for row in review["samples"]
            if row["blind_id"] == item["blind_id"]
        ]
        if len({row["style_choice"] for row in rows}) == 1:
            unanimous_samples += 1
        for getter in (
            lambda row: row["s3_closeness"],
            *(
                lambda row, field=field: row["style_scores"][field]
                for field in STYLE_SCORE_FIELDS
            ),
            *(
                lambda row, field=field: row["quality_scores"][field]
                for field in QUALITY_SCORE_FIELDS
            ),
        ):
            score_sds.append(statistics.pstdev([getter(row) for row in rows]))

    unanimous_style_pairs = 0
    unanimous_quality_pairs = 0
    for item in private["pairs"]:
        rows = [
            row
            for review in reviews
            for row in review["pairs"]
            if row["pair_id"] == item["pair_id"]
        ]
        if len({row["closer_to_s3"] for row in rows}) == 1:
            unanimous_style_pairs += 1
        if len({row["better_quality"] for row in rows}) == 1:
            unanimous_quality_pairs += 1

    machine_rows = []
    cost_by_arm: dict[str, dict[str, list[float]]] = {
        arm: defaultdict(list) for arm in arms
    }
    for sample in plan["samples"]:
        result = _read_json(Path(sample["result_path"]))
        metrics = result["copy_safety_metrics"]
        metadata = result["metadata"]
        machine_rows.append(
            {
                "sample_id": sample["sample_id"],
                "arm": sample["arm"],
                "finish_reason": metadata["finish_reason"],
                "exact_sentence_copies": metrics["exact_copied_sentence_count"],
                "shared_12grams": metrics["shared_12gram_unique_count"],
                "longest_common_contiguous_chars": metrics[
                    "longest_common_contiguous_chars"
                ],
                "truncated": result["hard_gate_flags"]["truncated"],
            }
        )
        cost_by_arm[sample["arm"]]["input_tokens"].append(metadata["input_tokens"])
        cost_by_arm[sample["arm"]]["output_tokens"].append(metadata["output_tokens"])
        cost_by_arm[sample["arm"]]["latency_seconds"].append(metadata["latency_seconds"])
        prompt = _read_json(Path(sample["prompt_path"]))
        style_keys = {
            "style_signature",
            "scene_modulation",
            "positive_demonstrations",
            "negative_demonstrations",
            "negative_reasons",
            "action_style_bridge",
        }
        style_tokens = sum(
            value["estimated_tokens"]
            for key, value in prompt["component_telemetry"].items()
            if key in style_keys
        )
        demo_tokens = sum(
            value["estimated_tokens"]
            for key, value in prompt["component_telemetry"].items()
            if key
            in {"positive_demonstrations", "negative_demonstrations", "negative_reasons"}
        )
        cost_by_arm[sample["arm"]]["style_input_estimated_tokens"].append(style_tokens)
        cost_by_arm[sample["arm"]]["demonstration_estimated_tokens"].append(demo_tokens)

    costs = {}
    baseline_input = _mean(cost_by_arm[baseline_arm]["input_tokens"])
    for arm in arms:
        arm_input = _mean(cost_by_arm[arm]["input_tokens"])
        cost_row = {
            "average_input_tokens": arm_input,
            "average_output_tokens": _mean(cost_by_arm[arm]["output_tokens"]),
            "average_latency_seconds": _mean(cost_by_arm[arm]["latency_seconds"]),
            "average_style_input_estimated_tokens": _mean(
                cost_by_arm[arm]["style_input_estimated_tokens"]
            ),
            "average_demonstration_estimated_tokens": _mean(
                cost_by_arm[arm]["demonstration_estimated_tokens"]
            ),
            "actual_input_token_delta_vs_baseline": round(
                arm_input - baseline_input, 3
            ),
        }
        if baseline_arm == "D0":
            cost_row["actual_input_token_delta_vs_d0"] = round(
                arm_input - baseline_input, 3
            )
        costs[arm] = cost_row

    automatic_gates = {
        "exact_sentence_copy_total": sum(
            item["exact_sentence_copies"] for item in machine_rows
        ),
        "shared_12gram_total": sum(item["shared_12grams"] for item in machine_rows),
        "truncation_total": sum(item["truncated"] for item in machine_rows),
        "max_longest_common_contiguous_chars": max(
            item["longest_common_contiguous_chars"] for item in machine_rows
        ),
    }

    eligibility = {}
    for arm in arms:
        eligibility[arm] = {
            "s3_identification_at_least_70pct": (
                by_arm[arm]["s3_identification"]["rate"] >= 0.7
            ),
            "human_hard_gate_pass": by_arm[arm]["human_hard_gate_pass"],
            "automatic_copy_and_truncation_gate_pass": (
                automatic_gates["exact_sentence_copy_total"] == 0
                and automatic_gates["shared_12gram_total"] == 0
                and automatic_gates["truncation_total"] == 0
            ),
            "paired_quality_gate_pass": (
                True
                if arm == baseline_arm
                else paired[arm]["quality"]["no_more_than_10pp_drop_gate"]
            ),
        }
        eligibility[arm]["all_required_gates_pass"] = all(
            eligibility[arm].values()
        )

    leading_arm = max(
        arms,
        key=lambda arm: (
            by_arm[arm]["s3_identification"]["rate"],
            by_arm[arm]["s3_closeness_mean"],
            by_arm[arm]["quality_scores_mean"]["overall_reading_preference"],
        ),
    )
    eligible_candidates = [
        arm
        for arm in production_candidate_arms
        if eligibility[arm]["all_required_gates_pass"]
    ]
    decision = {
        "leading_arm": leading_arm,
        "eligible_production_candidate_arms": eligible_candidates,
        "interpretation": (
            f"{leading_arm} leads this batch descriptively. Eligibility is determined "
            "by target-style recognition, human hard gates, automatic copy/truncation "
            "gates, and paired quality."
        ),
        "next_minimal_step": (
            "Run the eligible candidate through cross-scene confirmation before any "
            "production integration."
            if eligible_candidates
            else "Retain the baseline and revise the candidate before another focused batch."
        ),
        "production_integration_approved": False,
    }
    if "F0" in arms:
        decision["f0_diagnostic"] = (
            "F0 remains diagnostic-only; interpret it as a test of few-shot without "
            "the style signature, not as a production candidate."
        )

    payload = {
        "schema_version": "2.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "validation": validation,
        "by_arm": by_arm,
        "paired_vs_baseline": paired,
        "pair_baseline_arm": baseline_arm,
        "agreement": {
            "sample_style_unanimous": unanimous_samples,
            "sample_count": len(private["samples"]),
            "sample_style_unanimous_rate": _rate(
                unanimous_samples, len(private["samples"])
            ),
            "mean_within_sample_numeric_score_sd": round(
                statistics.mean(score_sds), 3
            ),
            "pair_style_unanimous": unanimous_style_pairs,
            "pair_quality_unanimous": unanimous_quality_pairs,
            "pair_count": len(private["pairs"]),
            "pair_style_unanimous_rate": _rate(
                unanimous_style_pairs, len(private["pairs"])
            ),
            "pair_quality_unanimous_rate": _rate(
                unanimous_quality_pairs, len(private["pairs"])
            ),
        },
        "hard_error_evidence": hard_evidence,
        "automatic_gates": automatic_gates,
        "costs": costs,
        "eligibility": eligibility,
        "decision": decision,
        "sample_level_means": sample_mean_by_id,
    }
    if baseline_arm == "D0":
        payload["paired_vs_d0"] = paired
    _write_json(run_dir / "style-contract-ablation-human-review-aggregate.json", payload)
    return payload


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args()
    result = aggregate_reviews(args.run_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
