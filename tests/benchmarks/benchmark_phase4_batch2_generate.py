"""Generate one isolated, anonymous Phase 4 Batch 2 A/B pair.

Raw generations and the private arm mapping are runtime artifacts and must not
be committed.  The script never invokes Writer and never changes production
messages or stores.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import random
import time
from pathlib import Path

from chromadb.api.client import SharedSystemClient

from app.config import settings
from app.context_ab_shadow import assemble_shadow_messages
from app.utils.llm_client import (
    cost_label,
    estimate_messages_tokens,
    estimate_tokens,
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


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def generate_prepared(query_dir: Path) -> None:
    prepared_path = query_dir / "messages.private.json"
    if not prepared_path.exists():
        raise RuntimeError("prepared private messages are missing")
    payload = json.loads(prepared_path.read_text(encoding="utf-8"))
    prepare = json.loads((query_dir / "prepare.json").read_text(encoding="utf-8"))
    if not settings.LLM_API_KEY:
        raise RuntimeError("LLM_API_KEY is not configured; generation aborted")
    arms = payload["arms"]
    order = payload["order"]
    candidate_for_arm = payload["candidate_for_arm"]
    reset_token_counter()
    public_candidates = []
    private_mapping = {}
    for arm in order:
        candidate_id = candidate_for_arm[arm]
        started = time.perf_counter()
        with cost_label(candidate_id):
            output = get_llm_client().chat_completion(
                arms[arm], temperature=prepare["temperature"], max_tokens=prepare["max_tokens"],
                max_retries=0, top_p=prepare["top_p"], prompt_name="phase4_batch2_shadow_ab",
            )
        elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
        (query_dir / f"{candidate_id}.txt").write_text(output, encoding="utf-8")
        public_candidates.append({
            "candidate_id": candidate_id,
            "output_sha256": sha256_text(output),
            "characters": len(output),
            "estimated_output_tokens": estimate_tokens(output),
            "elapsed_ms": elapsed_ms,
        })
        private_mapping[candidate_id] = {
            "arm": arm,
            "messages_hash": prepare["legacy_messages_hash"] if arm == "legacy_full" else prepare["broker_messages_hash"],
            "actual_total_tokens": get_token_breakdown().get(candidate_id),
        }
    blind = {
        "query_index": prepare["query_index"],
        "section": prepare["section"],
        "subsection": prepare["subsection"],
        "candidates": public_candidates,
        "review_provenance": None,
        "review": None,
    }
    (query_dir / "blind.json").write_text(json.dumps(blind, ensure_ascii=False, indent=2), encoding="utf-8")
    (query_dir / "private_mapping.json").write_text(json.dumps({
        "query_index": prepare["query_index"], "mapping": private_mapping,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"query_index": prepare["query_index"], "candidates": public_candidates}, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--query-index", type=int, required=True)
    parser.add_argument("--runtime-dir", type=Path, required=True)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--generate-prepared", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.query_index <= 10:
        raise ValueError("query-index must be 1..10")
    query_dir = args.runtime_dir / f"q{args.query_index:02d}"
    if args.generate_prepared:
        generate_prepared(query_dir)
        return

    rag_annotation = load_json(DEFAULT_RAG)
    style_config = load_json(DEFAULT_STYLE)
    hard_annotation = load_json(DEFAULT_CHARACTER)
    batch1 = json.loads(BATCH1_REPORT.read_text(encoding="utf-8"))
    entry = next(item for item in rag_annotation["entries"] if int(item["query_index"]) == args.query_index)
    frozen_sample = next(item for item in batch1["samples"] if int(item["query_index"]) == args.query_index)
    frozen_run = frozen_sample["profiles"]["budgeted_broker"]
    frozen_retrieval = next(item for item in batch1["retrieval_runs"] if int(item["query_index"]) == args.query_index)

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
        entry, sections=sections, handovers=handovers, outline=outline, rag_items=rag_items,
        hard_annotation=hard_annotation, evidence_review=None, global_rules=global_rules,
        serialize_blocks=False, include_runtime=True,
    )
    if sample["prompt_hash"] != frozen_sample["writer_legacy_message_hash_before"]:
        raise AssertionError("legacy Writer prompt differs from frozen Batch 1 hash")
    assembled = assemble_shadow_messages(sample, frozen_run)
    if assembled["legacy_hash"] == assembled["shadow_hash"]:
        raise AssertionError("A/B messages unexpectedly identical")

    query_dir.mkdir(parents=True, exist_ok=True)
    arms = {
        "legacy_full": assembled["legacy_messages"],
        "budgeted_broker": assembled["shadow_messages"],
    }
    order = list(arms)
    random.Random(42000 + args.query_index).shuffle(order)
    candidate_for_arm = {arm: f"candidate_{position + 1}" for position, arm in enumerate(order)}
    prepare = {
        "query_index": args.query_index,
        "section": int(entry["section"]),
        "subsection": int(entry["subsection"]),
        "logical_generation_calls": 2,
        "model": settings.LLM_MODEL,
        "base_url_host": settings.LLM_BASE_URL.split("//", 1)[-1].split("/", 1)[0],
        "api_key_configured": bool(settings.LLM_API_KEY),
        "temperature": 0.5,
        "top_p": 0.9,
        "max_tokens": 8000,
        "seed_supported_by_current_client": False,
        "legacy_context_estimated_tokens": assembled["legacy_tokens"],
        "broker_context_estimated_tokens": assembled["shadow_tokens"],
        "legacy_api_estimated_input_tokens": estimate_messages_tokens(arms["legacy_full"]),
        "broker_api_estimated_input_tokens": estimate_messages_tokens(arms["budgeted_broker"]),
        "legacy_messages_hash": assembled["legacy_hash"],
        "broker_messages_hash": assembled["shadow_hash"],
        "frozen_production_prompt_hash": sample["prompt_hash"],
        "production_prompt_hash_unchanged": sample["prompt_hash"] == frozen_sample["writer_legacy_message_hash_after"],
        "kept_source_ids": assembled["kept_source_ids"],
        "dropped_source_ids": assembled["dropped_source_ids"],
        "dropped_item_ids": assembled["dropped_item_ids"],
        "retrieval_source_ids": source_ids,
        "retrieval_filter": {"task_id": rag_annotation["task_id"]},
    }
    (query_dir / "prepare.json").write_text(json.dumps(prepare, ensure_ascii=False, indent=2), encoding="utf-8")
    (query_dir / "messages.private.json").write_text(json.dumps({
        "arms": arms,
        "order": order,
        "candidate_for_arm": candidate_for_arm,
    }, ensure_ascii=False), encoding="utf-8")
    if args.prepare_only:
        print(json.dumps(prepare, ensure_ascii=False, indent=2))
        return
    if not settings.LLM_API_KEY:
        raise RuntimeError("LLM_API_KEY is not configured; generation aborted")

    # The local BGE-M3 model is large.  Release the read-only retrieval stack
    # before generation so it cannot compete with HTTP response handling.
    store._client._system.stop()
    SharedSystemClient.clear_system_cache()
    del store, rag_items, sample
    gc.collect()
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except ImportError:
        pass

    generate_prepared(query_dir)


if __name__ == "__main__":
    main()
