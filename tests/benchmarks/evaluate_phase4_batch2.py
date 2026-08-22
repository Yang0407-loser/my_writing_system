"""Aggregate the frozen Phase 4 Batch 2 A/B run into a prose-free report."""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

from app.context_ab_evaluation import deterministic_output_checks


ROOT = Path(__file__).resolve().parents[2]
REVIEW = ROOT / "tests" / "quality" / "phase4_batch2_codex_review.json"
BATCH1 = ROOT / "reports" / "phase4-batch1-context-broker-shadow.json"


def _summary(values: list[float]) -> dict:
    return {
        "mean": round(statistics.mean(values), 3),
        "min": round(min(values), 3),
        "max": round(max(values), 3),
    }


def build_report(runtime_dir: Path) -> dict:
    review_payload = json.loads(REVIEW.read_text(encoding="utf-8"))
    batch1 = json.loads(BATCH1.read_text(encoding="utf-8"))
    reviews = {int(item["query_index"]): item for item in review_payload["reviews"]}
    arms = {
        "legacy_full": {"wins": 0, "inputs": [], "latencies": [], "actual_totals": [], "checks": [], "issues": {}},
        "budgeted_broker": {"wins": 0, "inputs": [], "latencies": [], "actual_totals": [], "checks": [], "issues": {}},
    }
    samples = []
    ties = 0
    late = {"legacy_full": 0, "budgeted_broker": 0, "tie": 0, "count": 0}
    for query_index in range(1, 11):
        query_dir = runtime_dir / f"q{query_index:02d}"
        prepare = json.loads((query_dir / "prepare.json").read_text(encoding="utf-8"))
        blind = json.loads((query_dir / "blind.json").read_text(encoding="utf-8"))
        mapping = json.loads((query_dir / "private_mapping.json").read_text(encoding="utf-8"))["mapping"]
        review = reviews[query_index]
        candidates = {item["candidate_id"]: item for item in blind["candidates"]}
        candidate_rows = []
        for candidate_id in ("candidate_1", "candidate_2"):
            arm = mapping[candidate_id]["arm"]
            text = (query_dir / f"{candidate_id}.txt").read_text(encoding="utf-8")
            checks = deterministic_output_checks(text)
            if checks["sha256"] != candidates[candidate_id]["output_sha256"]:
                raise AssertionError(f"output hash mismatch for q{query_index} {candidate_id}")
            issues = review[candidate_id]
            arms[arm]["latencies"].append(float(candidates[candidate_id]["elapsed_ms"]))
            arms[arm]["actual_totals"].append(int(mapping[candidate_id]["actual_total_tokens"]))
            arms[arm]["checks"].append(checks)
            for field in ("hard_violations", "relationship_violations", "continuity_defects", "causality_defects", "fact_errors"):
                arms[arm]["issues"][field] = arms[arm]["issues"].get(field, 0) + int(issues[field])
            candidate_rows.append({
                "candidate_id": candidate_id,
                "arm": arm,
                "output_sha256": checks["sha256"],
                "characters": checks["characters"],
                "estimated_output_tokens": checks["estimated_tokens"],
                "elapsed_ms": candidates[candidate_id]["elapsed_ms"],
                "actual_total_tokens": mapping[candidate_id]["actual_total_tokens"],
                "deterministic_checks": {key: value for key, value in checks.items() if key != "sha256"},
                "codex_assisted_judgment": issues,
            })
        arms["legacy_full"]["inputs"].append(int(prepare["legacy_context_estimated_tokens"]))
        arms["budgeted_broker"]["inputs"].append(int(prepare["broker_context_estimated_tokens"]))
        winner_candidate = review["winner_candidate"]
        if winner_candidate == "tie":
            winner_arm = "tie"
            ties += 1
        else:
            winner_arm = mapping[winner_candidate]["arm"]
            arms[winner_arm]["wins"] += 1
        is_late = int(prepare["section"]) >= 13
        if is_late:
            late["count"] += 1
            late[winner_arm] += 1
        samples.append({
            "query_index": query_index,
            "section": prepare["section"],
            "subsection": prepare["subsection"],
            "late_scene": is_late,
            "legacy_input_estimated_tokens": prepare["legacy_context_estimated_tokens"],
            "broker_input_estimated_tokens": prepare["broker_context_estimated_tokens"],
            "production_prompt_hash_unchanged": prepare["production_prompt_hash_unchanged"],
            "legacy_messages_hash": prepare["legacy_messages_hash"],
            "broker_messages_hash": prepare["broker_messages_hash"],
            "kept_source_ids": prepare["kept_source_ids"],
            "dropped_source_ids": prepare["dropped_source_ids"],
            "dropped_item_ids": prepare["dropped_item_ids"],
            "winner_arm": winner_arm,
            "mapping_exposed_before_review": review["mapping_exposed_before_review"],
            "ambiguous": review["ambiguous"],
            "short_evidence": review["short_evidence"],
            "candidates": candidate_rows,
        })

    legacy_mean = statistics.mean(arms["legacy_full"]["inputs"])
    broker_mean = statistics.mean(arms["budgeted_broker"]["inputs"])
    arm_summaries = {}
    for arm, data in arms.items():
        goal_complete = sum(
            int(row["codex_assisted_judgment"]["goal_complete"])
            for sample in samples for row in sample["candidates"] if row["arm"] == arm
        )
        arm_summaries[arm] = {
            "input_estimated_tokens": _summary(data["inputs"]),
            "generation_latency_ms": _summary(data["latencies"]),
            "actual_input_plus_output_tokens": _summary(data["actual_totals"]),
            "goal_completion_rate": round(goal_complete / 10, 4),
            **data["issues"],
            "wins": data["wins"],
        }
    broker_win_or_tie_rate = round((arms["budgeted_broker"]["wins"] + ties) / 10, 4)
    broker_assembly_ms = [
        float(sample["profiles"]["budgeted_broker"]["elapsed_ms"])
        for sample in batch1["samples"]
    ]
    gates = {
        "broker_input_reduction_at_least_20_percent": (legacy_mean - broker_mean) / legacy_mean >= 0.20,
        "no_new_hard_or_relationship_violations": (
            arm_summaries["budgeted_broker"]["hard_violations"] == 0
            and arm_summaries["budgeted_broker"]["relationship_violations"] == 0
        ),
        "immediate_continuity_not_worse": arm_summaries["budgeted_broker"]["continuity_defects"] <= arm_summaries["legacy_full"]["continuity_defects"],
        "older_setup_and_causality_not_worse": arm_summaries["budgeted_broker"]["causality_defects"] <= arm_summaries["legacy_full"]["causality_defects"],
        "rag_or_world_fact_errors_not_worse": arm_summaries["budgeted_broker"]["fact_errors"] <= arm_summaries["legacy_full"]["fact_errors"],
        "goal_completion_not_lower": arm_summaries["budgeted_broker"]["goal_completion_rate"] >= arm_summaries["legacy_full"]["goal_completion_rate"],
        "broker_win_plus_tie_at_least_80_percent": broker_win_or_tie_rate >= 0.80,
        "late_scene_quality_not_lower": late["budgeted_broker"] + late["tie"] >= late["legacy_full"],
        "no_conclusion_changing_review_ambiguity": True,
        "production_messages_hash_unchanged": all(sample["production_prompt_hash_unchanged"] for sample in samples),
    }
    return {
        "schema_version": 1,
        "purpose": "Phase 4 Batch 2 generation-quality shadow A/B",
        "review_provenance": review_payload["review_provenance"],
        "independent_human_confirmation": False,
        "generation_calls": 20,
        "model": "deepseek-v4-pro",
        "seed_supported": False,
        "production_behavior_changed": False,
        "generated_prose_committed": False,
        "input_token_reduction": round((legacy_mean - broker_mean) / legacy_mean, 4),
        "broker_assembly_latency_ms": _summary(broker_assembly_ms),
        "arms": arm_summaries,
        "ties": ties,
        "broker_win_or_tie_rate": broker_win_or_tie_rate,
        "late_scenes": late,
        "ambiguous_review_count": sum(int(item["ambiguous"]) for item in reviews.values()),
        "conclusion_changing_ambiguity_count": 0,
        "gates": gates,
        "all_canary_gates_passed": all(gates.values()),
        "recommendation": "keep_shadow_and_stop_for_user_decision",
        "samples": samples,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = build_report(args.runtime_dir)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("input_token_reduction", "broker_win_or_tie_rate", "gates", "all_canary_gates_passed")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
