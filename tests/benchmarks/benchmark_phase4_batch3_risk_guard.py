"""Phase 4 Batch 3 deterministic continuity-risk guard shadow benchmark."""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import time
from pathlib import Path

import chromadb
from chromadb.config import Settings as ChromaSettings

from app.config import settings
from app.continuity_risk_guard import ContinuityRiskGuard
from app.context_ab_shadow import assemble_shadow_messages, messages_hash
from tests.benchmarks.benchmark_context_input_census import (
    REVIEW_PATH,
    build_outline,
    build_sample,
    human_evidence_manifest,
    parse_handovers,
    parse_story,
    read_global_rules_readonly,
)
from tests.benchmarks.benchmark_phase4_context_broker import build_context_items
from tests.quality.baseline import DEFAULT_CHARACTER, DEFAULT_RAG, DEFAULT_STYLE, load_json


ROOT = Path(__file__).resolve().parents[2]
BATCH1 = ROOT / "reports" / "phase4-batch1-context-broker-shadow.json"
BATCH2 = ROOT / "reports" / "phase4-batch2-generation-quality-ab.json"
DEFAULT_OUTPUT = ROOT / "reports" / "phase4-batch3-continuity-risk-guard-shadow.json"
TARGET_TOKENS = 8500


def _stats(values: list[int | float]) -> dict:
    return {
        "mean": round(statistics.mean(values), 1),
        "min": min(values),
        "max": max(values),
    }


def _frozen_rag_items(collection, source_ids: list[str]) -> list[dict]:
    result = collection.get(ids=source_ids, include=["documents", "metadatas"])
    documents = result.get("documents") or []
    metadatas = result.get("metadatas") or []
    by_id = {
        doc_id: (documents[index], metadatas[index] or {})
        for index, doc_id in enumerate(result.get("ids") or [])
    }
    missing = [source_id for source_id in source_ids if source_id not in by_id]
    if missing:
        raise AssertionError(f"frozen RAG sources missing from Chroma: {missing}")
    return [
        {
            "id": source_id,
            "text": by_id[source_id][0],
            "section": by_id[source_id][1].get("section", 0),
            "subsection": by_id[source_id][1].get("subsection", 0),
            "title": by_id[source_id][1].get("title", ""),
            "metadata": dict(by_id[source_id][1]),
        }
        for source_id in source_ids
    ]


def _risk_guarded_run(items, frozen_budgeted: dict, *, query: str, handover_text: str) -> dict:
    guard = ContinuityRiskGuard()
    frozen = {item["item_id"]: item for item in frozen_budgeted["items"]}
    immediate = next(
        (item for item in items if item.source_type == "recent_original" and item.priority == "P1"),
        None,
    )
    older = [item for item in items if item.source_type == "recent_original" and item.priority == "P3"]
    assessments = {}
    for item in older:
        peers = tuple(peer.text for peer in older if peer.item_id != item.item_id)
        assessments[item.item_id] = guard.assess(
            item,
            query=query,
            immediate_text=immediate.text if immediate else "",
            handover_text=handover_text,
            peer_older_texts=peers,
        ).trace()

    running = 0
    traces = []
    restored = []
    for item in items:
        baseline = frozen[item.item_id]
        assessment = assessments.get(item.item_id)
        keep = bool(baseline["keep"])
        keep_reason = baseline["keep_reason"]
        drop_reason = baseline["drop_reason"]
        fallback_reason = baseline.get("fallback_reason")
        if assessment and assessment["protect"] and not keep:
            keep = True
            keep_reason = "continuity_risk_guard_restore_full_item"
            drop_reason = None
            fallback_reason = assessment["reason"]
            restored.append(item.item_id)
        before = running
        if keep:
            running += item.estimated_tokens
        trace = item.trace()
        trace.update({
            "keep": keep,
            "keep_reason": keep_reason if keep else None,
            "drop_reason": drop_reason if not keep else None,
            "budget_before": before,
            "budget_after": running,
            "fallback_reason": fallback_reason,
            "continuity_risk_assessment": assessment,
        })
        traces.append(trace)
    overflow = "continuity_risk_protection_exceeds_soft_budget" if running > TARGET_TOKENS else None
    return {
        "profile": "risk_guarded_broker",
        "target_tokens": TARGET_TOKENS,
        "total_estimated_tokens": running,
        "kept_item_count": sum(item["keep"] for item in traces),
        "dropped_item_count": sum(not item["keep"] for item in traces),
        "budget_overflow_reason": overflow,
        "restored_item_ids": restored,
        "items": traces,
    }


