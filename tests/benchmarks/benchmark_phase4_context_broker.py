"""Run Phase 4 Batch 1 whole-item ContextBroker shadow comparisons."""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path
from statistics import mean

from chromadb.api.client import SharedSystemClient

from app.context_broker import ContextBroker, ContextItem, priority_for
from app.context_census import estimate_tokens
from app.vector_store import VectorStore
from tests.benchmarks.benchmark_context_input_census import (
    REVIEW_PATH,
    build_outline,
    build_sample,
    human_evidence_manifest,
    parse_handovers,
    parse_story,
    read_global_rules_readonly,
)
from tests.quality.baseline import DEFAULT_CHARACTER, DEFAULT_RAG, DEFAULT_STYLE, load_json


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "reports" / "phase4-batch1-context-broker-shadow.json"
PROFILES = ("legacy_full", "continuity_first", "budgeted_broker")


def _section_coordinates(source_id: str) -> tuple[int | None, int | None]:
    match = re.search(r":S(\d+):U(\d+)", source_id)
    return (int(match.group(1)), int(match.group(2))) if match else (None, None)


def _split_global_rule_block(block: dict) -> list[ContextItem]:
    lines = [line for line in block["text"].splitlines() if line.strip()]
    if not lines:
        return []
    raw_tokens = [estimate_tokens(line) for line in lines]
    # Allocate the original block's already-reconciled token cost exactly.
    estimates = list(raw_tokens)
    estimates[0] += int(block["estimated_tokens"]) - sum(estimates)
    result = []
    for index, (line, tokens) in enumerate(zip(lines, estimates)):
        match = re.search(r"\[[^\]\d]*(\d+)\]", line)
        locked = bool(match and int(match.group(1)) >= 8)
        requirement, priority = priority_for("other", locked=locked)
        result.append(ContextItem(
            item_id=f"{block['block_id']}:line:{index}",
            source_id=f"{block['source_id']}:line:{index}",
            source_type="other",
            requirement=requirement,
            priority=priority,
            text=line,
            estimated_tokens=max(0, tokens),
            injection_position=block["injection_position"],
            provenance="rules.db enabled rule line; LOCKED threshold priority>=8" if match else "rules prompt header",
        ))
    return result


def build_context_items(sample: dict) -> list[ContextItem]:
    blocks = sample["blocks"]
    recent_ids = [block["block_id"] for block in blocks if block["category"] == "recent_original"]
    immediate_id = recent_ids[-1] if recent_ids else None
    items: list[ContextItem] = []
    for block in blocks:
        if block["block_id"] == "other:global-rules":
            items.extend(_split_global_rule_block(block))
            continue
        immediate = block["block_id"] == immediate_id
        requirement, priority = priority_for(block["category"], immediate_previous=immediate)
        section, subsection = _section_coordinates(block["source_id"])
        section = block.get("section", section)
        subsection = block.get("subsection", subsection)
        if block["category"] in {"current_writing", "character_relation"}:
            section, subsection = sample["section"], sample["subsection"]
        elif block["category"] == "handover":
            section = max(0, int(sample["section"]) - 1)
        items.append(ContextItem(
            item_id=block["block_id"],
            source_id=block["source_id"],
            source_type=block["category"],
            requirement=requirement,
            priority=priority,
            text=block["text"],
            estimated_tokens=int(block["estimated_tokens"]),
            injection_position=block["injection_position"],
            section=section,
            subsection=subsection,
            provenance="legacy prompt block reconstructed by Phase 4 entry census",
        ))
    delta = int(sample["ledger"]["reconciliation_delta_assigned_to_fixed_prompt"])
    if delta:
        index = next(index for index, item in enumerate(items) if item.source_type == "fixed_prompt")
        item = items[index]
        items[index] = ContextItem(
            **{**item.__dict__, "estimated_tokens": item.estimated_tokens + delta,
               "provenance": item.provenance + "; includes prompt-accounting reconciliation"}
        )
    expected = int(sample["ledger"]["total_estimated_tokens"])
    if sum(item.estimated_tokens for item in items) != expected:
        raise AssertionError("ContextItem accounting does not reconcile to legacy prompt")
    return items


def _kept_ids(run: dict) -> set[str]:
    return {item["source_id"] for item in run["items"] if item["keep"]}


