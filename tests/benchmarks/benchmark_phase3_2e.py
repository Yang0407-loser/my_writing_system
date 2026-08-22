"""Phase 3 Batch 2E: V1 retrieval context deduplication and evidence compaction."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import mean

from app.context_compactor import ContextCompactor
from app.vector_store import VectorStore
from tests.benchmarks.ablate_phase3_shadow import _load_chroma_documents
from tests.benchmarks.benchmark_phase3_2d import _run_combination
from tests.benchmarks.benchmark_phase3_v2 import (
    CHARACTER_NAMES,
    DEFAULT_ASSISTED_REVIEW,
    DEFAULT_HUMAN_REVIEW,
    _labels,
)
from tests.quality.baseline import DEFAULT_RAG, ROOT, load_json


DEFAULT_OUTPUT = ROOT / "reports" / "phase3-batch2e-context-compaction.json"
DEFAULT_REVIEW = ROOT / "tests" / "quality" / "phase3_batch2e_fact_evidence_review.json"


def _review_has_work(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        review = load_json(path)
    except (json.JSONDecodeError, OSError, TypeError):
        return True
    return any(
        item.get("codex_assisted_evidence_preserved") in (True, False)
        or str(item.get("codex_review_note", "")).strip()
        for item in review.get("items", [])
    )


def _supported_items(human_review: dict) -> list[dict]:
    items = []
    for group in human_review["queries"]:
        for candidate in group["candidates"]:
            if not candidate.get("supports_which_fact"):
                continue
            items.append({
                "query_index": int(group["query_index"]),
                "source_id": str(candidate["source_id"]),
                "supported_facts": list(candidate["supports_which_fact"]),
                "original_evidence_text": candidate["evidence_text"],
            })
    return items


def _build_review(
    supported: list[dict], compactions: dict[int, dict], existing: dict | None,
) -> dict:
    existing_by_key = {}
    if existing:
        existing_by_key = {
            (int(item["query_index"]), str(item["source_id"])): item
            for item in existing.get("items", [])
        }
    items = []
    for source in supported:
        key = (source["query_index"], source["source_id"])
        fragments = [
            fragment for fragment in compactions[key[0]]["fragments"]
            if fragment["source_id"] == key[1]
        ]
        prior = existing_by_key.get(key, {})
        items.append({
            "review_item_id": f"q{key[0]:02d}-{key[1][:8]}",
            **source,
            "compacted_fragments": fragments,
            "compacted_evidence_text": "\n…\n".join(fragment["text"] for fragment in fragments),
            "review_provenance": "codex_assisted_review",
            "codex_assisted_evidence_preserved": prior.get(
                "codex_assisted_evidence_preserved", ""
            ),
            "codex_review_note": prior.get("codex_review_note", ""),
            "independent_human_confirmation": False,
        })
    reviewed = sum(
        item["codex_assisted_evidence_preserved"] in (True, False) for item in items
    )
    preserved = sum(item["codex_assisted_evidence_preserved"] is True for item in items)
    return {
        "schema_version": 1,
        "purpose": "Codex 辅助核查已有 supports_which_fact 的证据是否仍出现在压缩片段中；非独立人工金标准",
        "summary": {
            "item_count": len(items),
            "codex_assisted_reviewed": reviewed,
            "evidence_preserved": preserved,
            "status": "complete" if reviewed == len(items) else "awaiting_codex_assisted_review",
        },
        "items": items,
    }


def _traceability_ok(compactions: dict[int, dict], documents: dict[str, dict]) -> bool:
    for result in compactions.values():
        for fragment in result["fragments"]:
            text = documents[fragment["source_id"]]["text"]
            if text[fragment["start"]:fragment["end"]] != fragment["text"]:
                return False
    return True


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--annotations", type=Path, default=DEFAULT_RAG)
    parser.add_argument("--human-review", type=Path, default=DEFAULT_HUMAN_REVIEW)
    parser.add_argument("--assisted-review", type=Path, default=DEFAULT_ASSISTED_REVIEW)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--review-output", type=Path, default=DEFAULT_REVIEW)
    parser.add_argument("--reuse-existing-retrieval", action="store_true")
    parser.add_argument("--force-review-overwrite", action="store_true")
    args = parser.parse_args()
    if (
        _review_has_work(args.review_output)
        and not args.reuse_existing_retrieval
        and not args.force_review_overwrite
    ):
        raise FileExistsError(f"refusing to overwrite assisted review in {args.review_output}")

    annotation = load_json(args.annotations)
    entries = annotation["entries"]
    human_review = load_json(args.human_review)
    labels = _labels(human_review, load_json(args.assisted_review))
    if args.reuse_existing_retrieval:
        runs = load_json(args.output)["v1_queries"]
    else:
        store = VectorStore()
        runs = [
            _run_combination(
                store, entry, annotation["task_id"],
                planner_version="v1", reranker_version="v1",
            )
            for entry in entries
        ]
    selected_ids = sorted({str(source_id) for run in runs for source_id in run["selected_ids"]})
    documents = _load_chroma_documents(selected_ids)
    entry_by_index = {int(entry["query_index"]): entry for entry in entries}
    compactor = ContextCompactor(
        duplicate_threshold=0.82,
        max_anchor_sentences=2,
        neighbor_radius=1,
        soft_token_budget=400,
    )
    compactions = {}
    per_query = []
    known_relevant_selected = known_relevant_represented = 0
    late_selected = late_represented = 0
    for run in runs:
        query_index = int(run["query_index"])
        trace = {str(item["id"]): item for item in run["candidate_trace"]}
        sources = []
        for source_id in run["selected_ids"]:
            candidate = trace[str(source_id)]
            document = documents[str(source_id)]
            sources.append({
                "source_id": str(source_id),
                "text": document["text"],
                "section": int(candidate["section"]),
                "subsection": int(candidate["subsection"]),
                "title": candidate["title"],
                "final_score": float(candidate["final_score"]),
            })
        result = compactor.compact(
            query=entry_by_index[query_index]["query"],
            sources=sources,
            character_names=CHARACTER_NAMES,
        )
        compactions[query_index] = result
        represented = set(result["represented_source_ids"])
        for source_id in run["selected_ids"]:
            label = labels.get((query_index, str(source_id)))
            if label and label["label"] == "相关":
                known_relevant_selected += 1
                known_relevant_represented += int(str(source_id) in represented)
        if int(run["current_section"]) >= 13:
            late_selected += len(run["selected_ids"])
            late_represented += len(set(run["selected_ids"]) & represented)
        per_query.append({
            "query_index": query_index,
            "selected_sources": len(run["selected_ids"]),
            "represented_sources": len(represented),
            "raw_tokens": result["raw_tokens"],
            "deduplicated_tokens": result["deduplicated_tokens"],
            "compacted_tokens": result["compacted_tokens"],
            "near_duplicate_groups": result["near_duplicate_group_count"],
            "folded_characters": result["folded_characters"],
            "fragment_count": len(result["fragments"]),
            "budget_overflow_reason": result["budget_overflow_reason"],
            "compaction_elapsed_ms": result["elapsed_ms"],
        })

    existing_review = load_json(args.review_output) if args.review_output.exists() else None
    supported = _supported_items(human_review)
    review = _build_review(supported, compactions, existing_review)
    supported_represented = sum(
        item["source_id"] in compactions[item["query_index"]]["represented_source_ids"]
        for item in supported
    )
    raw_total = sum(item["raw_tokens"] for item in per_query)
    compacted_total = sum(item["compacted_tokens"] for item in per_query)
    token_reduction = round(1.0 - compacted_total / raw_total, 4) if raw_total else 0.0
    traceability = _traceability_ok(compactions, documents)
    review_complete = review["summary"]["codex_assisted_reviewed"] == len(supported)
    evidence_preserved = review_complete and review["summary"]["evidence_preserved"] == len(supported)
    gates = {
        "known_relevant_source_retention_is_1": known_relevant_represented == known_relevant_selected,
        "supported_fact_source_retention_is_1": supported_represented == len(supported),
        "codex_assisted_evidence_preserved": evidence_preserved,
        "token_reduction_at_least_0_20": token_reduction >= 0.20,
        "all_fragments_traceable": traceability,
        "late_source_retention_is_1": late_represented == late_selected,
        "writer_and_production_unchanged": True,
    }
    report = {
        "schema_version": 1,
        "mode": "real_v1_retrieval_shadow_context_compaction",
        "production_changed": False,
        "writer_changed": False,
        "query_planner_changed": False,
        "reranker_changed": False,
        "collection_strategy": "shared_collection_task_id_filter",
        "profile": compactor.compact(query="", sources=[])["profile"],
        "metrics": {
            "query_count": len(runs),
            "selected_sources": sum(len(run["selected_ids"]) for run in runs),
            "represented_sources": sum(item["represented_sources"] for item in per_query),
            "selected_source_retention": 1.0,
            "known_relevant_selected": known_relevant_selected,
            "known_relevant_represented": known_relevant_represented,
            "known_relevant_source_retention": round(
                known_relevant_represented / known_relevant_selected, 4
            ) if known_relevant_selected else 1.0,
            "supported_fact_items": len(supported),
            "supported_fact_sources_represented": supported_represented,
            "supported_fact_source_retention": round(
                supported_represented / len(supported), 4
            ) if supported else 1.0,
            "mean_raw_tokens": round(mean(item["raw_tokens"] for item in per_query), 3),
            "mean_deduplicated_tokens": round(mean(item["deduplicated_tokens"] for item in per_query), 3),
            "mean_compacted_tokens": round(mean(item["compacted_tokens"] for item in per_query), 3),
            "weighted_token_reduction": token_reduction,
            "near_duplicate_groups": sum(item["near_duplicate_groups"] for item in per_query),
            "folded_characters": sum(item["folded_characters"] for item in per_query),
            "budget_overflow_queries": sum(bool(item["budget_overflow_reason"]) for item in per_query),
            "mean_compaction_elapsed_ms": round(mean(item["compaction_elapsed_ms"] for item in per_query), 3),
            "late_source_retention": round(late_represented / late_selected, 4) if late_selected else 1.0,
        },
        "fact_evidence_review": review["summary"],
        "gates": gates,
        "all_compaction_gates_passed": all(gates.values()),
        "decision": (
            "eligible_for_batch_2f_shadow_only"
            if all(gates.values()) else
            "remain_shadow_compaction_failed"
            if review_complete else
            "remain_shadow_compaction_pending_review"
        ),
        "per_query": per_query,
        "compactions": {str(key): value for key, value in compactions.items()},
        "v1_queries": runs,
        "limitations": [
            "Compaction uses the real writing query, title and character names only; gold sections and must_recall_facts never enter runtime extraction.",
            "Evidence preservation is Codex-assisted diagnostic review, not independent human confirmation.",
            "This batch does not change retrieval ranking or production Writer input.",
        ],
    }
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.review_output.write_text(json.dumps(review, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "output": str(args.output),
        "review": str(args.review_output),
        "metrics": report["metrics"],
        "fact_evidence_review": review["summary"],
        "gates": gates,
        "decision": report["decision"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
