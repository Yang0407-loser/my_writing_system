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
