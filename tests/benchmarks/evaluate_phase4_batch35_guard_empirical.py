"""Audit ContinuityRiskGuard assumptions against existing real Writer A/B outputs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BATCH2 = ROOT / "reports" / "phase4-batch2-generation-quality-ab.json"
BATCH3 = ROOT / "reports" / "phase4-batch3-continuity-risk-guard-shadow.json"
TARGET_QUERIES = (4, 6, 7, 8)
ISSUE_FIELDS = (
    "hard_violations",
    "relationship_violations",
    "continuity_defects",
    "causality_defects",
    "fact_errors",
)


def build_report() -> dict:
    batch2 = json.loads(BATCH2.read_text(encoding="utf-8"))
    batch3 = json.loads(BATCH3.read_text(encoding="utf-8"))
    batch2_samples = {int(item["query_index"]): item for item in batch2["samples"]}
    batch3_samples = {int(item["query_index"]): item for item in batch3["samples"]}
    samples = []
    for query_index in TARGET_QUERIES:
        generated = batch2_samples[query_index]
        guarded = batch3_samples[query_index]
        candidates = {item["arm"]: item for item in generated["candidates"]}
        legacy = candidates["legacy_full"]["codex_assisted_judgment"]
        broker = candidates["budgeted_broker"]["codex_assisted_judgment"]
        deltas = {field: int(broker[field]) - int(legacy[field]) for field in ISSUE_FIELDS}
        goal_delta = int(bool(broker["goal_complete"])) - int(bool(legacy["goal_complete"]))
        net_regression = goal_delta < 0 or any(delta > 0 for delta in deltas.values())
        restored = guarded["profiles"]["risk_guarded_broker"]["restored_item_ids"]
        hash_match = generated["broker_messages_hash"] == guarded["messages_hashes"]["budgeted_broker"]
        samples.append({
            "query_index": query_index,
            "section": generated["section"],
            "subsection": generated["subsection"],
            "budgeted_messages_hash": generated["broker_messages_hash"],
            "matches_frozen_batch3_budgeted_hash": hash_match,
            "budgeted_output_sha256": candidates["budgeted_broker"]["output_sha256"],
            "legacy_output_sha256": candidates["legacy_full"]["output_sha256"],
            "budgeted_output_characters": candidates["budgeted_broker"]["characters"],
            "actual_input_plus_output_tokens": candidates["budgeted_broker"]["actual_total_tokens"],
            "restored_by_guard_item_ids": restored,
            "guard_theoretical_risk_item_count": len(restored),
            "legacy_judgment": legacy,
            "budgeted_judgment": broker,
            "budgeted_minus_legacy_issue_delta": deltas,
            "goal_completion_delta": goal_delta,
            "empirical_net_regression": net_regression,
            "winner_arm": generated["winner_arm"],
            "short_evidence": generated["short_evidence"],
            "review_provenance": "codex_assisted_review",
        })

    regressions = [sample for sample in samples if sample["empirical_net_regression"]]
    shared_only = [
        sample for sample in samples
        if not sample["empirical_net_regression"]
        and any(int(sample["budgeted_judgment"][field]) > 0 for field in ISSUE_FIELDS)
    ]
    return {
        "schema_version": 1,
        "purpose": "Phase 4 Batch 3.5 empirical audit of ContinuityRiskGuard assumptions",
        "new_generation_calls": 0,
        "new_generation_blocked_reason": "tenant policy denied private workspace data transmission to api.deepseek.com",
        "historical_real_generation_source": str(BATCH2.relative_to(ROOT)).replace("\\", "/"),
        "historical_budgeted_writer_outputs_reused": len(samples),
        "historical_legacy_comparators_reused": len(samples),
        "model": batch2["model"],
        "seed_supported": batch2["seed_supported"],
        "review_provenance": "codex_assisted_review",
        "independent_human_confirmation": False,
        "guard_theoretical_protected_items_all_scenes": sum(
            len(item["profiles"]["risk_guarded_broker"]["restored_item_ids"])
            for item in batch3["samples"]
        ),
        "guard_theoretical_protected_items_target_scenes": sum(item["guard_theoretical_risk_item_count"] for item in samples),
        "target_scene_count": len(samples),
        "empirical_net_regression_scene_count": len(regressions),
        "empirical_net_regression_rate": round(len(regressions) / len(samples), 4),
        "shared_defect_not_attributable_to_broker_scene_count": len(shared_only),
        "all_budgeted_hashes_match_batch3": all(item["matches_frozen_batch3_budgeted_hash"] for item in samples),
        "conclusions": {
            "supported": [
                "the complete budgeted-broker deletion bundle was associated with measured regressions in Q4, Q6 and Q7",
                "Q8 over-advancement occurred in both legacy and budgeted outputs and is not a net Broker regression",
                "the any-risk-restores-full-item guard is too broad to meet the token target",
            ],
            "not_supported": [
                "all 19 protected older items are necessary",
                "each of the five guard signal classes prevents a measured regression",
                "all whole-item selection strategies are infeasible",
                "traceable section summaries are the only remaining architecture",
            ],
        },
        "recommendation": "hold_architecture_decision_and_test_minimal_restoration_across_all_dropped_context_items_when_generation_is_permitted",
        "production_behavior_changed": False,
        "generated_prose_committed": False,
        "samples": samples,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = build_report()
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "target_scenes": report["target_scene_count"],
        "empirical_regressions": report["empirical_net_regression_scene_count"],
        "shared_non_broker_defects": report["shared_defect_not_attributable_to_broker_scene_count"],
        "hashes_match": report["all_budgeted_hashes_match_batch3"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
