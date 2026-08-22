from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean
from typing import Any

from .builder import DEFAULT_OUTPUT, ROOT, load_json, write_json
from .review import RootCauseBlindReview, validate_review_against_public


DEFAULT_AGGREGATE = DEFAULT_OUTPUT / "aggregate.json"
DEFAULT_REPORT = ROOT / "reports/style-root-cause-probe-v0-aggregate-2026-08-01.md"
POSITIVE_METRICS = (
    "literary_intentionality",
    "commercial_momentum",
    "narrative_intentionality",
    "character_motivation_credibility",
)
NEGATIVE_METRICS = (
    "redundant_explanation",
    "formulaic_expression",
    "prompt_structure_leak",
    "overall_ai_taste",
)
ALL_METRICS = POSITIVE_METRICS + NEGATIVE_METRICS
EXPECTED_MODE = {
    "G": "generic_or_unclear",
    "L": "traditional_literary",
    "W": "commercial_web_fiction",
}


def rounded(value: float) -> float:
    return round(value, 3)


def majority(values: list[bool]) -> bool:
    return sum(values) >= 2


def load_reviews(output_dir: Path) -> list[RootCauseBlindReview]:
    public = load_json(output_dir / "public/blind-review-material.json")
    paths = sorted((output_dir / "reviews").glob("reviewer-*.json"))
    if len(paths) != 3:
        raise ValueError("aggregate requires exactly three review files")
    reviews = [RootCauseBlindReview.model_validate(load_json(path)) for path in paths]
    if len({item.reviewer_id for item in reviews}) != 3:
        raise ValueError("reviewer IDs must be unique")
    for review in reviews:
        validate_review_against_public(review, public)
    return reviews


