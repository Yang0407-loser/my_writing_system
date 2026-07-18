"""Run a real V1/V2 shadow comparison for Phase 3 Batch 2C."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import mean

from app.retrieval_pipeline import (
    QueryPlanner,
    QueryPlannerV2,
    ShadowRetriever,
    ShadowRetrieverV2,
)
from app.vector_store import VectorStore
from tests.benchmarks.ablate_phase3_shadow import _load_chroma_documents
from tests.quality.baseline import DEFAULT_RAG, ROOT, load_json


CHARACTER_NAMES = ("林晚", "周野", "季晴", "顾衍", "吴阿姨")
DEFAULT_HUMAN_REVIEW = ROOT / "tests" / "quality" / "phase3_shadow_candidates_review.json"
DEFAULT_ASSISTED_REVIEW = ROOT / "tests" / "quality" / "phase3_batch2_new_candidates_review.json"
DEFAULT_OUTPUT = ROOT / "reports" / "phase3-batch2c-v2-shadow.json"
DEFAULT_DIFF = ROOT / "tests" / "quality" / "phase3_batch2c_new_candidates_review.json"


def _review_has_work(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        review = load_json(path)
    except (json.JSONDecodeError, OSError, TypeError):
        return True
    return any(
        candidate.get("codex_assisted_relevant")
        or candidate.get("codex_supports_which_fact")
        or str(candidate.get("codex_review_note", "")).strip()
        or candidate.get("independent_human_relevant")
        for candidate in review.get("candidates", [])
    )


def _labels(human_review: dict, assisted_review: dict) -> dict[tuple[int, str], dict]:
    labels = {}
    for group in human_review["queries"]:
        for candidate in group["candidates"]:
            labels[(int(group["query_index"]), str(candidate["source_id"]))] = {
                "label": candidate["human_relevant"],
                "provenance": "independent_human",
            }
    for candidate in assisted_review["candidates"]:
        labels[(int(candidate["query_index"]), str(candidate["source_id"]))] = {
            "label": candidate["human_relevant"],
            "provenance": candidate["review_provenance"],
        }
    return labels


def _run_variant(
    store: VectorStore, entry: dict, task_id: str, *, version: str,
) -> dict:
    if version == "v1":
        plan = QueryPlanner(max_queries=4).plan_text(
            entry["query"],
            requested_intents=entry["query_intent"],
            character_names=CHARACTER_NAMES,
            current_section=int(entry["section"]),
            current_subsection=int(entry["subsection"]),
        )
        retriever = ShadowRetriever(candidate_k=12, min_score=0.35, max_results=5)
    else:
        plan = QueryPlannerV2(max_queries=2).plan_text(
            entry["query"],
            requested_intents=entry["query_intent"],
            character_names=CHARACTER_NAMES,
            current_section=int(entry["section"]),
            current_subsection=int(entry["subsection"]),
        )
        retriever = ShadowRetrieverV2(
            candidate_k=12, min_score=0.35, max_results=5, token_budget=600
        )
    result = retriever.run(store, plan, task_id=task_id)
    return {
        "query_index": int(entry["query_index"]),
        "current_section": int(entry["section"]),
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


def _metrics(entries: list[dict], runs: list[dict], labels: dict) -> dict:
    entry_by_index = {int(entry["query_index"]): entry for entry in entries}
    all_known_relevant = sum(item["label"] == "相关" for item in labels.values())
    relevant = irrelevant = unknown = 0
    independent_relevant = independent_irrelevant = 0
    late_relevant = late_irrelevant = 0
    gold_hits = gold_total = 0
    per_query = []
    for run in runs:
        query_index = int(run["query_index"])
        q_relevant = q_irrelevant = q_unknown = 0
        for source_id in run["selected_ids"]:
            item = labels.get((query_index, str(source_id)))
            if item is None:
                q_unknown += 1
                continue
            if item["label"] == "相关":
                q_relevant += 1
                if item["provenance"] == "independent_human":
                    independent_relevant += 1
            else:
                q_irrelevant += 1
                if item["provenance"] == "independent_human":
                    independent_irrelevant += 1
        relevant += q_relevant
        irrelevant += q_irrelevant
        unknown += q_unknown
        if int(run["current_section"]) >= 13:
            late_relevant += q_relevant
            late_irrelevant += q_irrelevant
        pool_sections = {
            int(candidate["section"])
            for candidate in run["candidate_trace"]
            if candidate["reason"] != "future_section"
        }
        gold_sections = {int(value) for value in entry_by_index[query_index]["gold_sections"]}
        q_gold_hits = len(pool_sections & gold_sections)
        gold_hits += q_gold_hits
        gold_total += len(gold_sections)
        per_query.append({
            "query_index": query_index,
            "query_count": len(run["plan"]["queries"]),
            "selected": int(run["selected_count"]),
            "known_relevant": q_relevant,
            "known_irrelevant": q_irrelevant,
            "unlabeled": q_unknown,
            "gold_section_pool_hits": q_gold_hits,
            "gold_sections": len(gold_sections),
            "token_estimate": int(run["estimated_context_tokens"]),
            "elapsed_ms": float(run["elapsed_ms"]),
        })
    labeled = relevant + irrelevant
    independent_labeled = independent_relevant + independent_irrelevant
    late_labeled = late_relevant + late_irrelevant
    return {
        "selected_candidates": sum(run["selected_count"] for run in runs),
        "known_relevant_selected": relevant,
        "known_irrelevant_selected": irrelevant,
        "unlabeled_selected": unknown,
        "reviewed_closed_set_precision": round(relevant / labeled, 4) if labeled else None,
        "pooled_known_relevant_retention": (
            round(relevant / all_known_relevant, 4) if all_known_relevant else None
        ),
        "independent_human_closed_set_precision": (
            round(independent_relevant / independent_labeled, 4)
            if independent_labeled else None
        ),
        "independent_human_label_coverage": (
            round(independent_labeled / sum(run["selected_count"] for run in runs), 4)
            if runs and sum(run["selected_count"] for run in runs) else 0.0
        ),
        "late_reviewed_closed_set_precision": (
            round(late_relevant / late_labeled, 4) if late_labeled else None
        ),
        "gold_section_candidate_pool_coverage": (
            round(gold_hits / gold_total, 4) if gold_total else None
        ),
        "gold_section_pool_hits": gold_hits,
        "gold_sections": gold_total,
        "mean_selected_per_query": round(mean(run["selected_count"] for run in runs), 3),
        "mean_token_estimate": round(mean(run["estimated_context_tokens"] for run in runs), 3),
        "mean_real_latency_ms": round(mean(run["elapsed_ms"] for run in runs), 3),
        "per_query": per_query,
    }


def _build_diff(entries: list[dict], v1_runs: list[dict], v2_runs: list[dict], labels: dict) -> dict:
    entry_by_index = {int(entry["query_index"]): entry for entry in entries}
    v1_ids = {int(run["query_index"]): set(run["selected_ids"]) for run in v1_runs}
    candidates = []
    pending = []
    for run in v2_runs:
        query_index = int(run["query_index"])
        trace_by_id = {str(item["id"]): item for item in run["candidate_trace"]}
        for source_id in run["selected_ids"]:
            key = (query_index, str(source_id))
            if source_id in v1_ids[query_index] or key in labels:
                continue
            pending.append((query_index, str(source_id), trace_by_id[str(source_id)], run["plan"]))
    documents = _load_chroma_documents(sorted({source_id for _, source_id, _, _ in pending})) if pending else {}
    for query_index, source_id, trace, plan in pending:
        entry = entry_by_index[query_index]
        candidates.append({
            "review_item_id": f"q{query_index:02d}-{source_id[:8]}",
            "query_index": query_index,
            "query": entry["query"],
            "query_intent": entry["query_intent"],
            "must_recall_facts": entry["must_recall_facts"],
            "v2_plan": plan,
            "source_id": source_id,
            "section": int(trace["section"]),
            "subsection": int(trace["subsection"]),
            "title": trace["title"],
            "evidence_text": documents[source_id]["text"],
            "selection_reason": trace["reason"],
            "score_components": trace["score_components"],
            "character_evidence": trace["character_evidence"],
            "final_score": trace["final_score"],
            "review_provenance": "unreviewed",
            "codex_assisted_relevant": "",
            "codex_supports_which_fact": [],
            "codex_review_note": "",
            "independent_human_relevant": "",
        })
    return {
        "schema_version": 1,
        "purpose": "Phase 3 Batch 2C：V2 相对真实 V1 新进入 top-5 且无既有标签的候选",
        "summary": {
            "candidate_count": len(candidates),
            "codex_assisted_reviewed": 0,
            "independent_human_reviewed": 0,
            "status": "awaiting_source_attributed_review" if candidates else "no_new_candidates",
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
    args = parser.parse_args()
    if _review_has_work(args.diff_output) and not args.force_review_overwrite:
        raise FileExistsError(
            f"refusing to overwrite attributed review data in {args.diff_output}; "
            "preserve it or pass --force-review-overwrite explicitly"
        )

    annotation = load_json(args.annotations)
    entries = annotation["entries"]
    labels = _labels(load_json(args.human_review), load_json(args.assisted_review))
    store = VectorStore()
    v1_runs = [_run_variant(store, entry, annotation["task_id"], version="v1") for entry in entries]
    v2_runs = [_run_variant(store, entry, annotation["task_id"], version="v2") for entry in entries]
    v1_metrics = _metrics(entries, v1_runs, labels)
    v2_metrics = _metrics(entries, v2_runs, labels)
    diff = _build_diff(entries, v1_runs, v2_runs, labels)
    raw_new_candidate_count = diff["summary"]["candidate_count"]
    known_relevant_total = sum(item["label"] == "相关" for item in labels.values())
    optimistic_relevant = v2_metrics["known_relevant_selected"] + raw_new_candidate_count
    optimistic_pool_total = known_relevant_total + raw_new_candidate_count
    optimistic_retention = (
        round(optimistic_relevant / optimistic_pool_total, 4)
        if optimistic_pool_total else 0.0
    )
    if optimistic_retention < 0.90:
        diff["candidates"] = []
        diff["summary"] = {
            "candidate_count": 0,
            "suppressed_new_top5_candidates": raw_new_candidate_count,
            "codex_assisted_reviewed": 0,
            "independent_human_reviewed": 0,
            "status": "review_not_required_release_gate_already_impossible",
            "reason": (
                "Even if every new unlabeled candidate were relevant, pooled known-relevant "
                f"retention would be {optimistic_retention:.4f}, below 0.90."
            ),
        }
    independent_gate_valid = (
        v2_metrics["independent_human_label_coverage"] == 1.0
        and diff["summary"]["independent_human_reviewed"] == diff["summary"]["candidate_count"]
    )
    gates = {
        "independent_human_precision_at_least_0_68": bool(
            independent_gate_valid
            and (v2_metrics["independent_human_closed_set_precision"] or 0.0) >= 0.68
        ),
        "pooled_known_relevant_retention_at_least_0_90": (
            (v2_metrics["pooled_known_relevant_retention"] or 0.0) >= 0.90
        ),
        "late_reviewed_precision_above_0_40": (
            (v2_metrics["late_reviewed_closed_set_precision"] or 0.0) > 0.40
        ),
        "writer_and_production_unchanged": True,
        "all_new_candidates_independently_reviewed": independent_gate_valid,
    }
    report = {
        "schema_version": 1,
        "mode": "real_v1_v2_shadow_comparison",
        "production_changed": False,
        "writer_changed": False,
        "default_configuration_changed": False,
        "collection_strategy": "shared_collection_task_id_filter",
        "v2_profile": {
            "max_queries": 2,
            "candidate_k_per_query": 12,
            "max_results": 5,
            "min_score": 0.35,
            "token_budget": 600,
            "character_score": "graded_metadata_title_text",
        },
        "metric_scope": {
            "reviewed_closed_set_precision": "relevant / labeled selected candidates; unknown excluded",
            "pooled_known_relevant_retention": "selected relevant / 23 known relevant query-candidate labels",
            "gold_section_candidate_pool_coverage": "gold sections present anywhere in the non-future merged candidate pool; proxy, not human Recall",
            "latency": "real elapsed time around each multi-query retrieval and rerank run",
        },
        "v1": {"metrics": v1_metrics, "queries": v1_runs},
        "v2": {"metrics": v2_metrics, "queries": v2_runs},
        "new_candidate_review": diff["summary"],
        "optimistic_unknown_upper_bound": {
            "assumption": "every V2 unlabeled selection is relevant",
            "reviewed_relevant_selected": v2_metrics["known_relevant_selected"],
            "unlabeled_selected": raw_new_candidate_count,
            "pooled_relevant_total_if_all_unknown_relevant": optimistic_pool_total,
            "maximum_pooled_known_relevant_retention": optimistic_retention,
            "can_reach_0_90_retention_gate": optimistic_retention >= 0.90,
        },
        "gates": gates,
        "all_release_gates_passed": all(gates.values()),
        "decision": "remain_shadow",
        "limitations": [
            "The planner uses only the recorded writing request and requested intent types; must_recall_facts are never passed into planning or scoring.",
            "Gold-section candidate-pool coverage is a section proxy and is not reported as true Recall.",
            "Codex-assisted labels are diagnostic and cannot satisfy the independent-human precision gate.",
        ],
    }
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.diff_output.write_text(json.dumps(diff, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "output": str(args.output),
        "v1_metrics": v1_metrics,
        "v2_metrics": v2_metrics,
        "new_candidate_review": diff["summary"],
        "gates": gates,
        "decision": report["decision"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
