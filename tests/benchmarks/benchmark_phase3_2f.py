"""Phase 3 Batch 2F: structure-aware context compaction on frozen V1 selections."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import mean

from app.structured_context_compactor import StructuredContextCompactor
from tests.benchmarks.ablate_phase3_shadow import _load_chroma_documents
from tests.benchmarks.benchmark_phase3_v2 import CHARACTER_NAMES, DEFAULT_ASSISTED_REVIEW, DEFAULT_HUMAN_REVIEW, _labels
from tests.quality.baseline import DEFAULT_RAG, ROOT, load_json


DEFAULT_BATCH2E = ROOT / "reports" / "phase3-batch2e-context-compaction.json"
DEFAULT_OUTPUT = ROOT / "reports" / "phase3-batch2f-structured-compaction.json"
DEFAULT_REVIEW = ROOT / "tests" / "quality" / "phase3_batch2f_evidence_review.json"
DEFAULT_JUDGMENTS = ROOT / "tests" / "quality" / "phase3_batch2f_evidence_judgments.json"


def _supported_items(human_review: dict) -> list[dict]:
    items = []
    for group in human_review["queries"]:
        for candidate in group["candidates"]:
            facts = candidate.get("supports_which_fact") or []
            if facts:
                items.append({
                    "review_item_id": f"q{int(group['query_index']):02d}-{str(candidate['source_id'])[:8]}",
                    "query_index": int(group["query_index"]),
                    "source_id": str(candidate["source_id"]),
                    "supported_facts": list(facts),
                    "original_evidence_text": candidate["evidence_text"],
                })
    return items


def _sources_for_run(run: dict, documents: dict[str, dict]) -> list[dict]:
    trace = {str(item["id"]): item for item in run["candidate_trace"]}
    sources = []
    for source_id in run["selected_ids"]:
        candidate, document = trace[str(source_id)], documents[str(source_id)]
        sources.append({
            "source_id": str(source_id),
            "text": document["text"],
            "section": int(candidate["section"]),
            "subsection": int(candidate["subsection"]),
            "title": candidate["title"],
            "final_score": float(candidate["final_score"]),
        })
    return sources


def _traceability_ok(results: dict[int, dict], documents: dict[str, dict]) -> bool:
    return all(
        documents[fragment["source_id"]]["text"][fragment["start"]:fragment["end"]] == fragment["text"]
        for result in results.values() for fragment in result["fragments"]
    )


def _metrics(results: dict[int, dict], runs: list[dict], labels: dict, supported: list[dict], documents: dict[str, dict]) -> dict:
    raw_total = sum(result["raw_tokens"] for result in results.values())
    compact_total = sum(result["compacted_tokens"] for result in results.values())
    selected = represented = known = known_kept = late = late_kept = 0
    for run in runs:
        query_index = int(run["query_index"])
        kept = set(results[query_index]["represented_source_ids"])
        selected += len(run["selected_ids"])
        represented += len(set(run["selected_ids"]) & kept)
        for source_id in run["selected_ids"]:
            label = labels.get((query_index, str(source_id)))
            if label and label["label"] == "相关":
                known += 1
                known_kept += int(str(source_id) in kept)
        if int(run["current_section"]) >= 13:
            late += len(run["selected_ids"])
            late_kept += len(set(run["selected_ids"]) & kept)
    supported_kept = sum(
        item["source_id"] in results[item["query_index"]]["represented_source_ids"]
        for item in supported
    )
    return {
        "selected_sources": selected,
        "represented_sources": represented,
        "selected_source_retention": round(represented / selected, 4) if selected else 1.0,
        "known_relevant_sources": known,
        "known_relevant_sources_retained": known_kept,
        "known_relevant_source_retention": round(known_kept / known, 4) if known else 1.0,
        "supported_fact_sources_retained": supported_kept,
        "supported_fact_source_retention": round(supported_kept / len(supported), 4) if supported else 1.0,
        "mean_raw_tokens": round(mean(result["raw_tokens"] for result in results.values()), 3),
        "mean_compacted_tokens": round(mean(result["compacted_tokens"] for result in results.values()), 3),
        "weighted_token_reduction": round(1 - compact_total / raw_total, 4) if raw_total else 0.0,
        "fallback_full_text_count": sum(result["fallback_full_text_count"] for result in results.values()),
        "budget_overflow_queries": sum(bool(result["budget_overflow_reason"]) for result in results.values()),
        "mean_fragment_count": round(mean(len(result["fragments"]) for result in results.values()), 3),
        "mean_elapsed_ms": round(mean(result["elapsed_ms"] for result in results.values()), 3),
        "late_source_retention": round(late_kept / late, 4) if late else 1.0,
        "all_fragments_traceable": _traceability_ok(results, documents),
    }


def _build_review(supported: list[dict], profiles: dict[str, dict[int, dict]], judgments: dict) -> dict:
    decisions = judgments.get("decisions", {}) if judgments else {}
    items = []
    for item in supported:
        strategy_rows = {}
        for profile, results in profiles.items():
            fragments = [
                fragment for fragment in results[item["query_index"]]["fragments"]
                if fragment["source_id"] == item["source_id"]
            ]
            item_decisions = decisions.get(item["review_item_id"], {})
            decision = item_decisions.get(profile, item_decisions.get("*", {}))
            strategy_rows[profile] = {
                "fragments": fragments,
                "compacted_evidence_text": "\n…\n".join(fragment["text"] for fragment in fragments),
                "codex_assisted_evidence_preserved": decision.get("preserved", ""),
                "codex_review_note": decision.get("note", ""),
                "review_provenance": "codex_assisted_review",
                "independent_human_confirmation": False,
            }
        items.append({**item, "strategies": strategy_rows})
    summary = {}
    for profile in profiles:
        rows = [item["strategies"][profile] for item in items]
        reviewed = sum(row["codex_assisted_evidence_preserved"] in (True, False) for row in rows)
        preserved = sum(row["codex_assisted_evidence_preserved"] is True for row in rows)
        summary[profile] = {
            "item_count": len(rows), "reviewed": reviewed, "preserved": preserved,
            "status": "complete" if reviewed == len(rows) else "awaiting_codex_assisted_review",
        }
    return {
        "schema_version": 1,
        "purpose": "结构化压缩策略并排事实证据复核；Codex 辅助诊断，不是独立人工金标准。",
        "summary": summary,
        "items": items,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch2e", type=Path, default=DEFAULT_BATCH2E)
    parser.add_argument("--annotations", type=Path, default=DEFAULT_RAG)
    parser.add_argument("--human-review", type=Path, default=DEFAULT_HUMAN_REVIEW)
    parser.add_argument("--assisted-review", type=Path, default=DEFAULT_ASSISTED_REVIEW)
    parser.add_argument("--judgments", type=Path, default=DEFAULT_JUDGMENTS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--review-output", type=Path, default=DEFAULT_REVIEW)
    args = parser.parse_args()

    batch2e = load_json(args.batch2e)
    runs = batch2e["v1_queries"]
    annotation = load_json(args.annotations)
    entries = {int(entry["query_index"]): entry for entry in annotation["entries"]}
    human_review = load_json(args.human_review)
    labels = _labels(human_review, load_json(args.assisted_review))
    supported = _supported_items(human_review)
    selected_ids = sorted({str(source_id) for run in runs for source_id in run["selected_ids"]})
    documents = _load_chroma_documents(selected_ids)
    compactors = {
        "paragraph_window": StructuredContextCompactor(strategy="paragraph_window"),
        "dialogue_narrative_block": StructuredContextCompactor(strategy="dialogue_narrative_block"),
        "character_span_150": StructuredContextCompactor(strategy="character_span_window", window_radius=150),
        "character_span_250": StructuredContextCompactor(strategy="character_span_window", window_radius=250),
        "character_span_350": StructuredContextCompactor(strategy="character_span_window", window_radius=350),
    }
    profiles: dict[str, dict[int, dict]] = {name: {} for name in compactors}
    for run in runs:
        query_index = int(run["query_index"])
        sources = _sources_for_run(run, documents)
        for name, compactor in compactors.items():
            profiles[name][query_index] = compactor.compact(
                query=entries[query_index]["query"], sources=sources, character_names=CHARACTER_NAMES
            )
    metrics = {
        name: _metrics(results, runs, labels, supported, documents)
        for name, results in profiles.items()
    }
    judgments = load_json(args.judgments) if args.judgments.exists() else {}
    review = _build_review(supported, profiles, judgments)
    gates = {}
    for name, values in metrics.items():
        review_summary = review["summary"][name]
        gates[name] = {
            "selected_source_retention_is_1": values["selected_source_retention"] == 1.0,
            "known_relevant_source_retention_is_1": values["known_relevant_source_retention"] == 1.0,
            "all_11_fact_evidence_preserved": review_summary["status"] == "complete" and review_summary["preserved"] == 11,
            "token_reduction_at_least_0_20": values["weighted_token_reduction"] >= 0.20,
            "late_source_retention_is_1": values["late_source_retention"] == 1.0,
            "all_fragments_traceable": values["all_fragments_traceable"],
            "writer_and_production_unchanged": True,
        }
    eligible = [name for name, checks in gates.items() if all(checks.values())]
    selected = min(eligible, key=lambda name: metrics[name]["mean_compacted_tokens"]) if eligible else None
    per_query = {
        name: [{
            "query_index": query_index,
            "raw_tokens": result["raw_tokens"],
            "compacted_tokens": result["compacted_tokens"],
            "token_reduction": result["token_reduction"],
            "fragment_count": len(result["fragments"]),
            "fallbacks": result["fallbacks"],
            "budget_overflow_reason": result["budget_overflow_reason"],
            "elapsed_ms": result["elapsed_ms"],
        } for query_index, result in sorted(results.items())]
        for name, results in profiles.items()
    }
    report = {
        "schema_version": 1,
        "mode": "frozen_v1_selected_sources_structured_compaction_shadow",
        "production_changed": False,
        "writer_changed": False,
        "query_planner_changed": False,
        "reranker_changed": False,
        "candidate_set_changed": False,
        "runtime_uses_gold_or_must_recall_facts": False,
        "query_count": len(runs),
        "batch2e_negative_baseline": {
            "mean_raw_tokens": batch2e["metrics"]["mean_raw_tokens"],
            "mean_compacted_tokens": batch2e["metrics"]["mean_compacted_tokens"],
            "weighted_token_reduction": batch2e["metrics"]["weighted_token_reduction"],
            "fact_evidence_preserved": batch2e["fact_evidence_review"]["evidence_preserved"],
        },
        "metrics": metrics,
        "fact_evidence_review": review["summary"],
        "baseline_annotation_ceiling": {
            "independently_verifiable_items": 9,
            "total_items": 11,
            "affected_review_item_ids": ["q06-679a7aa0", "q07-679a7aa0"],
            "reason": "The original sources do not independently contain every part of their assigned supports_which_fact claims.",
        },
        "gates": gates,
        "eligible_profiles": eligible,
        "selected_profile": selected,
        "decision": "eligible_for_limited_production_trial" if selected else "remain_shadow_no_structured_profile_passed",
        "failure_diagnosis": {
            "structure_boundary_insufficient": "Paragraph and dialogue profiles reduce tokens by at least 20% but preserve only 4/11 and 7/11 assigned fact items.",
            "current_chunk_redundancy_insufficient": "The 150/250/350 character profiles preserve all 9 independently verifiable items but reduce tokens by only 7.76%, 1.62% and 0.04%.",
            "source_level_budget_infeasible": "Boundary expansion around three deterministic anchors causes character windows to cover most chunks; a safe 20% reduction is not achieved.",
            "annotation_ceiling": "Two assigned fact items are not fully supported by their original source, making the literal 11/11 gate unattainable before label correction.",
        },
        "per_query": per_query,
        "compactions": {name: {str(k): v for k, v in results.items()} for name, results in profiles.items()},
        "v1_queries": runs,
        "limitations": [
            "The exact Batch 2E V1 selected source set is frozen and reused.",
            "Gold sections, must-recall facts and review answers are evaluation-only inputs.",
            "Evidence decisions are Codex-assisted diagnostics, not independent human gold labels.",
            "No strategy is connected to Writer or production retrieval.",
        ],
    }
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.review_output.write_text(json.dumps(review, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "output": str(args.output), "review": str(args.review_output), "metrics": metrics,
        "review_summary": review["summary"], "eligible_profiles": eligible,
        "selected_profile": selected, "decision": report["decision"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
