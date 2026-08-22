"""Deterministic metrics for comparing legacy and Phase 3 retrieval outputs."""

from __future__ import annotations

import hashlib
from statistics import mean


def text_hash(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def section_proxy_metrics(entries: list[dict], runs: list[dict]) -> dict:
    """Evaluate selected sections against human-authored gold section sets."""
    by_index = {int(run["query_index"]): run for run in runs}
    selected_count = 0
    relevant_count = 0
    recall_hits = 0
    recall_gold = 0
    late_selected = 0
    late_relevant = 0
    latencies = []
    candidates = []
    token_estimates = []

    for entry in entries:
        run = by_index[int(entry["query_index"])]
        sections = [int(section) for section in run.get("selected_sections", [])]
        gold = {int(section) for section in entry["gold_sections"]}
        selected_count += len(sections)
        relevant_count += sum(section in gold for section in sections)
        recall_hits += len(set(sections) & gold)
        recall_gold += len(gold)
        if int(entry.get("section", 0)) >= 13:
            late_selected += len(sections)
            late_relevant += sum(section in gold for section in sections)
        latencies.append(float(run.get("elapsed_ms", 0.0)))
        candidates.append(int(run.get("candidate_count", 0)))
        token_estimates.append(int(run.get("estimated_context_tokens", 0)))

    return {
        "queries": len(entries),
        "precision_at_5_section_proxy": round(relevant_count / selected_count, 4) if selected_count else None,
        "recall_at_5_section_proxy": round(recall_hits / recall_gold, 4) if recall_gold else None,
        "late_chapter_precision_at_5_section_proxy": round(late_relevant / late_selected, 4) if late_selected else None,
        "selected_candidates": selected_count,
        "mean_selected_per_query": round(selected_count / len(entries), 3) if entries else 0.0,
        "mean_candidate_pool": round(mean(candidates), 3) if candidates else 0.0,
        "mean_latency_ms": round(mean(latencies), 3) if latencies else 0.0,
        "mean_estimated_context_tokens": round(mean(token_estimates), 3) if token_estimates else 0.0,
    }


def manual_label_metrics(entries: list[dict], runs: list[dict]) -> dict:
    """Reuse exact old-candidate labels when a new result has identical text.

    Results outside the original top-5 are intentionally left unlabeled.  A
    production decision is invalid until coverage reaches 100% through a new
    human pass.
    """
    entry_map = {int(entry["query_index"]): entry for entry in entries}
    labeled = 0
    relevant = 0
    total = 0
    for run in runs:
        entry = entry_map[int(run["query_index"])]
        labels = {
            text_hash(item.get("text", "")): item.get("human_relevant") == "相关"
            for item in entry.get("items", [])
        }
        for digest in run.get("selected_text_hashes", []):
            total += 1
            if digest in labels:
                labeled += 1
                relevant += int(labels[digest])
    return {
        "selected_candidates": total,
        "human_labeled_candidates": labeled,
        "human_label_coverage": round(labeled / total, 4) if total else 0.0,
        "precision_at_5_on_labeled": round(relevant / labeled, 4) if labeled else None,
        "production_gate_valid": bool(total) and labeled == total,
    }


def human_review_metrics(annotation: dict, review: dict) -> dict:
    """Compute human metrics without mixing section and fact denominators.

    ``section_recall_at_5`` is comparable to the legacy 66.7% recall baseline:
    for each query it counts unique gold sections represented by candidates a
    human marked relevant.  ``fact_coverage_recall`` is a separate, stricter
    diagnostic based only on explicitly selected ``supports_which_fact`` values.
    """
    annotations = {
        int(entry["query_index"]): entry for entry in annotation["entries"]
    }
    groups = {int(group["query_index"]): group for group in review["queries"]}
    selected_total = 0
    relevant_total = 0
    late_selected = 0
    late_relevant = 0
    gold_section_total = 0
    gold_section_hits = 0
    fact_total = 0
    fact_hits = 0
    per_query = []

    for query_index in sorted(annotations):
        source = annotations[query_index]
        candidates = list(groups[query_index]["candidates"])
        relevant = [
            candidate for candidate in candidates
            if candidate["human_relevant"] == "相关"
        ]
        gold_sections = {int(section) for section in source["gold_sections"]}
        relevant_sections = {int(candidate["section"]) for candidate in relevant}
        section_hits = relevant_sections & gold_sections
        must_facts = list(source["must_recall_facts"])
        supported_facts = {
            fact
            for candidate in relevant
            for fact in candidate["supports_which_fact"]
        }
        supported_facts &= set(must_facts)
        is_late = int(source.get("section", 0)) >= 13

        selected_total += len(candidates)
        relevant_total += len(relevant)
        gold_section_total += len(gold_sections)
        gold_section_hits += len(section_hits)
        fact_total += len(must_facts)
        fact_hits += len(supported_facts)
        if is_late:
            late_selected += len(candidates)
            late_relevant += len(relevant)

        per_query.append({
            "query_index": query_index,
            "current_section": int(source.get("section", 0)),
            "selected_candidates": len(candidates),
            "relevant_candidates": len(relevant),
            "human_precision_at_5": (
                round(len(relevant) / len(candidates), 4) if candidates else None
            ),
            "gold_section_hits": len(section_hits),
            "gold_sections": len(gold_sections),
            "section_recall_at_5": (
                round(len(section_hits) / len(gold_sections), 4)
                if gold_sections else None
            ),
            "supported_facts": len(supported_facts),
            "must_recall_facts": len(must_facts),
            "fact_coverage_recall": (
                round(len(supported_facts) / len(must_facts), 4)
                if must_facts else None
            ),
            "supported_fact_values": [
                fact for fact in must_facts if fact in supported_facts
            ],
            "zero_result": not candidates,
            "late_query": is_late,
        })

    return {
        "formulas": {
            "human_precision_at_5": "human-relevant selected candidates / all selected candidates",
            "section_recall_at_5": "unique gold sections represented by human-relevant candidates / all gold sections",
            "fact_coverage_recall": "unique must_recall_facts explicitly supported / all must_recall_facts",
            "late_chapter_human_precision_at_5": "human-relevant selected candidates in queries with current_section >= 13 / all selected candidates in those queries",
        },
        "queries": len(annotations),
        "selected_candidates": selected_total,
        "human_relevant_candidates": relevant_total,
        "human_precision_at_5": (
            round(relevant_total / selected_total, 4) if selected_total else None
        ),
        "gold_section_hits": gold_section_hits,
        "gold_sections": gold_section_total,
        "section_recall_at_5": (
            round(gold_section_hits / gold_section_total, 4)
            if gold_section_total else None
        ),
        "supported_facts": fact_hits,
        "must_recall_facts": fact_total,
        "fact_coverage_recall": (
            round(fact_hits / fact_total, 4) if fact_total else None
        ),
        "late_selected_candidates": late_selected,
        "late_human_relevant_candidates": late_relevant,
        "late_chapter_human_precision_at_5": (
            round(late_relevant / late_selected, 4) if late_selected else None
        ),
        "zero_result_queries": [
            item["query_index"] for item in per_query if item["zero_result"]
        ],
        "per_query": per_query,
    }


def human_review_failure_observations(review: dict) -> dict:
    """Describe observed failure layers without claiming causal proof."""
    candidates = [
        candidate for group in review["queries"] for candidate in group["candidates"]
    ]
    false_positives = [
        candidate for candidate in candidates
        if candidate["human_relevant"] == "不相关"
    ]
    partial_fact_candidates = [
        candidate for candidate in candidates
        if candidate["human_relevant"] == "相关"
        and not candidate["supports_which_fact"]
    ]
    generic_intent_false_positives = [
        candidate for candidate in false_positives
        if {"character", "scene"} & set(candidate["matched_intents"])
    ]
    character_dominant_false_positives = [
        candidate for candidate in false_positives
        if candidate["score_components"].get("character") == 1.0
    ]
    zero_groups = [
        int(group["query_index"])
        for group in review["queries"] if not group["candidates"]
    ]
    return {
        "query_planner_intent_deviation": {
            "status": "inference_not_causal_proof",
            "candidate_count": len(generic_intent_false_positives),
            "query_indices": sorted({
                int(candidate["query_index"])
                for candidate in generic_intent_false_positives
            }),
            "observation": "false positives matched broad character or scene intents; an intent ablation is required to prove planner causality",
        },
        "vector_coarse_recall_deviation": {
            "status": "observed_false_positives_entered_candidate_pool",
            "candidate_count": len(false_positives),
            "query_indices": sorted({
                int(candidate["query_index"]) for candidate in false_positives
            }),
            "observation": "irrelevant chunks entered the coarse pool; unselected candidates are not human-labeled, so missing-fact recall cannot yet be causally assigned to coarse recall",
        },
        "rule_rerank_deviation": {
            "status": "observed_selected_false_positives",
            "selected_false_positives": len(false_positives),
            "character_score_one": len(character_dominant_false_positives),
            "observation": "all listed false positives passed threshold and final selection; character overlap saturation is reported as correlation, not proof",
        },
        "partial_fact_support": {
            "status": "observed",
            "candidate_count": len(partial_fact_candidates),
            "query_indices": sorted({
                int(candidate["query_index"])
                for candidate in partial_fact_candidates
            }),
            "observation": "human-relevant chunks provide event context but explicitly support no complete must-recall fact",
        },
        "zero_result_queries": {
            "status": "observed",
            "query_indices": zero_groups,
            "count": len(zero_groups),
        },
        "false_positive_items": [
            {
                "review_item_id": candidate["review_item_id"],
                "query_index": int(candidate["query_index"]),
                "source_id": candidate["source_id"],
                "matched_intents": candidate["matched_intents"],
                "final_score": candidate["final_score"],
                "selection_reason": candidate["selection_reason"],
            }
            for candidate in false_positives
        ],
    }
