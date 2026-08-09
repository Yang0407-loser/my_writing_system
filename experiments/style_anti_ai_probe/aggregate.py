from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean
from typing import Any

from .builder import DEFAULT_OUTPUT, ROOT, load_json, write_json
from .review import AntiAIBlindReview, validate_review_against_public


DEFAULT_REPORT = ROOT / "reports/style-anti-ai-probe-v0-aggregate-2026-08-02.md"
POSITIVE = ("commercial_momentum", "character_motivation_credibility", "specificity", "naturalness")
NEGATIVE = ("redundant_explanation", "formulaic_expression", "summary_closure", "prompt_structure_leak", "overall_ai_taste")
METRICS = POSITIVE + NEGATIVE
PAIR_FIELDS = ("better_commercial_execution", "lower_ai_taste", "better_overall")


def rounded(value: float) -> float:
    return round(value, 3)


def load_reviews(output_dir: Path) -> list[AntiAIBlindReview]:
    public = load_json(output_dir / "public/blind-review-material.json")
    paths = sorted((output_dir / "reviews").glob("reviewer-*.json"))
    if len(paths) != 3:
        raise ValueError("aggregate requires exactly three reviews")
    reviews = [AntiAIBlindReview.model_validate(load_json(path)) for path in paths]
    if len({item.reviewer_id for item in reviews}) != 3:
        raise ValueError("reviewer IDs must be unique")
    for review in reviews:
        validate_review_against_public(review, public)
    return reviews


