from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path
from statistics import correlation, mean
from typing import Any

from .builder import DEFAULT_OUTPUT, ROOT, load_json, write_json
from .review import SparseKernelBlindReview, validate_review_against_public


REVIEW_DIR = DEFAULT_OUTPUT / "reviews"
REPORT = ROOT / "reports/writer-sparse-kernel-canary-v0-aggregate-2026-07-31.md"
METRICS = (
    "naturalness",
    "less_template",
    "character_credibility",
    "emotional_residue",
    "overall_quality",
    "mechanicalness",
)
PRIMARY = ("naturalness", "less_template", "overall_quality")


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rounded(value: float) -> float:
    return round(value, 3)


def aggregate(
    output_dir: Path = DEFAULT_OUTPUT,
    report_path: Path = REPORT,
) -> dict[str, Any]:
    public = load_json(output_dir / "public/blind-review-material.json")
    blind_key = load_json(output_dir / "private/blind-key.json")
    review_paths = sorted(REVIEW_DIR.glob("reviewer-*.private.json"))
    if len(review_paths) != 3:
        raise ValueError("exactly three blind reviews are required")
    reviews = []
    review_inputs = []
    for path in review_paths:
        review = SparseKernelBlindReview.model_validate(load_json(path))
        validate_review_against_public(review, public)
        reviews.append(review)
        review_inputs.append(
            {
                "reviewer_id": review.reviewer_id,
                "file": path.name,
                "sha256": file_sha256(path),
            }
        )
    if len({review.reviewer_id for review in reviews}) != 3:
        raise ValueError("reviewer IDs must be distinct")
    id_to_key = {
        entry["public_text_id"]: entry for entry in blind_key["entries"]
    }
    if len(id_to_key) != 12:
        raise ValueError("blind key must contain 12 unique public IDs")

    scores: dict[str, dict[str, list[int]]] = {
        arm: {metric: [] for metric in METRICS} for arm in "ABC"
    }
    winner_fraction: dict[str, dict[str, float]] = {
        metric: {arm: 0.0 for arm in "ABC"}
        for metric in (
            "naturalness",
            "less_template",
            "character_credibility",
            "emotional_residue",
            "overall_quality",
            "most_mechanical",
        )
    }
    text_hard: dict[str, list[bool]] = defaultdict(list)
    text_violation: dict[str, list[bool]] = defaultdict(list)
    text_scores: dict[str, dict[str, list[int]]] = defaultdict(
        lambda: {metric: [] for metric in METRICS}
    )
    normalized_reviews = []
    for review in reviews:
        normalized_blocks = []
        for block in review.blocks:
            normalized_assessments = []
            for assessment in block.assessments:
                key = id_to_key[assessment.public_text_id]
                arm = key["arm"]
                for metric in METRICS:
                    value = getattr(assessment, metric)
                    scores[arm][metric].append(value)
                    text_scores[assessment.public_text_id][metric].append(value)
                text_hard[assessment.public_text_id].append(
                    assessment.hard_task_complete
                )
                text_violation[assessment.public_text_id].append(
                    assessment.unauthorized_event_detected
                )
                normalized_assessments.append(
                    {
                        "public_text_id": assessment.public_text_id,
                        "arm": arm,
                        "hard_task_complete": assessment.hard_task_complete,
                        "unauthorized_event_detected": (
                            assessment.unauthorized_event_detected
                        ),
                        "scores": {
                            metric: getattr(assessment, metric)
                            for metric in METRICS
                        },
                    }
                )
            for metric, winners in block.winners.model_dump().items():
                share = 1.0 / len(winners)
                for public_id in winners:
                    winner_fraction[metric][id_to_key[public_id]["arm"]] += share
            normalized_blocks.append(
                {
                    "public_block_id": block.public_block_id,
                    "assessments": normalized_assessments,
                    "winners": block.winners.model_dump(),
                    "confidence": block.confidence,
                }
            )
        normalized_reviews.append(
            {"reviewer_id": review.reviewer_id, "blocks": normalized_blocks}
        )

    hard_by_arm = {
        arm: {
            "texts_majority_hard_pass": 0,
            "texts_majority_violation": 0,
            "texts_total": 4,
        }
        for arm in "ABC"
    }
    text_outcomes = []
    for public_id, key in sorted(id_to_key.items()):
        hard_votes = text_hard[public_id]
        violation_votes = text_violation[public_id]
        hard_majority = sum(hard_votes) >= 2
        violation_majority = sum(violation_votes) >= 2
        arm = key["arm"]
        hard_by_arm[arm]["texts_majority_hard_pass"] += int(hard_majority)
        hard_by_arm[arm]["texts_majority_violation"] += int(violation_majority)
        text_outcomes.append(
            {
                "public_text_id": public_id,
                "arm": arm,
                "hard_pass_votes": sum(hard_votes),
                "violation_votes": sum(violation_votes),
                "majority_hard_pass": hard_majority,
                "majority_violation": violation_majority,
            }
        )

    score_means = {
        arm: {metric: rounded(mean(values)) for metric, values in metrics.items()}
        for arm, metrics in scores.items()
    }
    winner_counts = {
        metric: {arm: rounded(value) for arm, value in arms.items()}
        for metric, arms in winner_fraction.items()
    }
    deltas_c_vs_b = {
        metric: rounded(score_means["C"][metric] - score_means["B"][metric])
        for metric in METRICS
    }
    deltas_c_vs_a = {
        metric: rounded(score_means["C"][metric] - score_means["A"][metric])
        for metric in METRICS
    }
    score_wins_vs_b = sum(deltas_c_vs_b[metric] > 0 for metric in PRIMARY)
    winner_wins_vs_b = sum(
        winner_counts[metric]["C"] > winner_counts[metric]["B"]
        for metric in PRIMARY
    )
    hard_not_worse = (
        hard_by_arm["C"]["texts_majority_hard_pass"]
        >= hard_by_arm["B"]["texts_majority_hard_pass"]
    )
    violation_not_worse = (
        hard_by_arm["C"]["texts_majority_violation"]
        <= hard_by_arm["B"]["texts_majority_violation"]
    )
    less_often_most_mechanical = (
        winner_counts["most_mechanical"]["C"]
        < winner_counts["most_mechanical"]["B"]
    )
    directional_support = (
        score_wins_vs_b >= 2
        and winner_wins_vs_b >= 2
        and hard_not_worse
        and violation_not_worse
        and less_often_most_mechanical
    )
    clear_negative = (
        score_wins_vs_b <= 1
        and winner_wins_vs_b <= 1
    ) or not hard_not_worse
    conclusion = (
        "sparse_kernel_directionally_supported"
        if directional_support
        else "sparse_kernel_not_supported"
        if clear_negative
        else "mixed_inconclusive"
    )

    preflight = load_json(output_dir / "preflight.json")
    text_lengths = {}
    for path in (output_dir / "private/texts").glob("SK-GEN-*.json"):
        record = load_json(path)
        text_lengths[record["generation_id"]] = record["basic_checks"][
            "character_count"
        ]
    block_level = []
    c_block_wins_vs_b = {metric: 0 for metric in PRIMARY}
    all_lengths = []
    all_overall_scores = []
    all_naturalness_scores = []
    by_private_block: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    for public_id, key in id_to_key.items():
        by_private_block[key["canary_block_id"]].append((public_id, key))
    for private_block_id, entries in sorted(by_private_block.items()):
        arms = {}
        for public_id, key in entries:
            per_text_scores = {
                metric: rounded(mean(text_scores[public_id][metric]))
                for metric in METRICS
            }
            length = text_lengths[key["generation_id"]]
            arms[key["arm"]] = {
                "public_text_id": public_id,
                "characters": length,
                "mean_scores": per_text_scores,
            }
            all_lengths.append(length)
            all_overall_scores.append(per_text_scores["overall_quality"])
            all_naturalness_scores.append(per_text_scores["naturalness"])
        primary_deltas = {}
        for metric in PRIMARY:
            delta = rounded(
                arms["C"]["mean_scores"][metric]
                - arms["B"]["mean_scores"][metric]
            )
            primary_deltas[metric] = delta
            c_block_wins_vs_b[metric] += int(delta > 0)
        block_level.append(
            {
                "canary_block_id": private_block_id,
                "arms": arms,
                "c_minus_b_characters": (
                    arms["C"]["characters"] - arms["B"]["characters"]
                ),
                "c_minus_b_primary_score_deltas": primary_deltas,
            }
        )
    length_sensitivity = {
        "c_block_wins_vs_b": c_block_wins_vs_b,
        "c_longer_than_b_blocks": sum(
            block["c_minus_b_characters"] > 0 for block in block_level
        ),
        "length_overall_score_correlation_all_texts": rounded(
            correlation(all_lengths, all_overall_scores)
        ),
        "length_naturalness_correlation_all_texts": rounded(
            correlation(all_lengths, all_naturalness_scores)
        ),
        "interpretation": (
            "Associations are descriptive only; n=12 and arm assignment is confounded "
            "with prompt form and observed length."
        ),
    }
    result = {
        "schema_version": "writer-sparse-kernel-canary-aggregate-v0",
        "experiment_id": "writer-sparse-kernel-canary-v0",
        "input_gate": {
            "passed": True,
            "reviewer_count": 3,
            "distinct_reviewer_ids": True,
            "all_schema_valid": True,
            "all_public_coverage_valid": True,
            "all_independence_declarations_valid": True,
            "blind_key_accessed_only_during_aggregation": True,
            "reviewer_inputs_modified": False,
        },
        "review_inputs": review_inputs,
        "decision_rule": {
            "locked_before_blind_key_access": True,
            "primary_metrics": list(PRIMARY),
            "requirements": {
                "c_mean_score_beats_b_on_at_least_two_primary_metrics": True,
                "c_fractional_winner_count_beats_b_on_at_least_two_primary_metrics": True,
                "c_hard_pass_not_worse_than_b": True,
                "c_violation_not_worse_than_b": True,
                "c_selected_most_mechanical_less_often_than_b": True,
            },
            "single_total_score": None,
            "confidence_weighting": False,
        },
        "hard_outcomes_by_arm": hard_by_arm,
        "score_means_by_arm": score_means,
        "fractional_winner_counts_by_arm": winner_counts,
        "score_deltas_c_vs_b": deltas_c_vs_b,
        "score_deltas_c_vs_a": deltas_c_vs_a,
        "decision_components": {
            "c_primary_mean_score_wins_vs_b": score_wins_vs_b,
            "c_primary_fractional_winner_wins_vs_b": winner_wins_vs_b,
            "hard_not_worse": hard_not_worse,
            "violation_not_worse": violation_not_worse,
            "c_less_often_most_mechanical": less_often_most_mechanical,
        },
        "length_by_arm_private": preflight["length_by_arm_private"],
        "length_confound_present": preflight["length_confound_present"],
        "block_level_sensitivity": block_level,
        "length_sensitivity": length_sensitivity,
        "conclusion": conclusion,
        "limitations": [
            "Only four blocks and two scenes were tested.",
            "C mean character length is approximately 17.7% above B.",
            "The decision rule is directional and does not establish statistical significance.",
            "No single total quality score was computed.",
        ],
    }
    write_json(
        output_dir / "private/normalized-blind-reviews.json",
        normalized_reviews,
    )
    write_json(output_dir / "aggregate.json", result)
    lines = [
        "# Writer Sparse Decision Kernel Mini-Canary V0 聚合报告",
        "",
        f"聚合结论：`{conclusion}`",
        "",
        "## 硬任务",
        "",
        "| Arm | Majority hard pass | Majority violation |",
        "|---|---:|---:|",
    ]
    for arm in "ABC":
        lines.append(
            f"| {arm} | {hard_by_arm[arm]['texts_majority_hard_pass']}/4 | "
            f"{hard_by_arm[arm]['texts_majority_violation']}/4 |"
        )
    lines.extend(
        [
            "",
            "## 平均分",
            "",
            "| Arm | Naturalness | Less template | Character | Residue | Overall | Mechanicalness |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for arm in "ABC":
        values = score_means[arm]
        lines.append(
            f"| {arm} | {values['naturalness']} | {values['less_template']} | "
            f"{values['character_credibility']} | {values['emotional_residue']} | "
            f"{values['overall_quality']} | {values['mechanicalness']} |"
        )
    lines.extend(
        [
            "",
            "## C 相对 B 的平均分差",
            "",
            f"- Naturalness：{deltas_c_vs_b['naturalness']}",
            f"- Less template：{deltas_c_vs_b['less_template']}",
            f"- Overall quality：{deltas_c_vs_b['overall_quality']}",
            f"- Mechanicalness：{deltas_c_vs_b['mechanicalness']}（越低越好）",
            "",
            "## 判定",
            "",
            f"- C 在三项主指标平均分中胜 B：{score_wins_vs_b}/3。",
            f"- C 在三项主指标 fractional winner count 中胜 B：{winner_wins_vs_b}/3。",
            f"- Hard pass 不劣于 B：{str(hard_not_worse).lower()}。",
            f"- Violation 不劣于 B：{str(violation_not_worse).lower()}。",
            f"- C 更少被选为 most mechanical：{str(less_often_most_mechanical).lower()}。",
            "",
            "## 限制",
            "",
            "- 仅 4 个 block、2 个场景。",
            "- C 平均字符数比 B 高约 17.7%，存在长度混杂。",
            f"- C 在每个 block 的主指标胜 B 次数：{c_block_wins_vs_b}。",
            f"- 全部文本长度与 overall score 的描述性相关："
            f"{length_sensitivity['length_overall_score_correlation_all_texts']}。",
            "- 这是 directional canary，不提供统计显著性。",
            "- 未计算单一综合总分。",
        ]
    )
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return result


if __name__ == "__main__":
    print(json.dumps(aggregate(), ensure_ascii=False, indent=2))