def aggregate(output_dir: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    public = load_json(output_dir / "public/blind-review-material.json")
    blind_key = load_json(output_dir / "private/blind-key.json")
    reviews = load_reviews(output_dir)
    key_by_public = {entry["public_text_id"]: entry for entry in blind_key["entries"]}
    scores: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    classifications: dict[str, list[str]] = defaultdict(list)
    hard_votes: dict[str, list[bool]] = defaultdict(list)
    violation_votes: dict[str, list[bool]] = defaultdict(list)
    pair_votes: dict[str, dict[str, Counter[str]]] = defaultdict(
        lambda: {"target_mode": Counter(), "lower_ai_taste": Counter()}
    )

    for review in reviews:
        for block in review.blocks:
            for assessment in block.assessments:
                public_id = assessment.public_text_id
                classifications[public_id].append(assessment.mode_classification)
                hard_votes[public_id].append(assessment.hard_task_complete)
                violation_votes[public_id].append(assessment.unauthorized_event_detected)
                payload = assessment.model_dump()
                for metric in ALL_METRICS:
                    scores[public_id][metric].append(payload[metric])
        for pair in review.pairs:
            target_share = 1.0 / len(pair.target_mode_winners)
            ai_share = 1.0 / len(pair.lower_ai_taste_winners)
            for public_id in pair.target_mode_winners:
                pair_votes[pair.public_pair_id]["target_mode"][public_id] += target_share
            for public_id in pair.lower_ai_taste_winners:
                pair_votes[pair.public_pair_id]["lower_ai_taste"][public_id] += ai_share

    text_results = []
    by_arm_scores: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    arm_correct_votes: dict[str, int] = Counter()
    arm_total_votes: dict[str, int] = Counter()
    arm_hard: dict[str, list[bool]] = defaultdict(list)
    arm_violations: dict[str, list[bool]] = defaultdict(list)
    for public_id, key in sorted(key_by_public.items()):
        arm = key["arm"]
        classification_count = Counter(classifications[public_id])
        expected = EXPECTED_MODE[arm]
        correct_votes = classification_count[expected]
        arm_correct_votes[arm] += correct_votes
        arm_total_votes[arm] += len(classifications[public_id])
        metric_means = {metric: rounded(mean(scores[public_id][metric])) for metric in ALL_METRICS}
        for metric, value in metric_means.items():
            by_arm_scores[arm][metric].append(value)
        hard_pass = majority(hard_votes[public_id])
        violation = majority(violation_votes[public_id])
        arm_hard[arm].append(hard_pass)
        arm_violations[arm].append(violation)
        text_results.append(
            {
                "public_text_id": public_id,
                "scene_id": key["scene_id"],
                "repeat": key["repeat"],
                "arm": arm,
                "classification_votes": dict(classification_count),
                "expected_mode_votes": correct_votes,
                "metric_means": metric_means,
                "majority_hard_task_complete": hard_pass,
                "majority_unauthorized_event": violation,
            }
        )

    arm_results = {}
    for arm in "GLW":
        arm_results[arm] = {
            "expected_mode_vote_rate": rounded(arm_correct_votes[arm] / arm_total_votes[arm]),
            "metric_means": {
                metric: rounded(mean(values))
                for metric, values in by_arm_scores[arm].items()
            },
            "majority_hard_pass": sum(arm_hard[arm]),
            "majority_unauthorized_event": sum(arm_violations[arm]),
            "texts": len(arm_hard[arm]),
        }

    pair_results = []
    pair_arm_fraction: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    pair_lookup = {pair["public_pair_id"]: pair for pair in blind_key["pairs"]}
    for pair_id, key in sorted(pair_lookup.items()):
        candidate_arm = {public_id: key_by_public[public_id]["arm"] for public_id in key["candidate_ids"]}
        vote_record = {"public_pair_id": pair_id, "pair_type": key["pair_type"], "votes": {}}
        for vote_type in ("target_mode", "lower_ai_taste"):
            by_arm = Counter()
            for public_id, value in pair_votes[pair_id][vote_type].items():
                by_arm[candidate_arm[public_id]] += value
            total = sum(by_arm.values()) or 1.0
            fractions = {arm: rounded(value / total) for arm, value in by_arm.items()}
            vote_record["votes"][vote_type] = fractions
            for arm, value in fractions.items():
                pair_arm_fraction[key["pair_type"]][f"{vote_type}:{arm}"].append(value)
        pair_results.append(vote_record)

    def pair_mean(pair_type: str, vote_type: str, arm: str) -> float:
        values = pair_arm_fraction[pair_type].get(f"{vote_type}:{arm}", [])
        return rounded(mean(values)) if values else 0.0

    separation = all(arm_results[arm]["expected_mode_vote_rate"] > 0.5 for arm in "GLW")
    w_better = (
        pair_mean("web_fiction", "target_mode", "W") > 0.5
        and arm_results["W"]["metric_means"]["commercial_momentum"] > arm_results["G"]["metric_means"]["commercial_momentum"]
        and arm_results["W"]["majority_hard_pass"] >= arm_results["G"]["majority_hard_pass"]
    )
    l_better = (
        pair_mean("literary", "target_mode", "L") > 0.5
        and arm_results["L"]["metric_means"]["literary_intentionality"] > arm_results["G"]["metric_means"]["literary_intentionality"]
        and arm_results["L"]["majority_hard_pass"] >= arm_results["G"]["majority_hard_pass"]
    )
    common_ai_reduction = all(
        arm_results[arm]["metric_means"]["overall_ai_taste"] < arm_results["G"]["metric_means"]["overall_ai_taste"]
        or (
            arm_results[arm]["metric_means"]["character_motivation_credibility"] > arm_results["G"]["metric_means"]["character_motivation_credibility"]
            and arm_results[arm]["metric_means"]["prompt_structure_leak"] <= arm_results["G"]["metric_means"]["prompt_structure_leak"]
        )
        for arm in ("L", "W")
    )
    scene_direction = {}
    for scene_id in sorted({item["scene_id"] for item in key_by_public.values()}):
        relevant = [item for item in pair_results if key_by_public[pair_lookup[item["public_pair_id"]]["candidate_ids"][0]]["scene_id"] == scene_id]
        scene_direction[scene_id] = {
            "literary_target_share": rounded(mean(item["votes"]["target_mode"].get("L", 0.0) for item in relevant if item["pair_type"] == "literary")),
            "web_target_share": rounded(mean(item["votes"]["target_mode"].get("W", 0.0) for item in relevant if item["pair_type"] == "web_fiction")),
        }
    both_scenes_nonnegative = all(
        value["literary_target_share"] >= 0.5 and value["web_target_share"] >= 0.5
        for value in scene_direction.values()
    )

    if separation and w_better and l_better and common_ai_reduction and both_scenes_nonnegative:
        decision = "type_separation_supported"
    elif w_better and not l_better:
        decision = "commercial_only_supported"
    elif separation and w_better and l_better and not common_ai_reduction:
        decision = "type_separation_without_ai_reduction"
    else:
        decision = "type_separation_not_supported"

    result = {
        "schema_version": "style-root-cause-aggregate-v0",
        "reviewers": [item.reviewer_id for item in reviews],
        "arm_results": arm_results,
        "pair_summary": {
            "literary_L_target_share": pair_mean("literary", "target_mode", "L"),
            "literary_L_lower_ai_share": pair_mean("literary", "lower_ai_taste", "L"),
            "web_W_target_share": pair_mean("web_fiction", "target_mode", "W"),
            "web_W_lower_ai_share": pair_mean("web_fiction", "lower_ai_taste", "W"),
        },
        "scene_direction": scene_direction,
        "gates": {
            "mode_separation": separation,
            "literary_better_than_generic": l_better,
            "web_better_than_generic": w_better,
            "common_ai_reduction": common_ai_reduction,
            "both_scenes_nonnegative": both_scenes_nonnegative,
        },
        "decision": decision,
        "limitations": [
            "directional probe with two scenes and no significance claim",
            "review scores are ordinal and are not merged into one total score",
            "length must be inspected as a possible mediator or confound",
        ],
        "text_results": text_results,
        "pair_results": pair_results,
    }
    write_json(DEFAULT_AGGREGATE if output_dir == DEFAULT_OUTPUT else output_dir / "aggregate.json", result)
    return result


def write_report(result: dict[str, Any], path: Path = DEFAULT_REPORT) -> None:
    arms = result["arm_results"]
    lines = [
        "# Style Root Cause Probe V0 聚合报告",
        "",
        f"结论：`{result['decision']}`",
        "",
        "## 类型识别与任务完成",
        "",
        "| Arm | 预期类型票率 | 硬任务通过 | 越界事件 | 文学意图 | 商业动量 | AI味严重度 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for arm in "GLW":
        item = arms[arm]
        metrics = item["metric_means"]
        lines.append(
            f"| {arm} | {item['expected_mode_vote_rate']:.1%} | "
            f"{item['majority_hard_pass']}/{item['texts']} | {item['majority_unauthorized_event']}/{item['texts']} | "
            f"{metrics['literary_intentionality']:.3f} | {metrics['commercial_momentum']:.3f} | {metrics['overall_ai_taste']:.3f} |"
        )
    lines.extend(
        [
            "",
            "## 配对份额",
            "",
            f"- L 相对 G 的文学目标胜选份额：{result['pair_summary']['literary_L_target_share']:.1%}",
            f"- L 相对 G 的低 AI 味胜选份额：{result['pair_summary']['literary_L_lower_ai_share']:.1%}",
            f"- W 相对 G 的商业目标胜选份额：{result['pair_summary']['web_W_target_share']:.1%}",
            f"- W 相对 G 的低 AI 味胜选份额：{result['pair_summary']['web_W_lower_ai_share']:.1%}",
            "",
            "## 判定门",
            "",
        ]
    )
    for gate, passed in result["gates"].items():
        lines.append(f"- {gate}: `{str(passed).lower()}`")
    lines.extend(
        [
            "",
            "## 限制",
            "",
            "本轮只有两个场景，是根因分流实验，不提供统计显著性。长度必须作为潜在混杂单独检查。",
            "",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8", newline="\n")


if __name__ == "__main__":
    aggregate_result = aggregate()
    write_report(aggregate_result)
    print(json.dumps({"decision": aggregate_result["decision"]}, ensure_ascii=False, indent=2))