def aggregate(output_dir: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    blind = load_json(output_dir / "private/blind-key.json")
    key = {item["public_text_id"]: item for item in blind["entries"]}
    reviews = load_reviews(output_dir)
    scores: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    hard: dict[str, list[bool]] = defaultdict(list)
    violations: dict[str, list[bool]] = defaultdict(list)
    pair_votes: dict[str, dict[str, Counter[str]]] = defaultdict(lambda: defaultdict(Counter))
    for review in reviews:
        for block in review.blocks:
            for assessment in block.assessments:
                payload = assessment.model_dump()
                public_id = assessment.public_text_id
                for metric in METRICS:
                    scores[public_id][metric].append(payload[metric])
                hard[public_id].append(assessment.hard_task_complete)
                violations[public_id].append(assessment.unauthorized_event_detected)
            for field in PAIR_FIELDS:
                winners = getattr(block, field)
                share = 1 / len(winners)
                for public_id in winners:
                    pair_votes[block.public_block_id][field][key[public_id]["arm"]] += share

    arm_scores: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    arm_hard: dict[str, list[bool]] = defaultdict(list)
    arm_violation: dict[str, list[bool]] = defaultdict(list)
    text_results = []
    for public_id, meta in sorted(key.items()):
        metrics = {name: rounded(mean(scores[public_id][name])) for name in METRICS}
        hard_pass = sum(hard[public_id]) >= 2
        violation = sum(violations[public_id]) >= 2
        for name, value in metrics.items():
            arm_scores[meta["arm"]][name].append(value)
        arm_hard[meta["arm"]].append(hard_pass)
        arm_violation[meta["arm"]].append(violation)
        text_results.append({"public_text_id": public_id, "arm": meta["arm"], "scene_id": meta["scene_id"], "repeat": meta["repeat"], "metric_means": metrics, "majority_hard_pass": hard_pass, "majority_violation": violation})
    arm_results = {}
    for arm in ("W", "WA"):
        arm_results[arm] = {
            "metric_means": {name: rounded(mean(values)) for name, values in arm_scores[arm].items()},
            "majority_hard_pass": sum(arm_hard[arm]),
            "majority_unauthorized_event": sum(arm_violation[arm]),
            "texts": len(arm_hard[arm]),
        }

    pair_results, scene_shares = [], defaultdict(list)
    for block_id, fields in sorted(pair_votes.items()):
        private_block = f"AA-BLOCK-{block_id.split('-')[-1]}"
        scene_id = next(item["scene_id"] for item in blind["entries"] if item["block_id"] == private_block)
        record = {"public_block_id": block_id, "scene_id": scene_id, "shares": {}}
        for field in PAIR_FIELDS:
            total = sum(fields[field].values()) or 1
            shares = {arm: rounded(value / total) for arm, value in fields[field].items()}
            record["shares"][field] = shares
            scene_shares[(scene_id, field)].append(shares.get("WA", 0.0))
        pair_results.append(record)

    pair_summary = {
        field: rounded(mean(item["shares"][field].get("WA", 0.0) for item in pair_results))
        for field in PAIR_FIELDS
    }
    scene_direction = {
        scene: {
            "WA_lower_ai_share": rounded(mean(scene_shares[(scene, "lower_ai_taste")])),
            "WA_commercial_share": rounded(mean(scene_shares[(scene, "better_commercial_execution")])),
        }
        for scene in sorted({item["scene_id"] for item in blind["entries"]})
    }
    w, wa = arm_results["W"], arm_results["WA"]
    gates = {
        "lower_ai_pair_share_at_least_65pct": pair_summary["lower_ai_taste"] >= 0.65,
        "commercial_noninferiority": pair_summary["better_commercial_execution"] >= 0.5 and wa["metric_means"]["commercial_momentum"] >= w["metric_means"]["commercial_momentum"] - 0.25,
        "surface_metrics_improve": wa["metric_means"]["redundant_explanation"] < w["metric_means"]["redundant_explanation"] and wa["metric_means"]["formulaic_expression"] < w["metric_means"]["formulaic_expression"],
        "motivation_noninferiority": wa["metric_means"]["character_motivation_credibility"] >= w["metric_means"]["character_motivation_credibility"] - 0.25,
        "hard_safety": wa["majority_hard_pass"] == 4 and wa["majority_unauthorized_event"] == 0,
        "both_scenes_lower_ai_nonnegative": all(item["WA_lower_ai_share"] >= 0.5 for item in scene_direction.values()),
    }
    if not gates["hard_safety"]:
        decision = "anti_ai_surface_unsafe"
    elif gates["lower_ai_pair_share_at_least_65pct"] and not gates["commercial_noninferiority"]:
        decision = "ai_reduced_but_momentum_harmed"
    elif all(gates.values()):
        decision = "anti_ai_surface_supported"
    else:
        decision = "anti_ai_surface_not_supported"
    result = {
        "schema_version": "style-anti-ai-aggregate-v0", "reviewers": [r.reviewer_id for r in reviews],
        "arm_results": arm_results, "pair_summary_WA_share": pair_summary,
        "scene_direction": scene_direction, "gates": gates, "decision": decision,
        "limitations": ["directional probe with two unseen scenes", "length is reported as a possible confound", "no statistical significance claim"],
        "text_results": text_results, "pair_results": pair_results,
    }
    write_json(output_dir / "aggregate.json", result)
    return result


def write_report(result: dict[str, Any], path: Path = DEFAULT_REPORT) -> None:
    lines = ["# Style Anti-AI Surface Mini-Probe V0 聚合报告", "", f"结论：`{result['decision']}`", "", "## 两臂结果", "", "| Arm | 商业动量 | 人物动机 | 具体性 | 自然度 | 重复解释 | 模板化 | 总结收尾 | AI味 | 硬任务 | 越界 |", "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for arm in ("W", "WA"):
        item, m = result["arm_results"][arm], result["arm_results"][arm]["metric_means"]
        lines.append(f"| {arm} | {m['commercial_momentum']:.3f} | {m['character_motivation_credibility']:.3f} | {m['specificity']:.3f} | {m['naturalness']:.3f} | {m['redundant_explanation']:.3f} | {m['formulaic_expression']:.3f} | {m['summary_closure']:.3f} | {m['overall_ai_taste']:.3f} | {item['majority_hard_pass']}/4 | {item['majority_unauthorized_event']}/4 |")
    lines.extend(["", "## WA 配对份额", ""])
    for name, value in result["pair_summary_WA_share"].items(): lines.append(f"- {name}: {value:.1%}")
    lines.extend(["", "## Gates", ""])
    for name, value in result["gates"].items(): lines.append(f"- {name}: `{str(value).lower()}`")
    lines.extend(["", "本轮仅作方向性验证，不提供统计显著性。", ""])
    path.write_text("\n".join(lines), encoding="utf-8", newline="\n")


if __name__ == "__main__":
    result = aggregate()
    write_report(result)
    print(json.dumps({"decision": result["decision"]}, ensure_ascii=False, indent=2))
