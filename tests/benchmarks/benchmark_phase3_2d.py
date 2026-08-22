"""Phase 3 Batch 2D: real 2x2 planner/reranker loss attribution."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.retrieval_pipeline import (
    ExplainableReranker,
    ExplainableRerankerV2,
    QueryPlanner,
    QueryPlannerV2,
    ShadowRetriever,
)
from app.vector_store import VectorStore
from tests.benchmarks.ablate_phase3_shadow import _load_chroma_documents
from tests.benchmarks.benchmark_phase3_v2 import (
    CHARACTER_NAMES,
    DEFAULT_ASSISTED_REVIEW,
    DEFAULT_HUMAN_REVIEW,
    _labels,
    _metrics,
    _review_has_work,
)
from tests.quality.baseline import DEFAULT_RAG, ROOT, load_json


DEFAULT_OUTPUT = ROOT / "reports" / "phase3-batch2d-loss-attribution.json"
DEFAULT_DIFF = ROOT / "tests" / "quality" / "phase3_batch2d_decision_candidates_review.json"

COMBINATIONS = {
    "p1_r1": ("v1", "v1"),
    "p1_r2": ("v1", "v2"),
    "p2_r1": ("v2", "v1"),
    "p2_r2": ("v2", "v2"),
}


def _run_combination(
    store: VectorStore,
    entry: dict,
    task_id: str,
    *,
    planner_version: str,
    reranker_version: str,
) -> dict:
    planner = QueryPlanner(max_queries=4) if planner_version == "v1" else QueryPlannerV2(max_queries=2)
    plan = planner.plan_text(
        entry["query"],
        requested_intents=entry["query_intent"],
        character_names=CHARACTER_NAMES,
        current_section=int(entry["section"]),
        current_subsection=int(entry["subsection"]),
    )
    if reranker_version == "v1":
        reranker = ExplainableReranker(min_score=0.35, max_results=5)
    else:
        reranker = ExplainableRerankerV2(
            min_score=0.35, max_results=5, token_budget=600
        )
    result = ShadowRetriever(candidate_k=12, reranker=reranker).run(
        store, plan, task_id=task_id
    )
    return {
        "query_index": int(entry["query_index"]),
        "current_section": int(entry["section"]),
        "planner_version": planner_version,
        "reranker_version": reranker_version,
        "plan": result["plan"],
        "filter": result["filter"],
        "selected_ids": result["selected_ids"],
        "selected_sections": result["selected_sections"],
        "selected_count": result["selected_count"],
        "candidate_pool_count": result["merged_candidate_count"],
        "estimated_context_tokens": result["estimated_context_tokens"],
        "elapsed_ms": result["elapsed_ms"],
        "candidate_trace": result["rerank"]["candidates"],
    }


def _index_runs(runs: list[dict]) -> dict[int, dict]:
    return {int(run["query_index"]): run for run in runs}


def _loss_attribution(
    baseline_runs: list[dict], v2_runs: list[dict], labels: dict,
) -> dict:
    v2_by_query = _index_runs(v2_runs)
    losses = []
    pending_documents = set()
    for baseline in baseline_runs:
        query_index = int(baseline["query_index"])
        v2 = v2_by_query[query_index]
        v2_selected = set(v2["selected_ids"])
        v2_trace = {str(item["id"]): item for item in v2["candidate_trace"]}
        baseline_trace = {str(item["id"]): item for item in baseline["candidate_trace"]}
        for source_id in baseline["selected_ids"]:
            key = (query_index, str(source_id))
            label = labels.get(key)
            if not label or label["label"] != "相关" or source_id in v2_selected:
                continue
            v1_item = baseline_trace[str(source_id)]
            v2_item = v2_trace.get(str(source_id))
            cause = "planner_coarse_recall_miss" if v2_item is None else str(v2_item["reason"])
            if cause not in {
                "planner_coarse_recall_miss", "future_section", "below_min_score",
                "below_non_character_support", "top_k_limit", "token_budget_limit",
            }:
                cause = "other"
            losses.append({
                "query_index": query_index,
                "source_id": str(source_id),
                "label_provenance": label["provenance"],
                "section": int(v1_item["section"]),
                "subsection": int(v1_item["subsection"]),
                "title": v1_item["title"],
                "v1": {
                    "coarse_ranks": v1_item["coarse_ranks"],
                    "score_components": v1_item["score_components"],
                    "final_score": v1_item["final_score"],
                    "reason": v1_item["reason"],
                },
                "v2": (
                    {
                        "present_in_candidate_pool": True,
                        "coarse_ranks": v2_item["coarse_ranks"],
                        "score_components": v2_item["score_components"],
                        "character_evidence": v2_item["character_evidence"],
                        "final_score": v2_item["final_score"],
                        "reason": v2_item["reason"],
                    }
                    if v2_item else {
                        "present_in_candidate_pool": False,
                        "reason": "planner_coarse_recall_miss",
                    }
                ),
                "attribution": cause,
            })
            pending_documents.add(str(source_id))
    documents = _load_chroma_documents(sorted(pending_documents)) if pending_documents else {}
    for loss in losses:
        loss["evidence_text"] = documents[loss["source_id"]]["text"]
    counts = {cause: 0 for cause in (
        "planner_coarse_recall_miss", "future_section", "below_min_score",
        "below_non_character_support", "top_k_limit", "token_budget_limit", "other",
    )}
    for loss in losses:
        counts[loss["attribution"]] += 1
    return {"lost_known_relevant_count": len(losses), "counts": counts, "items": losses}


def _upper_bound(metrics: dict, known_relevant_total: int) -> dict:
    unknown = int(metrics["unlabeled_selected"])
    relevant = int(metrics["known_relevant_selected"])
    irrelevant = int(metrics["known_irrelevant_selected"])
    late_rows = [row for row in metrics["per_query"] if int(row["query_index"]) in {1, 4, 10}]
    late_relevant = sum(int(row["known_relevant"]) for row in late_rows)
    late_irrelevant = sum(int(row["known_irrelevant"]) for row in late_rows)
    late_unknown = sum(int(row["unlabeled"]) for row in late_rows)
    optimistic_precision = (
        (relevant + unknown) / (relevant + irrelevant + unknown)
        if relevant + irrelevant + unknown else 0.0
    )
    optimistic_retention = (
        (relevant + unknown) / (known_relevant_total + unknown)
        if known_relevant_total + unknown else 0.0
    )
    optimistic_late = (
        (late_relevant + late_unknown) / (late_relevant + late_irrelevant + late_unknown)
        if late_relevant + late_irrelevant + late_unknown else 0.0
    )
    return {
        "assumption": "every unlabeled selection is relevant",
        "maximum_closed_set_precision": round(optimistic_precision, 4),
        "maximum_pooled_known_relevant_retention": round(optimistic_retention, 4),
        "maximum_late_precision": round(optimistic_late, 4),
        "can_reach_all_quality_gates": (
            optimistic_precision >= 0.68
            and optimistic_retention >= 0.90
            and optimistic_late > 0.40
        ),
    }


def _decision_review(
    entries: list[dict], combinations: dict[str, dict], labels: dict, upper_bounds: dict,
) -> dict:
    entry_by_index = {int(entry["query_index"]): entry for entry in entries}
    eligible_combinations = {
        name for name, bound in upper_bounds.items() if bound["can_reach_all_quality_gates"]
    }
    pending: dict[tuple[int, str], dict] = {}
    for name in eligible_combinations:
        for run in combinations[name]["queries"]:
            query_index = int(run["query_index"])
            trace = {str(item["id"]): item for item in run["candidate_trace"]}
            for source_id in run["selected_ids"]:
                key = (query_index, str(source_id))
                if key in labels:
                    continue
                item = pending.setdefault(key, {
                    "query_index": query_index,
                    "source_id": str(source_id),
                    "selected_by_combinations": [],
                    "trace": trace[str(source_id)],
                })
                item["selected_by_combinations"].append(name)
    documents = _load_chroma_documents(sorted({key[1] for key in pending})) if pending else {}
    candidates = []
    for key in sorted(pending):
        item = pending[key]
        source = entry_by_index[key[0]]
        trace = item["trace"]
        candidates.append({
            "review_item_id": f"q{key[0]:02d}-{key[1][:8]}",
            "query_index": key[0],
            "query": source["query"],
            "query_intent": source["query_intent"],
            "must_recall_facts": source["must_recall_facts"],
            "source_id": key[1],
            "section": int(trace["section"]),
            "subsection": int(trace["subsection"]),
            "title": trace["title"],
            "evidence_text": documents[key[1]]["text"],
            "selected_by_combinations": sorted(item["selected_by_combinations"]),
            "score_components": trace["score_components"],
            "final_score": trace["final_score"],
            "review_provenance": "unreviewed",
            "codex_assisted_relevant": "",
            "codex_supports_which_fact": [],
            "codex_review_note": "",
            "independent_human_relevant": "",
        })
    return {
        "schema_version": 1,
        "purpose": "Batch 2D：仅包含乐观上界仍可能通过全部质量门槛的组合所引入的未知候选",
        "summary": {
            "candidate_count": len(candidates),
            "eligible_combinations": sorted(eligible_combinations),
            "status": "awaiting_source_attributed_review" if candidates else "review_not_required",
        },
        "candidates": candidates,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--annotations", type=Path, default=DEFAULT_RAG)
    parser.add_argument("--human-review", type=Path, default=DEFAULT_HUMAN_REVIEW)
    parser.add_argument("--assisted-review", type=Path, default=DEFAULT_ASSISTED_REVIEW)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--diff-output", type=Path, default=DEFAULT_DIFF)
    parser.add_argument("--force-review-overwrite", action="store_true")
    parser.add_argument("--reuse-existing-traces", action="store_true")
    args = parser.parse_args()
    if _review_has_work(args.diff_output) and not args.force_review_overwrite:
        raise FileExistsError(f"refusing to overwrite review work in {args.diff_output}")

    annotation = load_json(args.annotations)
    entries = annotation["entries"]
    labels = _labels(load_json(args.human_review), load_json(args.assisted_review))
    if args.reuse_existing_traces:
        existing = load_json(args.output)
        combination_results = existing["combinations"]
    else:
        store = VectorStore()
        combination_results = {}
        for name, (planner_version, reranker_version) in COMBINATIONS.items():
            runs = [
                _run_combination(
                    store, entry, annotation["task_id"],
                    planner_version=planner_version,
                    reranker_version=reranker_version,
                )
                for entry in entries
            ]
            combination_results[name] = {
                "planner": planner_version,
                "reranker": reranker_version,
                "metrics": _metrics(entries, runs, labels),
                "queries": runs,
            }
    overall_loss = _loss_attribution(
        combination_results["p1_r1"]["queries"],
        combination_results["p2_r2"]["queries"],
        labels,
    )
    planner_loss = _loss_attribution(
        combination_results["p1_r1"]["queries"],
        combination_results["p2_r1"]["queries"],
        labels,
    )
    reranker_v1_planner_loss = _loss_attribution(
        combination_results["p1_r1"]["queries"],
        combination_results["p1_r2"]["queries"],
        labels,
    )
    reranker_v2_planner_loss = _loss_attribution(
        combination_results["p2_r1"]["queries"],
        combination_results["p2_r2"]["queries"],
        labels,
    )
    known_relevant_total = sum(item["label"] == "相关" for item in labels.values())
    upper_bounds = {
        name: _upper_bound(result["metrics"], known_relevant_total)
        for name, result in combination_results.items()
    }
    review = _decision_review(entries, combination_results, labels, upper_bounds)
    p1r1 = combination_results["p1_r1"]["metrics"]
    p1r2 = combination_results["p1_r2"]["metrics"]
    retain_v2_reranker = (
        (p1r2["pooled_known_relevant_retention"] or 0.0) >= 0.90
        and (p1r2["reviewed_closed_set_precision"] or 0.0)
        > (p1r1["reviewed_closed_set_precision"] or 0.0)
    )
    report = {
        "schema_version": 1,
        "mode": "real_2x2_shadow_loss_attribution",
        "production_changed": False,
        "writer_changed": False,
        "default_configuration_changed": False,
        "collection_strategy": "shared_collection_task_id_filter",
        "combinations": combination_results,
        "known_relevant_loss_attribution": {
            "p1_r1_to_p2_r2": overall_loss,
            "planner_v2_effect_with_reranker_v1": planner_loss,
            "reranker_v2_effect_with_planner_v1": reranker_v1_planner_loss,
            "reranker_v2_effect_with_planner_v2": reranker_v2_planner_loss,
        },
        "optimistic_unknown_upper_bounds": upper_bounds,
        "new_candidate_review": review["summary"],
        "decision": {
            "remain_shadow": True,
            "retain_query_planner_v2": False,
            "retain_graded_reranker_direction": retain_v2_reranker,
            "start_phase4": False,
            "reason": (
                "V1 planner + V2 reranker clears the retention gate and improves precision."
                if retain_v2_reranker else
                "No tested hybrid both preserves at least 90% pooled known relevance and improves precision."
            ),
        },
        "metric_scope": {
            "closed_set_precision": "reviewed relevant / reviewed selected; unknown excluded",
            "pooled_known_relevant_retention": "selected known relevant / 23 known relevant query-candidate labels",
            "gold_section_candidate_pool_coverage": "section proxy only, not true Recall",
            "latency": "real elapsed time for each independently executed combination",
        },
        "limitations": [
            "must_recall_facts and gold sections are used only after retrieval for evaluation and never enter query planning or scoring.",
            "Codex-assisted labels remain diagnostic and cannot satisfy an independent-human release gate.",
            "The four combinations reuse the current embedding model, chunks, shared collection and mandatory task_id filter.",
        ],
    }
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.diff_output.write_text(json.dumps(review, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "output": str(args.output),
        "metrics": {name: value["metrics"] for name, value in combination_results.items()},
        "loss_counts": overall_loss["counts"],
        "lost_known_relevant_count": overall_loss["lost_known_relevant_count"],
        "module_loss_counts": {
            "planner_v2_with_reranker_v1": planner_loss["lost_known_relevant_count"],
            "reranker_v2_with_planner_v1": reranker_v1_planner_loss["lost_known_relevant_count"],
            "reranker_v2_with_planner_v2": reranker_v2_planner_loss["lost_known_relevant_count"],
        },
        "upper_bounds": upper_bounds,
        "new_candidate_review": review["summary"],
        "decision": report["decision"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
