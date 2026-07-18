"""Run legacy and Phase 3 shadow retrieval on the fixed 10-query RAG set."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from app.retrieval_pipeline import QueryPlanner, ShadowRetriever
from app.vector_store import VectorStore
from tests.quality.baseline import DEFAULT_RAG, compute_rag_metrics, load_json
from tests.quality.phase3_retrieval_eval import (
    manual_label_metrics,
    section_proxy_metrics,
    text_hash,
)


CHARACTER_NAMES = ("林晚", "周野", "季晴", "顾衍", "吴阿姨")


def _legacy_run(store: VectorStore, entry: dict, task_id: str) -> dict:
    started = time.perf_counter()
    items = store.search_with_meta(entry["query"], k=5, task_id=task_id)
    elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
    return {
        "query_index": entry["query_index"],
        "selected_sections": [int(item.get("section") or 0) for item in items],
        "selected_text_hashes": [text_hash(item.get("text", "")) for item in items],
        "candidate_count": store.last_search_trace.get("candidate_count", len(items)),
        "estimated_context_tokens": sum(len(str(item.get("text", ""))) for item in items) // 4,
        "elapsed_ms": elapsed_ms,
    }


def _shadow_run(store: VectorStore, entry: dict, task_id: str, args) -> dict:
    plan = QueryPlanner(max_queries=args.max_queries).plan_text(
        entry["query"],
        requested_intents=entry["query_intent"],
        character_names=CHARACTER_NAMES,
        current_section=int(entry["section"]),
        current_subsection=int(entry["subsection"]),
    )
    result = ShadowRetriever(
        candidate_k=args.candidate_k,
        min_score=args.min_score,
        max_results=5,
    ).run(store, plan, task_id=task_id)
    selected_traces = [
        item for item in result["rerank"]["candidates"] if item["selected"]
    ]
    return {
        "query_index": entry["query_index"],
        "plan": result["plan"],
        "selected_sections": result["selected_sections"],
        "selected_ids": result["selected_ids"],
        "selected_text_hashes": [item["text_hash"] for item in selected_traces],
        "candidate_count": result["merged_candidate_count"],
        "estimated_context_tokens": result["estimated_context_tokens"],
        "elapsed_ms": result["elapsed_ms"],
        "candidate_trace": result["rerank"]["candidates"],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--annotations", type=Path, default=DEFAULT_RAG)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--max-queries", type=int, default=4)
    parser.add_argument("--candidate-k", type=int, default=12)
    parser.add_argument("--min-score", type=float, default=0.35)
    args = parser.parse_args()

    annotation = load_json(args.annotations)
    entries = annotation["entries"]
    task_id = annotation["task_id"]
    store = VectorStore()
    legacy_runs = [_legacy_run(store, entry, task_id) for entry in entries]
    shadow_runs = [_shadow_run(store, entry, task_id, args) for entry in entries]
    manual_baseline = compute_rag_metrics(annotation)
    legacy_proxy = section_proxy_metrics(entries, legacy_runs)
    shadow_proxy = section_proxy_metrics(entries, shadow_runs)
    shadow_manual = manual_label_metrics(entries, shadow_runs)
    explainable = all(
        candidate.get("id") and candidate.get("reason") and candidate.get("score_components")
        for run in shadow_runs for candidate in run["candidate_trace"]
    )

    gates = {
        "precision_at_5_not_below_0_68": (
            shadow_manual["production_gate_valid"]
            and shadow_manual["precision_at_5_on_labeled"] is not None
            and shadow_manual["precision_at_5_on_labeled"] >= 0.68
        ),
        "recall_at_5_above_0_6667": (
            shadow_proxy["recall_at_5_section_proxy"] is not None
            and shadow_proxy["recall_at_5_section_proxy"] > 0.6667
        ),
        "late_precision_above_0_40": (
            shadow_proxy["late_chapter_precision_at_5_section_proxy"] is not None
            and shadow_proxy["late_chapter_precision_at_5_section_proxy"] > 0.40
        ),
        "writer_uses_legacy": True,
        "all_candidates_explainable": explainable,
    }
    report = {
        "profile": {
            "max_queries": args.max_queries,
            "candidate_k": args.candidate_k,
            "min_score": args.min_score,
            "collection_strategy": "shared_collection_task_id_filter",
            "writer_uses": "legacy",
        },
        "manual_baseline": manual_baseline,
        "legacy_live_section_proxy": legacy_proxy,
        "phase3_shadow_section_proxy": shadow_proxy,
        "phase3_shadow_manual_label_reuse": shadow_manual,
        "gates": gates,
        "all_gates_passed": all(gates.values()),
        "production_switched": False,
        "decision": (
            "eligible_for_batch_2_not_production_switch"
            if all(gates.values())
            else "remain_shadow"
        ),
        "limitations": [
            "Section-proxy metrics use human gold sections but do not prove that every new chunk is relevant.",
            "New candidates outside the recorded legacy top-5 require human labeling before the precision gate is valid.",
            "This benchmark uses the existing embedding model, chunks and shared task-filtered collection without modification.",
        ],
        "queries": shadow_runs,
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
        print(json.dumps({
            "output": str(args.output),
            "legacy_live_section_proxy": legacy_proxy,
            "phase3_shadow_section_proxy": shadow_proxy,
            "phase3_shadow_manual_label_reuse": shadow_manual,
            "gates": gates,
            "decision": report["decision"],
        }, ensure_ascii=False, indent=2))
    else:
        print(rendered)


if __name__ == "__main__":
    main()
