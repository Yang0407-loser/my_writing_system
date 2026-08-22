"""Phase 3 Batch 2A: offline trace-replay ablation without production changes."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import chromadb
from chromadb.config import Settings as ChromaSettings

from app.config import settings
from tests.quality.baseline import DEFAULT_RAG, ROOT, load_json
from tests.quality.phase3_ablation import (
    configuration_grid,
    evaluate_configuration,
    pareto_frontier,
    training_score,
)


DEFAULT_TRACE = ROOT / "reports" / "phase3-shadow-retrieval.json"
DEFAULT_REVIEW = ROOT / "tests" / "quality" / "phase3_shadow_candidates_review.json"
DEFAULT_OUTPUT = ROOT / "reports" / "phase3-batch2a-ablation.json"
DEFAULT_DIFF = ROOT / "tests" / "quality" / "phase3_batch2_new_candidates_review.json"


def _review_has_human_work(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        review = load_json(path)
    except (json.JSONDecodeError, OSError, TypeError):
        return True
    return any(
        candidate.get("human_relevant")
        or candidate.get("supports_which_fact")
        or str(candidate.get("review_note", "")).strip()
        for candidate in review.get("candidates", [])
    )


def _decode_list(value) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if not isinstance(value, str) or not value.strip():
        return []
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return [part.strip() for part in value.split(",") if part.strip()]
    return [str(item) for item in decoded] if isinstance(decoded, list) else []


def _load_chroma_documents(source_ids: list[str]) -> dict[str, dict]:
    client = chromadb.PersistentClient(
        path=settings.CHROMA_DATA_PATH,
        settings=ChromaSettings(anonymized_telemetry=False),
    )
    collection = client.get_collection("writing_paragraphs")
    result = collection.get(ids=source_ids, include=["documents", "metadatas"])
    ids = result.get("ids", [])
    documents = result.get("documents", []) or []
    metadatas = result.get("metadatas", []) or []
    return {
        str(source_id): {
            "text": str(documents[index]),
            "metadata": dict(metadatas[index] or {}),
        }
        for index, source_id in enumerate(ids)
    }


def _graded_character_score(candidate: dict, document: dict, query: dict) -> float:
    names = {
        str(name)
        for planned in query["plan"]["queries"]
        for name in planned.get("characters", [])
        if str(name).strip()
    }
    if not names:
        return 0.0
    metadata_names = set(_decode_list(document["metadata"].get("characters")))
    if names & metadata_names:
        return round(len(names & metadata_names) / len(names), 6)
    title = str(candidate.get("title", ""))
    if any(name in title for name in names):
        return 0.75
    text = document["text"]
    mentions = sum(text.count(name) for name in names)
    if mentions >= 2:
        return 0.5
    if mentions == 1:
        return 0.25
    return 0.0


def prepare_inputs(trace: dict, review: dict, annotation: dict) -> tuple[list[dict], dict, dict]:
    annotation_map = {
        int(entry["query_index"]): entry for entry in annotation["entries"]
    }
    source_ids = sorted({
        str(candidate["id"])
        for query in trace["queries"]
        for candidate in query["candidate_trace"]
        if candidate.get("id")
    })
    documents = _load_chroma_documents(source_ids)
    missing = sorted(set(source_ids) - set(documents))
    if missing:
        raise ValueError(f"candidate IDs missing from Chroma: {missing}")

    queries = []
    for raw_query in trace["queries"]:
        query = dict(raw_query)
        query_index = int(query["query_index"])
        query["current_section"] = int(annotation_map[query_index].get("section", 0))
        enriched = []
        for raw_candidate in query["candidate_trace"]:
            candidate = dict(raw_candidate)
            document = documents[str(candidate["id"])]
            candidate["token_estimate"] = (len(document["text"]) + 3) // 4
            candidate["graded_character_score"] = _graded_character_score(
                candidate, document, query
            )
            enriched.append(candidate)
        query["candidate_trace"] = enriched
        queries.append(query)

    labels = {
        (int(group["query_index"]), str(candidate["source_id"])): candidate["human_relevant"]
        for group in review["queries"]
        for candidate in group["candidates"]
    }
    return queries, labels, documents


def _compact_result(result: dict) -> dict:
    return {key: value for key, value in result.items() if key != "selections"}


def _choose_pareto(frontier: list[dict]) -> list[dict]:
    eligible = [
        result for result in frontier
        if (result["known_relevant_retention"] or 0.0) >= 0.70
    ]
    if not eligible:
        eligible = frontier
    rankings = [
        lambda result: (
            training_score(result), result["closed_set_precision"],
            result["known_relevant_retention"], -result["unlabeled_selected"],
        ),
        lambda result: (
            result["closed_set_precision"], result["known_relevant_retention"],
            -result["unlabeled_selected"], -result["mean_token_estimate"],
        ),
        lambda result: (
            result["known_relevant_retention"], result["closed_set_precision"],
            -result["mean_token_estimate"], -result["mean_latency_estimate_ms"],
        ),
    ]
    chosen = []
    seen = set()
    for ranking in rankings:
        candidate = max(eligible, key=ranking)
        config_id = candidate["config"]["config_id"]
        if config_id not in seen:
            chosen.append(candidate)
            seen.add(config_id)
        if len(chosen) == 3:
            break
    return chosen


def _leave_one_query_out(queries: list[dict], labels: dict, configs: list[dict]) -> dict:
    folds = []
    aggregate_relevant = 0
    aggregate_irrelevant = 0
    aggregate_unknown = 0
    aggregate_known_total = 0
    all_indices = {int(query["query_index"]) for query in queries}
    for held_out in sorted(all_indices):
        train_indices = all_indices - {held_out}
        train_results = [
            evaluate_configuration(
                queries, labels, config, query_indices=train_indices
            )
            for config in configs
        ]
        eligible = [
            result for result in train_results
            if (result["known_relevant_retention"] or 0.0) >= 0.75
            and result["selected_candidates"] > 0
        ] or train_results
        winner = max(
            eligible,
            key=lambda result: (
                training_score(result),
                result["closed_set_precision"] or 0.0,
                result["known_relevant_retention"] or 0.0,
                -result["unlabeled_selected"],
            ),
        )
        held = evaluate_configuration(
            queries, labels, winner["config"], query_indices={held_out}
        )
        known_total = sum(
            label == "相关"
            for (query_index, _), label in labels.items()
            if query_index == held_out
        )
        aggregate_relevant += held["known_relevant_selected"]
        aggregate_irrelevant += held["known_irrelevant_selected"]
        aggregate_unknown += held["unlabeled_selected"]
        aggregate_known_total += known_total
        folds.append({
            "held_out_query": held_out,
            "selected_config": winner["config"],
            "training_score": training_score(winner),
            "held_out": _compact_result(held),
        })
    labeled = aggregate_relevant + aggregate_irrelevant
    return {
        "method": "select on nine queries with a retention floor of 75%, evaluate on the held-out query",
        "folds": folds,
        "aggregate": {
            "known_relevant_selected": aggregate_relevant,
            "known_irrelevant_selected": aggregate_irrelevant,
            "unlabeled_selected": aggregate_unknown,
            "closed_set_precision": (
                round(aggregate_relevant / labeled, 4) if labeled else None
            ),
            "known_relevant_retention": (
                round(aggregate_relevant / aggregate_known_total, 4)
                if aggregate_known_total else None
            ),
        },
    }


def _build_diff_review(
    chosen: list[dict],
    labels: dict,
    documents: dict,
    annotation: dict,
) -> dict:
    annotations = {
        int(entry["query_index"]): entry for entry in annotation["entries"]
    }
    items: dict[tuple[int, str], dict] = {}
    for result in chosen:
        config_id = result["config"]["config_id"]
        for selected in result["selections"]:
            key = (int(selected["query_index"]), str(selected["source_id"]))
            if key in labels:
                continue
            source = annotations[key[0]]
            document = documents[key[1]]
            item = items.setdefault(key, {
                "review_item_id": f"q{key[0]:02d}-{key[1][:8]}",
                "query_index": key[0],
                "query": source["query"],
                "query_intent": source["query_intent"],
                "must_recall_facts": source["must_recall_facts"],
                "source_id": key[1],
                "section": selected["section"],
                "subsection": selected["subsection"],
                "title": selected["title"],
                "evidence_text": document["text"],
                "selected_by_configs": [],
                "human_relevant": "",
                "supports_which_fact": [],
                "review_note": "",
            })
            item["selected_by_configs"].append({
                "config_id": config_id,
                "final_score": selected["final_score"],
            })
    ordered = [items[key] for key in sorted(items)]
    return {
        "schema_version": 1,
        "purpose": "Phase 3 Batch 2A Pareto 配置相对既有 38 条标签的新进入候选",
        "review_instructions": {
            "human_relevant": "人工填写：相关 / 不相关 / 无法判断",
            "supports_which_fact": "相关时选择被正文完整支持的 must_recall_facts",
            "review_note": "填写简短依据",
        },
        "summary": {
            "candidate_count": len(ordered),
            "human_reviewed_count": 0,
            "status": "awaiting_targeted_review" if ordered else "no_new_candidates",
        },
        "candidates": ordered,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trace", type=Path, default=DEFAULT_TRACE)
    parser.add_argument("--review", type=Path, default=DEFAULT_REVIEW)
    parser.add_argument("--annotations", type=Path, default=DEFAULT_RAG)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--diff-output", type=Path, default=DEFAULT_DIFF)
    parser.add_argument("--force-review-overwrite", action="store_true")
    args = parser.parse_args()

    if _review_has_human_work(args.diff_output) and not args.force_review_overwrite:
        raise FileExistsError(
            f"refusing to overwrite human review data in {args.diff_output}; "
            "pass --force-review-overwrite only after preserving those labels"
        )

    trace = load_json(args.trace)
    review = load_json(args.review)
    annotation = load_json(args.annotations)
    queries, labels, documents = prepare_inputs(trace, review, annotation)
    configs = configuration_grid()
    results = [
        evaluate_configuration(queries, labels, config) for config in configs
    ]
    baseline_config = {
        "config_id": "current-baseline",
        "intent_variant": "all",
        "character_weight_factor": 1.0,
        "character_mode": "binary",
        "min_score": 0.35,
        "max_queries": 4,
        "max_results": 5,
        "duplicate_penalty": 0.08,
        "token_budget": None,
    }
    baseline = evaluate_configuration(queries, labels, baseline_config)
    frontier = pareto_frontier(results)
    chosen = _choose_pareto(frontier)
    loo = _leave_one_query_out(queries, labels, configs)
    diff_review = _build_diff_review(chosen, labels, documents, annotation)
    report = {
        "schema_version": 1,
        "mode": "offline_trace_replay",
        "production_changed": False,
        "writer_changed": False,
        "grid": {
            "configurations": len(configs),
            "intent_variants": ["all", "no_scene", "no_character"],
            "character_weight_factors": [1.0, 0.75, 0.5, 0.25],
            "character_modes": ["binary", "graded"],
            "min_scores": [0.35, 0.40, 0.45, 0.50],
            "max_queries": [2, 3, 4],
            "max_results": [3, 4, 5],
            "duplicate_penalties": [0.04, 0.08, 0.12],
            "token_budgets": [None, 400, 600, 800],
        },
        "metric_scope": {
            "closed_set_precision": "known relevant / all labeled selections; unlabeled candidates excluded from this denominator",
            "known_relevant_retention": "known relevant selections / the 21 known relevant candidates",
            "unlabeled_selected": "reported separately and never treated as irrelevant",
            "latency": "estimated by retained query count ratio; not a live timing measurement",
        },
        "baseline": _compact_result(baseline),
        "pareto_frontier_outcomes": len(frontier),
        "selected_pareto_configs": [_compact_result(result) for result in chosen],
        "leave_one_query_out": loo,
        "new_candidate_review": diff_review["summary"],
        "limitations": [
            "Intent removal and max_queries filter candidates by frozen matched_intents; keyword/title components are not re-embedded or recomputed.",
            "The graded character score uses metadata first, then title/text mention strength; it is an offline proposal, not production behavior.",
            "Closed-set metrics cannot establish true recall because candidates outside the existing 38 labels remain unlabeled.",
            "Token counts use exact Chroma text lengths; latency is a proportional estimate from the frozen shadow run.",
        ],
        "decision": "await_targeted_review" if diff_review["summary"]["candidate_count"] else "remain_shadow_no_new_review",
    }
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    args.diff_output.write_text(
        json.dumps(diff_review, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({
        "output": str(args.output),
        "grid_configurations": len(configs),
        "baseline": _compact_result(baseline),
        "pareto": [_compact_result(result) for result in chosen],
        "loo_aggregate": loo["aggregate"],
        "new_candidate_review": diff_review["summary"],
        "decision": report["decision"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
