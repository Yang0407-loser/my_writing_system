"""Merge isolated Phase 4 Batch 1 query runs into the canonical report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from tests.benchmarks.benchmark_context_input_census import REVIEW_PATH, human_evidence_manifest
from tests.benchmarks.benchmark_phase4_context_broker import PROFILES, _aggregate_profile
from tests.quality.baseline import DEFAULT_RAG, load_json


ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parts-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "reports" / "phase4-batch1-context-broker-shadow.json")
    args = parser.parse_args()
    parts = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(args.parts_dir.glob("part-q*.json"))]
    samples = sorted((sample for part in parts for sample in part["samples"]), key=lambda item: item["query_index"])
    if [sample["query_index"] for sample in samples] != list(range(1, 11)):
        raise ValueError("expected exactly isolated query parts 1..10")
    retrieval = sorted((run for part in parts for run in part["retrieval_runs"]), key=lambda item: item["query_index"])
    review = load_json(REVIEW_PATH)
    rag_annotation = load_json(DEFAULT_RAG)
    evaluation = {
        int(entry["query_index"]): human_evidence_manifest(review, int(entry["query_index"]))
        for entry in rag_annotation["entries"]
    }
    summaries = {profile: _aggregate_profile(samples, profile, evaluation) for profile in PROFILES}
    legacy_hash_unchanged = all(sample["writer_legacy_message_hash_unchanged"] for sample in samples)
    for summary in summaries.values():
        summary["acceptance"]["writer_legacy_message_hash_unchanged"] = legacy_hash_unchanged
        summary["acceptance"]["all_batch1_gates"] = all(summary["acceptance"].values())
    eligible = [profile for profile in PROFILES if profile != "legacy_full" and summaries[profile]["acceptance"]["all_batch1_gates"]]
    report = {
        "schema_version": 1,
        "purpose": "Phase 4 Batch 1 Context Broker whole-item selection and soft-budget shadow experiment",
        "offline_llm_calls": 0,
        "writer_generation_calls": 0,
        "production_behavior_changed": False,
        "context_manager_contract_changed": False,
        "runtime_forbidden_fields": ["must_recall_facts", "gold_sections", "human_relevant", "supports_which_fact", "review conclusions"],
        "evaluation_loaded_after_all_runtime_selections": True,
        "isolated_query_processes": 10,
        "isolation_reason": "bound cumulative local embedding/Chroma resources without changing retrieval behavior",
        "token_method": "estimated_token: Writer._estimate_prompt_tokens compatible",
        "target_tokens": 8500,
        "rules_snapshot": parts[0]["rules_snapshot"],
        "retrieval_runs": retrieval,
        "summary": summaries,
        "decision": {
            "eligible_profiles_for_generation_quality_shadow": eligible,
            "production_promotion": False,
            "status": "batch1_item_selection_passed_but_requires_generation_quality_evaluation" if eligible else "remain_shadow_batch1_gate_not_met",
            "batch2_started": False,
        },
        "limitations": [
            "This is a reconstructed frozen-input benchmark and does not call the Writer LLM.",
            "All legacy top-5 RAG items are protected; seven human-supported sources absent from legacy remain a retrieval ceiling.",
            "A token gate alone cannot justify production promotion.",
        ],
        "samples": samples,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "output": str(args.output),
        "profiles": {name: {
            "mean_tokens": value["mean_total_estimated_tokens"],
            "reduction": value["reduction_vs_legacy"],
            "passes": value["acceptance"]["all_batch1_gates"],
        } for name, value in summaries.items()},
        "decision": report["decision"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