def _aggregate_profile(samples: list[dict], profile: str, evaluation: dict[int, list[dict]]) -> dict:
    runs = [sample["profiles"][profile] for sample in samples]
    legacy = [sample["profiles"]["legacy_full"]["total_estimated_tokens"] for sample in samples]
    totals = [run["total_estimated_tokens"] for run in runs]
    category_values: dict[str, list[int]] = defaultdict(list)
    categories = sorted({item["source_type"] for run in runs for item in run["items"]})
    for category in categories:
        for run in runs:
            category_values[category].append(sum(
                item["estimated_tokens"] for item in run["items"]
                if item["keep"] and item["source_type"] == category
            ))
    hard = [item for run in runs for item in run["items"] if item["requirement"] == "hard_required"]
    immediate = [item for run in runs for item in run["items"] if item["source_type"] == "recent_original" and item["priority"] == "P1"]
    handovers = [item for run in runs for item in run["items"] if item["source_type"] == "handover"]
    older = [item for run in runs for item in run["items"] if item["source_type"] == "recent_original" and item["priority"] == "P3"]
    evidence_present = []
    evidence_absent = []
    for sample in samples:
        kept = _kept_ids(sample["profiles"][profile])
        legacy_ids = _kept_ids(sample["profiles"]["legacy_full"])
        for evidence in evaluation[sample["query_index"]]:
            target = evidence_present if evidence["source_id"] in legacy_ids else evidence_absent
            target.append({**evidence, "kept": evidence["source_id"] in kept})
    late_items = [
        item for sample in samples if sample["section"] >= 12
        for item in sample["profiles"][profile]["items"]
        if item["priority"] in {"P0", "P1", "P2"}
    ]
    def retention(items: list[dict]) -> float:
        return round(sum(bool(item.get("keep", item.get("kept"))) for item in items) / len(items), 4) if items else 1.0
    mean_total = mean(totals)
    legacy_mean = mean(legacy)
    reduction = 1 - mean_total / legacy_mean if legacy_mean else 0.0
    source_summary = {
        category: {
            "mean_estimated_tokens": round(mean(values), 1),
            "share": round(mean(values) / mean_total, 4) if mean_total else 0.0,
        }
        for category, values in category_values.items()
    }
    result = {
        "mean_total_estimated_tokens": round(mean_total, 1),
        "min_total_estimated_tokens": min(totals),
        "max_total_estimated_tokens": max(totals),
        "reduction_vs_legacy": round(reduction, 4),
        "source_tokens": source_summary,
        "older_recent_retention": retention(older),
        "immediate_previous_retention": retention(immediate),
        "handover_retention": retention(handovers),
        "hard_required_retention": retention(hard),
        "legacy_present_human_evidence": {
            "total": len(evidence_present), "kept": sum(item["kept"] for item in evidence_present),
            "retention": retention(evidence_present),
        },
        "baseline_retrieval_ceiling": {
            "total": len(evidence_absent),
            "reason": "human-supported evidence source was not present in legacy top-5 input",
        },
        "late_query_required_retention": retention(late_items),
        "budget_overflow_count": sum(bool(run["budget_overflow_reason"]) for run in runs),
        "budget_overflow_reasons": sorted({run["budget_overflow_reason"] for run in runs if run["budget_overflow_reason"]}),
        "traceability_rate": round(sum(
            bool(item["source_id"] and item["text_hash"] and item["injection_position"])
            for run in runs for item in run["items"]
        ) / max(1, sum(len(run["items"]) for run in runs)), 4),
        "mean_broker_elapsed_ms": round(mean(run["elapsed_ms"] for run in runs), 3),
        "per_query": [{
            "query_index": sample["query_index"],
            "total_estimated_tokens": sample["profiles"][profile]["total_estimated_tokens"],
            "kept_item_ids": [item["item_id"] for item in sample["profiles"][profile]["items"] if item["keep"]],
            "dropped_items": [{"item_id": item["item_id"], "reason": item["drop_reason"]} for item in sample["profiles"][profile]["items"] if not item["keep"]],
            "budget_overflow_reason": sample["profiles"][profile]["budget_overflow_reason"],
        } for sample in samples],
    }
    result["acceptance"] = {
        "hard_required_100": result["hard_required_retention"] == 1.0,
        "immediate_previous_100": result["immediate_previous_retention"] == 1.0,
        "handover_100": result["handover_retention"] == 1.0,
        "legacy_present_evidence_4_of_4": len(evidence_present) == 4 and sum(item["kept"] for item in evidence_present) == 4,
        "late_required_100": result["late_query_required_retention"] == 1.0,
        "traceability_100": result["traceability_rate"] == 1.0,
        "mean_token_reduction_at_least_20pct": reduction >= 0.20,
    }
    result["acceptance"]["all_batch1_gates"] = all(result["acceptance"].values())
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--query-index", type=int, help="diagnostic single-query run")
    args = parser.parse_args()
    print("phase4-batch1: loading frozen inputs", flush=True)
    rag_annotation = load_json(DEFAULT_RAG)
    style_config = load_json(DEFAULT_STYLE)
    hard_annotation = load_json(DEFAULT_CHARACTER)
    story_path = ROOT / style_config["source_file"]
    sections = parse_story(story_path)
    handovers = parse_handovers(story_path)
    outline = build_outline(sections)
    global_rules, rules_snapshot = read_global_rules_readonly(ROOT / "rules.db")
    store = VectorStore()
    print("phase4-batch1: vector store ready", flush=True)
    broker = ContextBroker(target_tokens=8500)
    samples = []
    retrieval = []
    entries = [entry for entry in rag_annotation["entries"] if not args.query_index or int(entry["query_index"]) == args.query_index]
    for entry_position, entry in enumerate(entries):
        if entry_position and entry_position % 2 == 0:
            store._client._system.stop()
            SharedSystemClient.clear_system_cache()
            del store
            gc.collect()
            store = VectorStore()
            print("phase4-batch1: recycled read-only Chroma client", flush=True)
        print(f"phase4-batch1: query {entry['query_index']}/10", flush=True)
        rag_items = store.search_with_meta(entry["query"], k=5, task_id=rag_annotation["task_id"])
        print(f"phase4-batch1: query {entry['query_index']} retrieved", flush=True)
        retrieval.append({
            "query_index": int(entry["query_index"]), "returned": len(rag_items),
            "source_ids": [item.get("id", "") for item in rag_items],
            "filter": {"task_id": rag_annotation["task_id"]},
            "elapsed_ms": store.last_search_trace.get("elapsed_ms"),
        })
        sample = build_sample(
            entry, sections=sections, handovers=handovers, outline=outline, rag_items=rag_items,
            hard_annotation=hard_annotation, evidence_review=None, global_rules=global_rules,
            serialize_blocks=False,
        )
        print(f"phase4-batch1: query {entry['query_index']} census built", flush=True)
        legacy_hash_before = sample["prompt_hash"]
        items = build_context_items(sample)
        print(f"phase4-batch1: query {entry['query_index']} items built", flush=True)
        profiles = {profile: broker.select(items, profile=profile, query=entry["query"]) for profile in PROFILES}
        samples.append({
            "query_index": int(entry["query_index"]), "section": int(entry["section"]),
            "subsection": int(entry["subsection"]), "query_hash": hashlib.sha256(entry["query"].encode()).hexdigest(),
            "writer_legacy_message_hash_before": legacy_hash_before,
            "writer_legacy_message_hash_after": sample["prompt_hash"],
            "writer_legacy_message_hash_unchanged": legacy_hash_before == sample["prompt_hash"],
            "profiles": profiles,
        })
        print(f"phase4-batch1: query {entry['query_index']} assembled", flush=True)
        del sample, items, rag_items, profiles
        gc.collect()

    # Evaluation labels enter only after every runtime selection has completed.
    print("phase4-batch1: loading post-selection evaluation", flush=True)
    review = load_json(REVIEW_PATH)
    evaluation = {
        int(entry["query_index"]): human_evidence_manifest(review, int(entry["query_index"]))
        for entry in entries
    }
    summaries = {profile: _aggregate_profile(samples, profile, evaluation) for profile in PROFILES}
    legacy_hash_unchanged = all(sample["writer_legacy_message_hash_unchanged"] for sample in samples)
    for summary in summaries.values():
        summary["acceptance"]["writer_legacy_message_hash_unchanged"] = legacy_hash_unchanged
        summary["acceptance"]["all_batch1_gates"] = all(summary["acceptance"].values())
    eligible = [profile for profile in PROFILES if profile != "legacy_full" and summaries[profile]["acceptance"]["all_batch1_gates"]]
    report = {
        "schema_version": 1,
        "purpose": "Phase 4 Batch 1 Context Broker whole-item selection and soft-budget shadow experiment",
        "offline_llm_calls": 0,
        "writer_generation_calls": 0,
        "production_behavior_changed": False,
        "context_manager_contract_changed": False,
        "runtime_forbidden_fields": ["must_recall_facts", "gold_sections", "human_relevant", "supports_which_fact", "review conclusions"],
        "evaluation_loaded_after_all_runtime_selections": True,
        "token_method": "estimated_token: Writer._estimate_prompt_tokens compatible",
        "target_tokens": 8500,
        "rules_snapshot": rules_snapshot,
        "retrieval_runs": retrieval,
        "summary": summaries,
        "decision": {
            "eligible_profiles_for_generation_quality_shadow": eligible,
            "production_promotion": False,
            "status": "batch1_item_selection_passed_but_requires_generation_quality_evaluation" if eligible else "remain_shadow_batch1_gate_not_met",
            "batch2_started": False,
        },
        "limitations": [
            "This is a reconstructed frozen-input benchmark and does not call the Writer LLM.",
            "All legacy top-5 RAG items are protected; seven human-supported sources absent from legacy remain a retrieval ceiling.",
            "A token gate alone cannot justify production promotion.",
        ],
        "samples": samples,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "output": str(args.output),
        "profiles": {name: {
            "mean_tokens": value["mean_total_estimated_tokens"],
            "reduction": value["reduction_vs_legacy"],
            "passes": value["acceptance"]["all_batch1_gates"],
        } for name, value in summaries.items()},
        "decision": report["decision"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
