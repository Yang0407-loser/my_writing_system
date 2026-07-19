"""Prepare or run the Phase 4 minimal whole-item restoration experiment.

The preparation path reconstructs the exact frozen Batch 2 messages and aborts
on any prompt or retrieval drift. Raw prompts, arm mappings, and generations are
runtime-only artifacts. The production Writer is never invoked or modified.
"""

from __future__ import annotations

import argparse
import copy
import gc
import hashlib
import json
import random
import time
from pathlib import Path
from typing import Any

from chromadb.api.client import SharedSystemClient

from app.config import settings
from app.context_ab_evaluation import deterministic_output_checks
from app.context_ab_shadow import assemble_shadow_messages, messages_hash, messages_tokens
from app.utils.llm_client import (
    cost_label,
    estimate_messages_tokens,
    get_llm_client,
    get_token_breakdown,
    reset_token_counter,
)
from app.vector_store import VectorStore
from tests.benchmarks.benchmark_context_input_census import (
    build_outline,
    build_sample,
    parse_handovers,
    parse_story,
    read_global_rules_readonly,
)
from tests.quality.baseline import DEFAULT_CHARACTER, DEFAULT_RAG, DEFAULT_STYLE, load_json


ROOT = Path(__file__).resolve().parents[2]
BATCH1_REPORT = ROOT / "reports" / "phase4-batch1-context-broker-shadow.json"
BATCH2_REPORT = ROOT / "reports" / "phase4-batch2-generation-quality-ab.json"
TARGET_QUERY_INDICES = (4, 6, 7)
GROUP_NAMES = {
    "recent_original": "recent_originals",
    "other": "soft_rules",
    "world_event": "world_events",
    "style_examples": "style_context",
}


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def restoration_groups(frozen_run: dict[str, Any]) -> dict[str, list[str]]:
    """Group only optional items that the frozen budgeted run actually dropped."""
    groups: dict[str, list[str]] = {}
    for item in frozen_run.get("items", []):
        if item.get("keep") is not False:
            continue
        if item.get("priority") != "P3":
            raise ValueError(f"frozen run dropped protected item: {item.get('item_id')}")
        group = GROUP_NAMES.get(str(item.get("source_type")), str(item.get("source_type")))
        groups.setdefault(group, []).append(str(item["item_id"]))
    return {name: sorted(item_ids) for name, item_ids in sorted(groups.items())}


def restore_items(frozen_run: dict[str, Any], item_ids: list[str]) -> dict[str, Any]:
    """Return an immutable-style trace copy with selected dropped P3 items restored."""
    result = copy.deepcopy(frozen_run)
    requested = set(item_ids)
    available = {
        str(item["item_id"])
        for item in result.get("items", [])
        if item.get("keep") is False and item.get("priority") == "P3"
    }
    missing = sorted(requested - available)
    if missing:
        raise ValueError(f"items are not restorable dropped P3 entries: {missing}")
    for item in result.get("items", []):
        if item["item_id"] in requested:
            item["keep"] = True
            item["keep_reason"] = "minimal_restoration_experiment"
            item["drop_reason"] = None
    return result


def arm_restorations(frozen_run: dict[str, Any], mode: str) -> dict[str, list[str]]:
    groups = restoration_groups(frozen_run)
    all_dropped = sorted(item_id for item_ids in groups.values() for item_id in item_ids)
    arms: dict[str, list[str]] = {"budgeted_broker": []}
    if mode == "grouped":
        arms.update({f"restore_group:{name}": item_ids for name, item_ids in groups.items()})
    elif mode == "singles":
        arms.update({f"restore_item:{item_id}": [item_id] for item_id in all_dropped})
    else:
        raise ValueError("mode must be grouped or singles")
    arms["legacy_full"] = all_dropped
    return arms


def _load_frozen(query_index: int) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    batch1 = json.loads(BATCH1_REPORT.read_text(encoding="utf-8"))
    batch2 = json.loads(BATCH2_REPORT.read_text(encoding="utf-8"))
    batch1_sample = next(item for item in batch1["samples"] if int(item["query_index"]) == query_index)
    batch2_sample = next(item for item in batch2["samples"] if int(item["query_index"]) == query_index)
    frozen_run = batch1_sample["profiles"]["budgeted_broker"]
    return batch1_sample, batch2_sample, frozen_run


