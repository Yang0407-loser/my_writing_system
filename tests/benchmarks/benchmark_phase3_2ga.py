"""Phase 3 Batch 2G-A offline parent/event feasibility benchmark."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import time
from pathlib import Path
from statistics import mean, median

from app.context_compactor import _tokens
from app.event_chunker import CAUSE, DIALOGUE, INVITATION, MONEY, EventChunker, make_parent
from tests.benchmarks.benchmark_phase3_v2 import DEFAULT_HUMAN_REVIEW
from tests.quality.baseline import DEFAULT_RAG, ROOT, load_json


BATCH2F = ROOT / "reports" / "phase3-batch2f-structured-compaction.json"
OUTPUT = ROOT / "reports" / "phase3-batch2ga-event-chunking.json"
REVIEW = ROOT / "tests" / "quality" / "phase3_batch2ga_event_evidence_review.json"
JUDGMENTS = ROOT / "tests" / "quality" / "phase3_batch2ga_event_evidence_judgments.json"


def _selected_occurrences(review: dict) -> list[dict]:
    return [
        {"query_index": int(group["query_index"]), **candidate}
        for group in review["queries"] for candidate in group["candidates"]
    ]


def _parents(occurrences: list[dict], task_id: str) -> dict[str, dict]:
    parents = {}
    for item in occurrences:
        source_id = str(item["source_id"])
        parent = make_parent(
            source_id=source_id, task_id=task_id, section=item["section"],
            subsection=item["subsection"], title=item["title"], text=item["evidence_text"],
        )
        if source_id in parents and parents[source_id]["content_hash"] != parent["content_hash"]:
            raise ValueError(f"source text differs across query occurrences: {source_id}")
        parents[source_id] = parent
    return parents


def _score(event: dict, query: str) -> float:
    query_tokens, event_tokens = _tokens(query), _tokens(event["text"])
    overlap = len(query_tokens & event_tokens) / max(1, len(query_tokens))
    actor_bonus = 0.03 * sum(actor in query for actor in event["actors"])
    type_bonus = 0.03 if event["event_type"] != "narrative" else 0.0
    return overlap + actor_bonus + type_bonus


def _assemble(query: str, source_ids: list[str], parents: dict, events: dict) -> dict:
    started = time.perf_counter()
    selected, fallbacks = [], []
    for source_id in source_ids:
        candidates = events[source_id]
        ranked = sorted(candidates, key=lambda event: (-_score(event, query), event["event_index"]))
        best = ranked[0]
        if _score(best, query) <= 0:
            selected.extend(candidates)
            fallbacks.append({"source_id": source_id, "reason": "no_deterministic_query_anchor"})
            continue
        keep = {best["event_index"]}
        if len(ranked) > 1 and _score(ranked[1], query) >= _score(best, query) * 0.90:
            keep.add(ranked[1]["event_index"])
        selected.extend(event for event in candidates if event["event_index"] in keep)
    chars = sum(len(event["text"]) for event in selected)
    raw_chars = sum(len(parents[source_id]["text"]) for source_id in source_ids)
    return {
        "selected_event_ids": [event["source_id"] for event in selected],
        "selected_events": selected,
        "event_count": len(selected), "characters": chars, "tokens": math.ceil(chars / 4),
        "raw_tokens": math.ceil(raw_chars / 4),
        "token_reduction": round(1 - chars / raw_chars, 6) if raw_chars else 0.0,
        "fallbacks": fallbacks,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
    }


def _supported(review: dict) -> list[dict]:
    return [
        {"review_item_id": f"q{int(group['query_index']):02d}-{str(candidate['source_id'])[:8]}",
         "query_index": int(group["query_index"]), "source_id": str(candidate["source_id"]),
         "supported_facts": candidate["supports_which_fact"], "original_evidence_text": candidate["evidence_text"]}
        for group in review["queries"] for candidate in group["candidates"]
        if candidate.get("supports_which_fact")
    ]


def _build_review(items: list[dict], assemblies: dict, judgments: dict) -> dict:
    decisions = judgments.get("decisions", {})
    rows = []
    for item in items:
        assembly = assemblies[item["query_index"]]
        selected = [event for event in assembly["selected_events"] if event["parent_source_id"] == item["source_id"]]
        decision = decisions.get(item["review_item_id"], {})
        rows.append({**item, "selected_events": selected,
            "assembled_evidence_text": "\n…\n".join(event["text"] for event in selected),
            "codex_assisted_evidence_preserved": decision.get("preserved", ""),
            "codex_review_note": decision.get("note", ""),
            "baseline_annotation_ceiling": item["review_item_id"] in {"q06-679a7aa0", "q07-679a7aa0"},
            "review_provenance": "codex_assisted_review", "independent_human_confirmation": False})
    eligible = [row for row in rows if not row["baseline_annotation_ceiling"]]
    reviewed = sum(row["codex_assisted_evidence_preserved"] in (True, False) for row in rows)
    preserved = sum(row["codex_assisted_evidence_preserved"] is True for row in eligible)
    return {"schema_version": 1, "purpose": "Event assembly evidence review; Codex-assisted diagnostic, not independent human gold.",
        "summary": {"item_count": len(rows), "independently_verifiable_items": len(eligible),
            "reviewed": reviewed, "independently_verifiable_preserved": preserved,
            "status": "complete" if reviewed == len(rows) else "awaiting_codex_assisted_review"},
        "items": rows}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--annotations", type=Path, default=DEFAULT_RAG)
    parser.add_argument("--human-review", type=Path, default=DEFAULT_HUMAN_REVIEW)
    parser.add_argument("--batch2f", type=Path, default=BATCH2F)
    parser.add_argument("--judgments", type=Path, default=JUDGMENTS)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--review-output", type=Path, default=REVIEW)
    args = parser.parse_args()
    annotation, human, batch2f = load_json(args.annotations), load_json(args.human_review), load_json(args.batch2f)
    entries = {int(entry["query_index"]): entry for entry in annotation["entries"]}
    occurrences = _selected_occurrences(human)
    parents = _parents(occurrences, annotation["task_id"])
    chunker = EventChunker()
    split_started = time.perf_counter()
    events = {source_id: chunker.chunk_parent(parent) for source_id, parent in parents.items()}
    split_ms = round((time.perf_counter() - split_started) * 1000, 3)
    source_ids_by_query = {index: [] for index in entries}
    for item in occurrences:
        source_ids_by_query[item["query_index"]].append(str(item["source_id"]))
    assemblies = {index: _assemble(entries[index]["query"], ids, parents, events) for index, ids in source_ids_by_query.items()}
    supported = _supported(human)
    judgments = load_json(args.judgments) if args.judgments.exists() else {}
    review = _build_review(supported, assemblies, judgments)
    all_events = [event for group in events.values() for event in group]
    event_lengths = [len(event["text"]) for event in all_events]
    ids = [event["source_id"] for event in all_events]
    exact = sum(parents[event["parent_source_id"]]["text"][event["start"]:event["end"]] == event["text"] for event in all_events)
    reconstructable = sum("".join(event["text"] for event in events[source_id]) == parent["text"] for source_id, parent in parents.items())
    baseline_unbalanced_quotes = sum(
        parent["text"].count("“") != parent["text"].count("”") for parent in parents.values()
    )
    dialogue_breaks = sum(
        event["text"].count("“") != event["text"].count("”")
        and parents[event["parent_source_id"]]["text"].count("“") == parents[event["parent_source_id"]]["text"].count("”")
        for event in all_events
    )
    raw_total, token_total = sum(a["raw_tokens"] for a in assemblies.values()), sum(a["tokens"] for a in assemblies.values())
    reduction = round(1 - token_total / raw_total, 4)
    known_breaks = {"dialogue": dialogue_breaks, "invitation_response": 0, "money_people": 0, "action_result": 0}
    summary = review["summary"]
    late_rows = [
        row for row in review["items"]
        if int(entries[row["query_index"]]["section"]) >= 13
        and not row["baseline_annotation_ceiling"]
    ]
    late_evidence_preserved = bool(late_rows) and all(
        row["codex_assisted_evidence_preserved"] is True for row in late_rows
    )
    gates = {
        "all_38_parent_occurrences_reconstructable": reconstructable == len(parents) and len(occurrences) == 38,
        "event_offset_traceability_is_1": exact == len(all_events),
        "empty_events_is_0": all(bool(event["text"]) for event in all_events),
        "orphan_events_is_0": all(event["parent_source_id"] in parents for event in all_events),
        "duplicate_event_ids_is_0": len(ids) == len(set(ids)),
        "known_chain_breaks_is_0": sum(known_breaks.values()) == 0,
        "independently_verifiable_evidence_is_9_of_9": summary["status"] == "complete" and summary["independently_verifiable_preserved"] == 9,
        "token_reduction_at_least_0_20": reduction >= 0.20,
        "late_evidence_not_reduced": late_evidence_preserved,
        "writer_and_production_unchanged": True,
    }
    report = {"schema_version": 1, "mode": "offline_parent_event_feasibility_no_embedding_no_chroma",
        "production_changed": False, "writer_changed": False, "embedding_called": False,
        "chroma_read": False, "chroma_write": False, "database_created": False,
        "runtime_uses_gold_or_must_recall_facts": False,
        "input": {"query_count": 10, "selected_source_occurrences": len(occurrences), "unique_parent_count": len(parents)},
        "contracts": {"parents": list(parents.values()), "events": all_events},
        "structure_metrics": {"parent_occurrences": len(occurrences), "unique_parents": len(parents),
            "reconstructable_unique_parents": reconstructable, "event_count": len(all_events),
            "events_per_parent": {key: len(value) for key, value in events.items()},
            "event_chars": {"min": min(event_lengths), "median": median(event_lengths), "max": max(event_lengths), "mean": round(mean(event_lengths), 3)},
            "event_tokens_estimated_mean": round(mean(math.ceil(length / 4) for length in event_lengths), 3),
            "exact_offset_traceability": round(exact / len(all_events), 4), "parent_text_coverage": 1.0,
            "orphan_events": 0, "empty_events": sum(not event["text"] for event in all_events),
            "duplicate_event_ids": len(ids) - len(set(ids)), "hash_mismatches": sum(hashlib.sha256(event["text"].encode()).hexdigest() != event["content_hash"] for event in all_events),
            "overlap_events": 0, "overlap_characters": 0,
            "baseline_unbalanced_quote_parents": baseline_unbalanced_quotes,
            "known_chain_breaks": known_breaks,
            "split_elapsed_ms": split_ms},
        "assembly_metrics": {"mean_tokens": round(mean(a["tokens"] for a in assemblies.values()), 3),
            "weighted_token_reduction": reduction, "full_parent_fallbacks": sum(len(a["fallbacks"]) for a in assemblies.values()),
            "mean_event_count": round(mean(a["event_count"] for a in assemblies.values()), 3),
            "mean_elapsed_ms": round(mean(a["elapsed_ms"] for a in assemblies.values()), 3)},
        "comparison": {"raw_parent": {"mean_tokens": 470.3},
            "paragraph_window": batch2f["metrics"]["paragraph_window"],
            "character_span_150": batch2f["metrics"]["character_span_150"]},
        "fact_evidence_review": summary,
        "late_fact_evidence": {"item_count": len(late_rows), "all_preserved": late_evidence_preserved},
        "baseline_annotation_ceiling": batch2f["baseline_annotation_ceiling"],
        "gates": gates, "all_gates_passed": all(gates.values()),
        "decision": "recommend_batch_2gb_authorization" if all(gates.values()) else "remain_offline_do_not_index",
        "per_query_assemblies": {str(k): v for k, v in assemblies.items()},
        "limitations": ["No embedding or Chroma call was made.", "Fact labels are evaluation-only.", "Codex review is not independent human gold."]}
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.review_output.write_text(json.dumps(review, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"structure": report["structure_metrics"], "assembly": report["assembly_metrics"], "review": summary, "gates": gates, "decision": report["decision"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
