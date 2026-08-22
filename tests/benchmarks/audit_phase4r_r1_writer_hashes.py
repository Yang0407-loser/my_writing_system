"""Rebuild the ten frozen Writer prompts through PromptBuilder without an LLM call."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.vector_store import VectorStore
from app.writing import PromptBuilder, SubsectionInput
from tests.benchmarks.benchmark_context_input_census import (
    REVIEW_PATH,
    ROOT,
    build_outline,
    build_sample,
    parse_handovers,
    parse_story,
    read_global_rules_readonly,
)
from tests.quality.baseline import DEFAULT_CHARACTER, DEFAULT_RAG, DEFAULT_STYLE, load_json


DEFAULT_OUTPUT = ROOT / "reports" / "phase4r-batch-r1-writer-hash-audit.json"
ENTRY_REPORT = ROOT / "reports" / "phase4-entry-context-census.json"
BATCH2_REPORT = ROOT / "reports" / "phase4-batch2-generation-quality-ab.json"


def run_audit() -> dict:
    rag_annotation = load_json(DEFAULT_RAG)
    style_config = load_json(DEFAULT_STYLE)
    hard_annotation = load_json(DEFAULT_CHARACTER)
    evidence_review = load_json(REVIEW_PATH)
    entry_report = load_json(ENTRY_REPORT)
    batch2_report = load_json(BATCH2_REPORT)
    expected_entry = {int(item["query_index"]): item for item in entry_report["samples"]}
    expected_batch2 = {int(item["query_index"]): item for item in batch2_report["samples"]}

    story_path = ROOT / style_config["source_file"]
    sections = parse_story(story_path)
    handovers = parse_handovers(story_path)
    outline = build_outline(sections)
    global_rules, rules_snapshot = read_global_rules_readonly(ROOT / "rules.db")
    store = VectorStore()
    samples = []
    for entry in rag_annotation["entries"]:
        query_index = int(entry["query_index"])
        rag_items = store.search_with_meta(
            entry["query"], k=5, task_id=rag_annotation["task_id"]
        )
        sample = build_sample(
            entry,
            sections=sections,
            handovers=handovers,
            outline=outline,
            rag_items=rag_items,
            hard_annotation=hard_annotation,
            evidence_review=evidence_review,
            global_rules=global_rules,
            include_runtime=True,
        )
        prepared = SubsectionInput(
            task_id=rag_annotation["task_id"],
            section=sample["section"],
            subsection=sample["subsection"],
            outline_target=sample["runtime"]["values"]["section_outline"],
            target_words=int(sample["runtime"]["values"]["target_words"]),
            generation_settings={"temperature": 0.5, "top_p": 0.9},
            prepared_context_fields=sample["runtime"]["values"],
            source_manifest=sample["required_manifest"],
        )
        artifact = PromptBuilder().build(prepared)
        expected_content_hash = expected_entry[query_index]["prompt_hash"]
        expected_messages_hash = expected_batch2[query_index]["legacy_messages_hash"]
        samples.append({
            "query_index": query_index,
            "content_hash": artifact.content_hash,
            "expected_content_hash": expected_content_hash,
            "content_hash_unchanged": artifact.content_hash == expected_content_hash,
            "messages_hash": artifact.messages_hash,
            "expected_messages_hash": expected_messages_hash,
            "messages_hash_unchanged": artifact.messages_hash == expected_messages_hash,
            "messages_equal_legacy_runtime": artifact.messages == sample["runtime"]["messages"],
            "estimated_tokens": artifact.estimated_tokens,
            "source_manifest_count": len(artifact.source_manifest),
        })
    all_unchanged = all(
        item["content_hash_unchanged"]
        and item["messages_hash_unchanged"]
        and item["messages_equal_legacy_runtime"]
        for item in samples
    )
    return {
        "schema_version": 1,
        "phase": "Phase 4R Batch R1",
        "purpose": "behavior-preserving PromptBuilder boundary audit",
        "writer_generation_calls": 0,
        "llm_calls": 0,
        "query_count": len(samples),
        "production_behavior_changed": False,
        "rules_snapshot": rules_snapshot,
        "samples": samples,
        "acceptance": {
            "all_ten_content_hashes_unchanged": all(item["content_hash_unchanged"] for item in samples),
            "all_ten_messages_hashes_unchanged": all(item["messages_hash_unchanged"] for item in samples),
            "all_ten_messages_equal_legacy_runtime": all(item["messages_equal_legacy_runtime"] for item in samples),
            "all_r1_hash_gates": all_unchanged and len(samples) == 10,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = run_audit()
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"phase4r-r1 hash audit: {result['query_count']} samples, "
        f"pass={result['acceptance']['all_r1_hash_gates']}"
    )
    if not result["acceptance"]["all_r1_hash_gates"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