def _build_live_sample(query_index: int) -> tuple[dict[str, Any], VectorStore, list[dict[str, Any]]]:
    rag_annotation = load_json(DEFAULT_RAG)
    style_config = load_json(DEFAULT_STYLE)
    hard_annotation = load_json(DEFAULT_CHARACTER)
    batch1_sample, _, _ = _load_frozen(query_index)
    frozen_retrieval = next(
        item
        for item in json.loads(BATCH1_REPORT.read_text(encoding="utf-8"))["retrieval_runs"]
        if int(item["query_index"]) == query_index
    )
    entry = next(item for item in rag_annotation["entries"] if int(item["query_index"]) == query_index)
    story_path = ROOT / style_config["source_file"]
    sections = parse_story(story_path)
    handovers = parse_handovers(story_path)
    outline = build_outline(sections)
    global_rules, _ = read_global_rules_readonly(ROOT / "rules.db")
    store = VectorStore()
    rag_items = store.search_with_meta(entry["query"], k=5, task_id=rag_annotation["task_id"])
    source_ids = [item.get("id", "") for item in rag_items]
    if source_ids != frozen_retrieval["source_ids"]:
        raise AssertionError("legacy RAG top-5 differs from frozen Batch 1 input")
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
    if sample["prompt_hash"] != batch1_sample["writer_legacy_message_hash_before"]:
        raise AssertionError("legacy Writer prompt differs from frozen Batch 1 hash")
    return sample, store, rag_items


def prepare_query(query_index: int, runtime_dir: Path, mode: str) -> dict[str, Any]:
    if query_index not in TARGET_QUERY_INDICES:
        raise ValueError(f"query-index must be one of {TARGET_QUERY_INDICES}")
    batch1_sample, batch2_sample, frozen_run = _load_frozen(query_index)
    sample, store, rag_items = _build_live_sample(query_index)
    baseline = assemble_shadow_messages(sample, frozen_run)
    if baseline["legacy_hash"] != batch2_sample["legacy_messages_hash"]:
        raise AssertionError("legacy messages hash differs from completed Batch 2")
    if baseline["shadow_hash"] != batch2_sample["broker_messages_hash"]:
        raise AssertionError("budgeted messages hash differs from completed Batch 2")

    restorations = arm_restorations(frozen_run, mode)
    arms: dict[str, list[dict[str, str]]] = {}
    arm_metadata: dict[str, dict[str, Any]] = {}
    for arm, restored_ids in restorations.items():
        assembled = assemble_shadow_messages(sample, restore_items(frozen_run, restored_ids))
        arms[arm] = assembled["shadow_messages"]
        arm_metadata[arm] = {
            "restored_item_ids": restored_ids,
            "messages_hash": assembled["shadow_hash"],
            "estimated_context_tokens": assembled["shadow_tokens"],
            "estimated_api_input_tokens": estimate_messages_tokens(assembled["shadow_messages"]),
        }
    if arm_metadata["budgeted_broker"]["messages_hash"] != baseline["shadow_hash"]:
        raise AssertionError("budgeted control drifted while constructing restoration arms")
    if arm_metadata["legacy_full"]["messages_hash"] != baseline["legacy_hash"]:
        raise AssertionError("restoring every dropped item does not reconstruct legacy_full")
    if messages_hash(arms["legacy_full"]) != messages_hash(baseline["legacy_messages"]):
        raise AssertionError("legacy reconstruction content mismatch")

    query_dir = runtime_dir / f"q{query_index:02d}-{mode}"
    query_dir.mkdir(parents=True, exist_ok=True)
    order = list(arms)
    random.Random(43500 + query_index + (0 if mode == "grouped" else 100)).shuffle(order)
    candidate_for_arm = {arm: f"candidate_{position + 1:02d}" for position, arm in enumerate(order)}
    prepare = {
        "schema_version": 1,
        "experiment": "phase4_minimal_whole_item_restoration",
        "mode": mode,
        "query_index": query_index,
        "section": int(batch1_sample["section"]),
        "subsection": int(batch1_sample["subsection"]),
        "logical_generation_calls": len(arms),
        "model": settings.LLM_MODEL,
        "base_url_host": settings.LLM_BASE_URL.split("//", 1)[-1].split("/", 1)[0],
        "api_key_configured": bool(settings.LLM_API_KEY),
        "temperature": 0.5,
        "top_p": 0.9,
        "max_tokens": 8000,
        "seed_supported_by_current_client": False,
        "frozen_batch2_legacy_hash": batch2_sample["legacy_messages_hash"],
        "frozen_batch2_broker_hash": batch2_sample["broker_messages_hash"],
        "production_prompt_hash_unchanged": sample["prompt_hash"] == batch1_sample["writer_legacy_message_hash_after"],
        "groups": restoration_groups(frozen_run),
        "arms": arm_metadata,
    }
    (query_dir / "prepare.json").write_text(json.dumps(prepare, ensure_ascii=False, indent=2), encoding="utf-8")
    (query_dir / "messages.private.json").write_text(
        json.dumps({"arms": arms, "order": order, "candidate_for_arm": candidate_for_arm}, ensure_ascii=False),
        encoding="utf-8",
    )

    # Release the local embedding stack before a later HTTP generation pass.
    try:
        store._client._system.stop()
        SharedSystemClient.clear_system_cache()
    finally:
        del store, rag_items, sample
        gc.collect()
    return prepare


