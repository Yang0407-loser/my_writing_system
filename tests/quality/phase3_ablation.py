"""Pure trace-replay utilities for Phase 3 Batch 2A offline ablations."""

from __future__ import annotations

import itertools
from collections import defaultdict


BASE_WEIGHTS = {
    "vector": 0.55,
    "keyword": 0.18,
    "title": 0.10,
    "character": 0.12,
    "chapter_proximity": 0.05,
}


def configuration_grid() -> list[dict]:
    configs = []
    values = itertools.product(
        ("all", "no_scene", "no_character"),
        (1.0, 0.75, 0.5, 0.25),
        ("binary", "graded"),
        (0.35, 0.40, 0.45, 0.50),
        (2, 3, 4),
        (3, 4, 5),
        (0.04, 0.08, 0.12),
        (None, 400, 600, 800),
    )
    for index, value in enumerate(values, 1):
        (
            intent_variant, character_weight_factor, character_mode,
            min_score, max_queries, max_results, duplicate_penalty,
            token_budget,
        ) = value
        configs.append({
            "config_id": f"cfg-{index:05d}",
            "intent_variant": intent_variant,
            "character_weight_factor": character_weight_factor,
            "character_mode": character_mode,
            "min_score": min_score,
            "max_queries": max_queries,
            "max_results": max_results,
            "duplicate_penalty": duplicate_penalty,
            "token_budget": token_budget,
        })
    return configs


def active_intents(query: dict, config: dict) -> list[str]:
    intents = [item["intent"] for item in query["plan"]["queries"]]
    intents = intents[: config["max_queries"]]
    if config["intent_variant"] == "no_scene":
        intents = [intent for intent in intents if intent != "scene"]
    elif config["intent_variant"] == "no_character":
        intents = [intent for intent in intents if intent != "character"]
    return intents


def replay_query(query: dict, config: dict) -> list[dict]:
    intents = set(active_intents(query, config))
    candidates = []
    for candidate in query["candidate_trace"]:
        if candidate["reason"] == "future_section":
            continue
        if not intents.intersection(candidate["matched_intents"]):
            continue
        components = candidate["score_components"]
        character_score = float(components["character"])
        if config["character_mode"] == "graded":
            character_score = float(candidate.get("graded_character_score", character_score))
        score = (
            float(components["vector"]) * BASE_WEIGHTS["vector"]
            + float(components["keyword"]) * BASE_WEIGHTS["keyword"]
            + float(components["title"]) * BASE_WEIGHTS["title"]
            + character_score * BASE_WEIGHTS["character"] * config["character_weight_factor"]
            + float(components["chapter_proximity"]) * BASE_WEIGHTS["chapter_proximity"]
        )
        candidates.append({**candidate, "ablation_base_score": round(score, 6)})

    candidates.sort(
        key=lambda item: (-item["ablation_base_score"], str(item["id"]))
    )
    selected = []
    section_counts: dict[int, int] = defaultdict(int)
    used_tokens = 0
    for candidate in candidates:
        section = int(candidate["section"])
        final_score = max(
            0.0,
            candidate["ablation_base_score"]
            - config["duplicate_penalty"] * section_counts[section],
        )
        if final_score < config["min_score"]:
            continue
        if len(selected) >= config["max_results"]:
            break
        tokens = int(candidate.get("token_estimate", 0))
        if (
            config["token_budget"] is not None
            and used_tokens + tokens > config["token_budget"]
        ):
            continue
        selected.append({
            **candidate,
            "ablation_final_score": round(final_score, 6),
        })
        used_tokens += tokens
        section_counts[section] += 1
    return selected