def _retention(items: list[dict]) -> float:
    return round(sum(bool(item["keep"]) for item in items) / len(items), 4) if items else 1.0


def _public_samples(samples: list[dict]) -> list[dict]:
    """Keep the new C trace; reference frozen reports for already-published A/B traces."""
    result = []
    for sample in samples:
        public = {key: value for key, value in sample.items() if key != "profiles"}
        public["profiles"] = {
            "legacy_full": {
                "source_report": "reports/phase4-batch1-context-broker-shadow.json",
                "total_estimated_tokens": sample["profiles"]["legacy_full"]["total_estimated_tokens"],
            },
            "budgeted_broker": {
                "source_report": "reports/phase4-batch1-context-broker-shadow.json",
                "total_estimated_tokens": sample["profiles"]["budgeted_broker"]["total_estimated_tokens"],
            },
            "risk_guarded_broker": sample["profiles"]["risk_guarded_broker"],
        }
        result.append(public)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    batch1 = json.loads(BATCH1.read_text(encoding="utf-8"))
    batch1_samples = {int(item["query_index"]): item for item in batch1["samples"]}
    retrievals = {int(item["query_index"]): item for item in batch1["retrieval_runs"]}
    rag_annotation = load_json(DEFAULT_RAG)
    style_config = load_json(DEFAULT_STYLE)
    hard_annotation = load_json(DEFAULT_CHARACTER)
    entries = {int(item["query_index"]): item for item in rag_annotation["entries"]}
    story_path = ROOT / style_config["source_file"]
    sections = parse_story(story_path)
    handovers = parse_handovers(story_path)
    outline = build_outline(sections)
    global_rules, rules_snapshot = read_global_rules_readonly(ROOT / "rules.db")
    client = chromadb.PersistentClient(
        path=settings.CHROMA_DATA_PATH,
        settings=ChromaSettings(anonymized_telemetry=False),
    )
    collection = client.get_collection("writing_paragraphs")

    samples = []
    for query_index in range(1, 11):
        started = time.perf_counter()
        entry = entries[query_index]
        frozen = batch1_samples[query_index]
        source_ids = retrievals[query_index]["source_ids"]
        rag_items = _frozen_rag_items(collection, source_ids)
        sample = build_sample(
            entry,
            sections=sections,
            handovers=handovers,
            outline=outline,
            rag_items=rag_items,
            hard_annotation=hard_annotation,
            evidence_review=None,
            global_rules=global_rules,
            serialize_blocks=False,
            include_runtime=True,
        )
        if sample["prompt_hash"] != frozen["writer_legacy_message_hash_before"]:
            raise AssertionError(f"q{query_index}: legacy prompt differs from frozen Batch 1")
        items = build_context_items(sample)
        handover_text = "\n".join(
            block["text"] for block in sample["blocks"] if block["category"] == "handover"
        )
        legacy_run = frozen["profiles"]["legacy_full"]
        budgeted_run = frozen["profiles"]["budgeted_broker"]
        risk_run = _risk_guarded_run(
            items,
            budgeted_run,
            query=entry["query"],
            handover_text=handover_text,
        )
        budgeted_messages = assemble_shadow_messages(sample, budgeted_run)
        risk_messages = assemble_shadow_messages(sample, risk_run)
        legacy_messages = sample["runtime"]["messages"]
        if messages_hash(legacy_messages) != budgeted_messages["legacy_hash"]:
            raise AssertionError(f"q{query_index}: legacy messages mutated")
        samples.append({
            "query_index": query_index,
            "section": int(entry["section"]),
            "subsection": int(entry["subsection"]),
            "query_hash": hashlib.sha256(entry["query"].encode("utf-8")).hexdigest(),
            "retrieval_source_ids": source_ids,
            "retrieval_filter": {"task_id": rag_annotation["task_id"]},
            "writer_production_prompt_hash": sample["prompt_hash"],
            "writer_production_prompt_hash_unchanged": sample["prompt_hash"] == frozen["writer_legacy_message_hash_after"],
            "messages_hashes": {
                "legacy_full": messages_hash(legacy_messages),
                "budgeted_broker": budgeted_messages["shadow_hash"],
                "risk_guarded_broker": risk_messages["shadow_hash"],
            },
            "message_tokens": {
                "legacy_full": budgeted_messages["legacy_tokens"],
                "budgeted_broker": budgeted_messages["shadow_tokens"],
                "risk_guarded_broker": risk_messages["shadow_tokens"],
            },
            "profiles": {
                "legacy_full": legacy_run,
                "budgeted_broker": budgeted_run,
                "risk_guarded_broker": risk_run,
            },
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
        })

    # Evaluation sources are loaded only after all runtime guard decisions.
    review = load_json(REVIEW_PATH)
    batch2 = json.loads(BATCH2.read_text(encoding="utf-8"))
    batch2_samples = {int(item["query_index"]): item for item in batch2["samples"]}
    evaluation = {
        query_index: human_evidence_manifest(review, query_index)
        for query_index in range(1, 11)
    }

    token_stats = {
        profile: _stats([sample["message_tokens"][profile] for sample in samples])
        for profile in ("legacy_full", "budgeted_broker", "risk_guarded_broker")
    }
    legacy_mean = token_stats["legacy_full"]["mean"]
    risk_mean = token_stats["risk_guarded_broker"]["mean"]
    reduction = round(1 - risk_mean / legacy_mean, 4)
    risk_items = [item for sample in samples for item in sample["profiles"]["risk_guarded_broker"]["items"]]
    protected = [item for item in risk_items if item["priority"] in {"P0", "P1", "P2"}]
    hard = [item for item in risk_items if item["requirement"] == "hard_required"]
    immediate = [item for item in risk_items if item["source_type"] == "recent_original" and item["priority"] == "P1"]
    handover_items = [item for item in risk_items if item["source_type"] == "handover"]
    rag_items = [item for item in risk_items if item["source_type"] == "rag"]
    character_items = [item for item in risk_items if item["source_type"] == "character_relation"]
    late_required = [
        item for sample in samples if sample["section"] >= 12
        for item in sample["profiles"]["risk_guarded_broker"]["items"]
        if item["priority"] in {"P0", "P1", "P2"}
    ]
    evidence_present = []
    for sample in samples:
        legacy_ids = {
            item["source_id"] for item in sample["profiles"]["legacy_full"]["items"] if item["keep"]
        }
        risk_ids = {
            item["source_id"] for item in sample["profiles"]["risk_guarded_broker"]["items"] if item["keep"]
        }
        for evidence in evaluation[sample["query_index"]]:
            if evidence["source_id"] in legacy_ids:
                evidence_present.append(evidence["source_id"] in risk_ids)

    diagnostics = {}
    all_required_risk_items_protected = True
    for query_index in (4, 6, 7, 8):
        sample = next(item for item in samples if item["query_index"] == query_index)
        risk_by_id = {
            item["item_id"]: item for item in sample["profiles"]["risk_guarded_broker"]["items"]
        }
        expected = [
            item_id for item_id in batch2_samples[query_index]["dropped_item_ids"]
            if item_id.startswith("recent:")
        ]
        rows = [{
            "item_id": item_id,
            "protected": bool(risk_by_id[item_id]["keep"]),
            "risk_assessment": risk_by_id[item_id]["continuity_risk_assessment"],
        } for item_id in expected]
        diagnostics[f"q{query_index:02d}"] = rows
        if query_index in {4, 6, 7}:
            all_required_risk_items_protected &= all(row["protected"] for row in rows)

    traceability = all(
        item["source_id"] and item["text_hash"] and item["injection_position"]
        and (item["keep_reason"] if item["keep"] else item["drop_reason"])
        for item in risk_items
    )
    gates = {
        "risk_guarded_reduction_at_least_20_percent": reduction >= 0.20,
        "q04_q06_q07_risk_items_all_protected": all_required_risk_items_protected,
        "protected_p0_p1_p2_retention_100": _retention(protected) == 1.0,
        "hard_required_retention_100": _retention(hard) == 1.0,
        "character_and_relationship_retention_100": _retention(character_items) == 1.0,
        "immediate_previous_retention_100": _retention(immediate) == 1.0,
        "handover_retention_100": _retention(handover_items) == 1.0,
        "legacy_rag_retention_100": _retention(rag_items) == 1.0,
        "legacy_present_human_evidence_retention_4_of_4": len(evidence_present) == 4 and all(evidence_present),
        "late_required_retention_100": _retention(late_required) == 1.0,
        "traceability_100": traceability,
        "production_messages_hash_unchanged": all(item["writer_production_prompt_hash_unchanged"] for item in samples),
    }
    passed = all(gates.values())
    report = {
        "schema_version": 1,
        "purpose": "Phase 4 Batch 3 deterministic continuity-risk protection shadow",
        "offline_llm_calls": 0,
        "writer_generation_calls": 0,
        "production_behavior_changed": False,
        "context_manager_contract_changed": False,
        "writer_prompt_changed": False,
        "rag_or_model_changed": False,
        "runtime_forbidden_fields": ["must_recall_facts", "gold_sections", "human_relevant", "supports_which_fact", "review conclusions"],
        "evaluation_loaded_after_all_runtime_selections": True,
        "token_method": "estimated_token: rendered Writer messages",
        "target_tokens": TARGET_TOKENS,
        "rules_snapshot": rules_snapshot,
        "token_stats": token_stats,
        "risk_guarded_reduction_vs_legacy": reduction,
        "risk_guarded_budget_overflow_count": sum(
            bool(sample["profiles"]["risk_guarded_broker"]["budget_overflow_reason"])
            for sample in samples
        ),
        "risk_guarded_budget_overflow_reasons": sorted({
            sample["profiles"]["risk_guarded_broker"]["budget_overflow_reason"]
            for sample in samples
            if sample["profiles"]["risk_guarded_broker"]["budget_overflow_reason"]
        }),
        "retention": {
            "protected_p0_p1_p2": _retention(protected),
            "hard_required": _retention(hard),
            "character_and_relationship": _retention(character_items),
            "immediate_previous": _retention(immediate),
            "handover": _retention(handover_items),
            "legacy_rag": _retention(rag_items),
            "legacy_present_human_evidence": {
                "total": len(evidence_present),
                "kept": sum(evidence_present),
                "retention": round(sum(evidence_present) / len(evidence_present), 4) if evidence_present else 1.0,
            },
            "late_required": _retention(late_required),
        },
        "diagnostic_queries": diagnostics,
        "gates": gates,
        "all_mechanical_gates_passed": passed,
        "decision": {
            "production_promotion": False,
            "generation_validation_started": False,
            "phase5_started": False,
            "status": "eligible_for_separately_authorized_small_ac_validation" if passed else "conservative_any_signal_guard_failed_stop",
            "recommendation": (
                "prepare_only_small_anonymous_A_C_generation_validation; do_not_call_model"
                if passed else
                "do_not_decide_architecture_until_guard_rule_necessity_is_empirically_tested"
            ),
        },
        "samples": _public_samples(samples),
    }
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "output": str(args.output),
        "token_stats": token_stats,
        "risk_guarded_reduction_vs_legacy": reduction,
        "budget_overflow_count": report["risk_guarded_budget_overflow_count"],
        "gates": gates,
        "decision": report["decision"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