def generate_prepared(query_dir: Path) -> dict[str, Any]:
    private_path = query_dir / "messages.private.json"
    prepare_path = query_dir / "prepare.json"
    if not private_path.exists() or not prepare_path.exists():
        raise RuntimeError("prepared private messages are missing")
    if not settings.LLM_API_KEY:
        raise RuntimeError("LLM_API_KEY is not configured; generation aborted")
    payload = json.loads(private_path.read_text(encoding="utf-8"))
    prepare = json.loads(prepare_path.read_text(encoding="utf-8"))
    reset_token_counter()
    public_candidates = []
    private_mapping = {}
    for arm in payload["order"]:
        candidate_id = payload["candidate_for_arm"][arm]
        messages = payload["arms"][arm]
        expected_hash = prepare["arms"][arm]["messages_hash"]
        if messages_hash(messages) != expected_hash:
            raise AssertionError(f"prepared messages changed for arm {arm}")
        started = time.perf_counter()
        with cost_label(candidate_id):
            output = get_llm_client().chat_completion(
                messages,
                temperature=prepare["temperature"],
                max_tokens=prepare["max_tokens"],
                max_retries=0,
                top_p=prepare["top_p"],
                prompt_name="phase4_minimal_restoration",
            )
        elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
        (query_dir / f"{candidate_id}.txt").write_text(output, encoding="utf-8")
        checks = deterministic_output_checks(output)
        public_candidates.append({
            "candidate_id": candidate_id,
            "output_sha256": checks["sha256"],
            "characters": checks["characters"],
            "estimated_output_tokens": checks["estimated_tokens"],
            "paragraph_count": checks["paragraph_count"],
            "duplicate_paragraph_count": checks["duplicate_paragraph_count"],
            "elapsed_ms": elapsed_ms,
        })
        private_mapping[candidate_id] = {
            "arm": arm,
            "messages_hash": expected_hash,
            "restored_item_ids": prepare["arms"][arm]["restored_item_ids"],
            "actual_total_tokens": get_token_breakdown().get(candidate_id),
        }
    blind = {
        "schema_version": 1,
        "query_index": prepare["query_index"],
        "section": prepare["section"],
        "subsection": prepare["subsection"],
        "mode": prepare["mode"],
        "candidates": public_candidates,
        "review_provenance": None,
        "review": None,
    }
    mapping = {"query_index": prepare["query_index"], "mode": prepare["mode"], "mapping": private_mapping}
    (query_dir / "blind.json").write_text(json.dumps(blind, ensure_ascii=False, indent=2), encoding="utf-8")
    (query_dir / "private_mapping.json").write_text(json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8")
    return blind


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--query-index", type=int, choices=TARGET_QUERY_INDICES, required=True)
    parser.add_argument("--runtime-dir", type=Path, default=ROOT / ".phase4_restoration_runtime")
    parser.add_argument("--mode", choices=("grouped", "singles"), default="grouped")
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--generate-prepared", action="store_true")
    args = parser.parse_args()
    query_dir = args.runtime_dir / f"q{args.query_index:02d}-{args.mode}"
    if args.generate_prepared:
        result = generate_prepared(query_dir)
    else:
        result = prepare_query(args.query_index, args.runtime_dir, args.mode)
        if not args.prepare_only:
            result = generate_prepared(query_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