def evaluate_configuration(
    queries: list[dict],
    labels: dict[tuple[int, str], str],
    config: dict,
    *,
    query_indices: set[int] | None = None,
) -> dict:
    known_relevant_total = sum(
        label == "相关"
        for (query_index, _), label in labels.items()
        if query_indices is None or query_index in query_indices
    )
    selected_count = 0
    known_relevant = 0
    known_irrelevant = 0
    unknown = 0
    tokens = 0
    latency = 0.0
    late_labeled = 0
    late_relevant = 0
    selections = []
    per_query = []

    for query in queries:
        query_index = int(query["query_index"])
        if query_indices is not None and query_index not in query_indices:
            continue
        selected = replay_query(query, config)
        q_relevant = 0
        q_irrelevant = 0
        q_unknown = 0
        q_tokens = sum(int(item.get("token_estimate", 0)) for item in selected)
        for item in selected:
            label = labels.get((query_index, str(item["id"])))
            q_relevant += int(label == "相关")
            q_irrelevant += int(label == "不相关")
            q_unknown += int(label is None)
            selections.append({
                "query_index": query_index,
                "source_id": str(item["id"]),
                "label": label or "unlabeled",
                "section": int(item["section"]),
                "subsection": int(item["subsection"]),
                "title": item["title"],
                "final_score": item["ablation_final_score"],
                "token_estimate": int(item.get("token_estimate", 0)),
            })
        selected_count += len(selected)
        known_relevant += q_relevant
        known_irrelevant += q_irrelevant
        unknown += q_unknown
        tokens += q_tokens
        is_late = int(query.get("current_section", 0)) >= 13
        if is_late:
            late_labeled += q_relevant + q_irrelevant
            late_relevant += q_relevant
        original_query_count = max(len(query["plan"]["queries"]), 1)
        latency += float(query["elapsed_ms"]) * (
            len(active_intents(query, config)) / original_query_count
        )
        per_query.append({
            "query_index": query_index,
            "selected": len(selected),
            "known_relevant": q_relevant,
            "known_irrelevant": q_irrelevant,
            "unlabeled": q_unknown,
            "token_estimate": q_tokens,
        })

    labeled_selected = known_relevant + known_irrelevant
    query_count = len(per_query)
    return {
        "config": config,
        "selected_candidates": selected_count,
        "known_relevant_selected": known_relevant,
        "known_irrelevant_selected": known_irrelevant,
        "unlabeled_selected": unknown,
        "closed_set_precision": (
            round(known_relevant / labeled_selected, 4)
            if labeled_selected else None
        ),
        "known_relevant_retention": (
            round(known_relevant / known_relevant_total, 4)
            if known_relevant_total else None
        ),
        "label_coverage": (
            round(labeled_selected / selected_count, 4) if selected_count else 0.0
        ),
        "late_closed_set_precision": (
            round(late_relevant / late_labeled, 4) if late_labeled else None
        ),
        "mean_selected_per_query": (
            round(selected_count / query_count, 3) if query_count else 0.0
        ),
        "mean_token_estimate": (
            round(tokens / query_count, 3) if query_count else 0.0
        ),
        "mean_latency_estimate_ms": (
            round(latency / query_count, 3) if query_count else 0.0
        ),
        "per_query": per_query,
        "selections": selections,
    }


def training_score(result: dict) -> float:
    precision = result["closed_set_precision"] or 0.0
    retention = result["known_relevant_retention"] or 0.0
    unknown_rate = (
        result["unlabeled_selected"] / result["selected_candidates"]
        if result["selected_candidates"] else 1.0
    )
    token_ratio = min(result["mean_token_estimate"] / 600.0, 1.5)
    return round(
        0.50 * precision + 0.35 * retention
        - 0.10 * unknown_rate - 0.05 * token_ratio,
        6,
    )


def pareto_frontier(results: list[dict]) -> list[dict]:
    """Return nondominated unique outcomes for five explicit objectives."""
    unique: dict[tuple, dict] = {}
    for result in results:
        if result["closed_set_precision"] is None:
            continue
        key = (
            result["closed_set_precision"],
            result["known_relevant_retention"],
            result["unlabeled_selected"],
            result["mean_token_estimate"],
            result["mean_latency_estimate_ms"],
        )
        unique.setdefault(key, result)
    values = list(unique.values())
    frontier = []
    for candidate in values:
        dominated = False
        for other in values:
            if other is candidate:
                continue
            no_worse = (
                other["closed_set_precision"] >= candidate["closed_set_precision"]
                and other["known_relevant_retention"] >= candidate["known_relevant_retention"]
                and other["unlabeled_selected"] <= candidate["unlabeled_selected"]
                and other["mean_token_estimate"] <= candidate["mean_token_estimate"]
                and other["mean_latency_estimate_ms"] <= candidate["mean_latency_estimate_ms"]
            )
            strictly_better = (
                other["closed_set_precision"] > candidate["closed_set_precision"]
                or other["known_relevant_retention"] > candidate["known_relevant_retention"]
                or other["unlabeled_selected"] < candidate["unlabeled_selected"]
                or other["mean_token_estimate"] < candidate["mean_token_estimate"]
                or other["mean_latency_estimate_ms"] < candidate["mean_latency_estimate_ms"]
            )
            if no_worse and strictly_better:
                dominated = True
                break
        if not dominated:
            frontier.append(candidate)
    return frontier
