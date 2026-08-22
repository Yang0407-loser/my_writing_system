"""WR3.5 metadata wiring benchmark (offline, isolated collection).

Evaluates whether WR3.5-shaped retrieval metadata (characters/locations/
weekday/time) closes W6-type coarse recall gaps.  The benchmark builds a
dedicated isolated Chroma collection from the frozen corpus snapshot with
deterministic text-derived WR3.5-shaped metadata, then compares:

- vector_only:        the tuned retrieval path (wr4_rich + v1_025) unchanged;
- metadata_supplement: the same planner, plus metadata-recalled candidates
                       (chunks whose metadata contains query characters or
                       locations) merged into the coarse pool;
- metadata_rerank:     metadata supplement + a metadata-evidence-aware reranker
                       configuration (wr35_metadata_020).

No production Chroma collection is touched; no LLM calls; production stays off.
The metadata is a WR3.5-shape simulation because the corpus predates WR
commits: ``metadata_source=wr35_shape_text_derived_v1``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import chromadb
from chromadb.config import Settings as ChromaSettings

from app.context_contracts import serialize_chroma_metadata
from app.embedding.factory import get_embedding_provider
from app.retrieval_pipeline import merge_candidates
from app.vector_store import _ChromaEmbedFn
from app.retrieval_pipeline import _decode_metadata_list

from gold_retrieval_baseline_v1 import score_entry, text_hash
from gold_retrieval_tune_v1 import _aggregate
from wr4_tuning_components import (
    CHARACTER_NAMES,
    LOCATION_LEXICON,
    PLANNER_REGISTRY,
    RERANKER_REGISTRY,
    rerank_candidates,
)


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
CORPUS_SNAPSHOT = FIXTURES / "gold_retrieval_corpus_snapshot_v1.json"
DEV_FIXTURE = FIXTURES / "wr4_gold_retrieval_v1.json"
RUNTIME_DIR = ROOT / ".world_runtime_wr4_metadata_bench_runtime"
REPORT_JSON = ROOT / "reports" / "wr4-metadata-benchmark-2026-08-07.json"
REPORT_MD = ROOT / "reports" / "world-runtime-wr4-metadata-benchmark-2026-08-07.md"

DEV_TASK_ID = "07d1391e-06ff-4af3-8bd7-6a404d2f4fd6"
COLLECTION_NAME = "writing_paragraphs_wr35_bench"
WEEKDAY_TERMS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
TIME_PATTERN = re.compile(
    r"(?:凌晨|清晨|早上|上午|中午|下午|傍晚|晚上|夜里)?\s*\d{1,2}[点时]\s*\d{0,2}半?"
)


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def build_augmented_metadata(row: dict[str, Any]) -> dict[str, Any]:
    text = str(row.get("text", ""))
    characters = sorted({name for name in CHARACTER_NAMES if name in text})
    locations = sorted(
        {
            canonical
            for term, canonical in LOCATION_LEXICON.items()
            if term in text
        }
    )
    weekdays = sorted({term for term in WEEKDAY_TERMS if term in text})
    times = sorted({match.group(0).strip() for match in TIME_PATTERN.finditer(text)})[:8]
    return {
        "characters": characters,
        "locations": locations,
        "weekday": weekdays,
        "time": times,
        "characters_text": " ".join(characters),
        "locations_text": " ".join(locations),
        "metadata_source": "wr35_shape_text_derived_v1",
    }


def build_isolated_collection(snapshot: dict[str, Any], task_id: str) -> tuple[Any, Any]:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(
        path=str(RUNTIME_DIR),
        settings=ChromaSettings(anonymized_telemetry=False),
    )
    provider = get_embedding_provider()
    collection = client.get_or_create_collection(
        name=COLLECTION_NAME,
        embedding_function=_ChromaEmbedFn(provider),
    )
    rows = snapshot["tasks"][task_id]["rows"]
    if collection.count() == len(rows):
        return collection, client
    ids: list[str] = []
    documents: list[str] = []
    metadatas: list[dict[str, Any]] = []
    for row in rows:
        metadata: dict[str, Any] = {
            "task_id": task_id,
            "section": row["section"],
            "subsection": row["subsection"],
            "title": row["title"],
            "topic": row["topic"],
            "content_hash": row["content_hash"],
        }
        metadata.update(build_augmented_metadata(row))
        ids.append(str(row["content_hash"]))
        documents.append(str(row["text"]))
        metadatas.append(serialize_chroma_metadata(metadata))
    collection.add(ids=ids, documents=documents, metadatas=metadatas)
    return collection, client


def _search(collection: Any, query: str, k: int, task_id: str) -> list[dict[str, Any]]:
    result = collection.query(
        query_texts=[query],
        n_results=k,
        where={"task_id": task_id},
    )
    items: list[dict[str, Any]] = []
    ids = result.get("ids", [[]])[0] or []
    docs = result.get("documents", [[]])[0] or []
    metas = result.get("metadatas", [[]])[0] or []
    distances = result.get("distances", [[]])[0] or []
    for index, doc in enumerate(ids):
        meta = metas[index] if index < len(metas) and metas[index] else {}
        distance = distances[index] if index < len(distances) else None
        score = None
        if isinstance(distance, (int, float)):
            score = round(1.0 / (1.0 + max(float(distance), 0.0)), 6)
        items.append(
            {
                "id": doc,
                "text": docs[index] if index < len(docs) else "",
                "section": meta.get("section", 0),
                "subsection": meta.get("subsection", 0),
                "title": meta.get("title", ""),
                "distance": distance,
                "score": score,
                "metadata": dict(meta),
                "rank": index + 1,
            }
        )
    return items


def _metadata_supplement(
    corpus_items: list[dict[str, Any]],
    query_text: str,
    *,
    total_limit: int = 80,
) -> list[dict[str, Any]]:
    required_chars = [name for name in CHARACTER_NAMES if name in query_text]
    required_locs = [
        canonical
        for term, canonical in LOCATION_LEXICON.items()
        if term in query_text
    ]
    if not required_chars and not required_locs:
        return []
    matched: list[dict[str, Any]] = []
    for item in corpus_items:
        meta = item.get("metadata") or {}
        item_chars = set(_decode_metadata_list(meta.get("characters")))
        item_locs = set(_decode_metadata_list(meta.get("locations")))
        if (set(required_chars) & item_chars) or (set(required_locs) & item_locs):
            matched.append(item)
            if len(matched) >= total_limit:
                break
    return matched


def load_corpus_items(collection: Any, task_id: str) -> list[dict[str, Any]]:
    result = collection.get(
        where={"task_id": task_id},
        include=["documents", "metadatas"],
    )
    items: list[dict[str, Any]] = []
    for index, doc_id in enumerate(result.get("ids", [])):
        meta = (
            result["metadatas"][index]
            if index < len(result.get("metadatas", []))
            else {}
        )
        items.append(
            {
                "id": doc_id,
                "text": result["documents"][index],
                "section": meta.get("section", 0),
                "subsection": meta.get("subsection", 0),
                "title": meta.get("title", ""),
                "distance": None,
                "score": None,
                "metadata": dict(meta),
                "rank": 0,
            }
        )
    return items


def run_variant(
    collection: Any,
    corpus_items: list[dict[str, Any]],
    entry: dict[str, Any],
    planner,
    reranker_name: str,
    *,
    task_id: str,
    coarse_k: int,
    supplement: bool,
) -> dict[str, Any]:
    config = dict(RERANKER_REGISTRY[reranker_name])
    plan = planner.plan_text(
        entry["query"],
        requested_intents=entry["query_intent"],
        character_names=CHARACTER_NAMES,
        current_section=int(entry["section"]),
        current_subsection=int(entry["subsection"]),
    )
    query_results = []
    for planned_query in plan.queries:
        items = _search(collection, planned_query.query, coarse_k, task_id)
        if supplement:
            items.extend(_metadata_supplement(corpus_items, planned_query.query))
        query_results.append((planned_query, items))
    merged = merge_candidates(query_results)
    ranked = rerank_candidates(plan, merged, **config)
    selected = ranked["selected"]
    run = {
        "query_index": entry["query_index"],
        "selected_ids": [str(item.get("id", "")) for item in selected],
        "selected_sections": [int(item.get("section") or 0) for item in selected],
        "selected_hashes": [text_hash(str(item.get("text", ""))) for item in selected],
        "selected_texts": [str(item.get("text", "")) for item in selected],
    }
    return {
        "score": score_entry(entry, run),
        "merged_count": len(merged),
        "selected_count": len(selected),
        "supplement_count": len(merged)
        - sum(1 for item in merged if item.get("rank", 0) > 0),
        "selected_sections": [int(item.get("section") or 0) for item in selected],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--coarse-k", type=int, default=24)
    parser.add_argument("--rebuild", action="store_true")
    args = parser.parse_args()

    snapshot = _load_json(CORPUS_SNAPSHOT)
    fixture = _load_json(DEV_FIXTURE)
    task = snapshot["tasks"][DEV_TASK_ID]
    if fixture["corpus"]["corpus_hash"] != task["corpus_hash"]:
        raise SystemExit("dev fixture corpus hash mismatch")
    if args.rebuild and RUNTIME_DIR.exists():
        import shutil

        shutil.rmtree(RUNTIME_DIR)

    collection, client = build_isolated_collection(snapshot, DEV_TASK_ID)
    corpus_items = load_corpus_items(collection, DEV_TASK_ID)
    started = time.perf_counter()
    planner = PLANNER_REGISTRY["wr4_rich"]()
    variants = {
        "vector_only": {"reranker": "v1_025", "supplement": False},
        "metadata_supplement": {"reranker": "v1_025", "supplement": True},
        "metadata_rerank": {"reranker": "wr35_metadata_020", "supplement": True},
    }
    results: dict[str, list[dict[str, Any]]] = {}
    for name, spec in variants.items():
        per_query = [
            run_variant(
                collection,
                corpus_items,
                entry,
                planner,
                spec["reranker"],
                task_id=DEV_TASK_ID,
                coarse_k=args.coarse_k,
                supplement=spec["supplement"],
            )
            for entry in fixture["entries"]
        ]
        results[name] = per_query
    elapsed = round(time.perf_counter() - started, 3)

    def aggregate(name: str) -> dict[str, Any]:
        return _aggregate([item["score"] for item in results[name]])

    def tier_aggregate(name: str, tier: str) -> dict[str, Any]:
        return _aggregate(
            [
                item["score"]
                for item in results[name]
                if item["score"]["tier"] == tier
            ]
        )

    live_tuned = _load_json(
        ROOT / "reports" / "wr4-gold-retrieval-tuned-2026-08-07.json"
    )
    live_all = live_tuned["aggregates"]["all"]
    live_b = live_tuned["aggregates"]["wr_key_evidence"]
    isolated = aggregate("vector_only")
    parity_ok = (
        abs((isolated["section_recall_at_5"] or 0.0) - (live_all["section_recall_at_5"] or 0.0)) <= 0.05
        and abs((isolated["fact_mention_recall"] or 0.0) - (live_all["fact_mention_recall"] or 0.0)) <= 0.05
        and abs(
            (tier_aggregate("vector_only", "wr_key_evidence")["section_recall_at_5"] or 0.0)
            - (live_b["section_recall_at_5"] or 0.0)
        ) <= 0.05
    )

    report = {
        "schema_version": "wr4-metadata-benchmark-v1",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "corpus": {
            "task_id": DEV_TASK_ID,
            "corpus_hash": task["corpus_hash"],
            "chunk_count": task["chunk_count"],
        },
        "metadata": {
            "source": "wr35_shape_text_derived_v1",
            "fields": ["characters", "locations", "weekday", "time"],
            "note": (
                "simulation of the WR3.5 write-side projection; the corpus "
                "predates WR commits, so metadata is deterministically derived "
                "from chunk text"
            ),
        },
        "profile": {
            "coarse_k": args.coarse_k,
            "planner": "wr4_rich",
            "variants": variants,
            "isolated_chroma": str(RUNTIME_DIR),
            "embedding_provider": "ollama:bge-m3 (local)",
            "llm_calls": 0,
            "chroma_writes": "isolated_bench_collection_only",
            "production_unchanged": True,
        },
        "gates": {
            "isolated_parity_with_live_tuned": parity_ok,
            "w6_recall_recovered": any(
                item["score"]["query_index"] == "W6"
                and item["score"]["section_recall"] and item["score"]["section_recall"] > 0
                for item in results["metadata_rerank"]
            ),
        },
        "aggregates": {
            name: {
                "all": aggregate(name),
                "legacy_author_labeled": tier_aggregate(name, "legacy_author_labeled"),
                "wr_key_evidence": tier_aggregate(name, "wr_key_evidence"),
            }
            for name in variants
        },
        "live_tuned_reference": {
            "all": live_all,
            "wr_key_evidence": live_b,
        },
        "per_query": {
            name: [
                {
                    "query_index": item["score"]["query_index"],
                    "tier": item["score"]["tier"],
                    "section_precision": item["score"]["section_precision"],
                    "section_recall": item["score"]["section_recall"],
                    "gold_section_hits": item["score"]["gold_section_hits"],
                    "gold_sections": item["score"]["gold_sections"],
                    "fact_mention_recall": item["score"]["fact_mention_recall"],
                    "fact_hits": item["score"]["fact_hits"],
                    "fact_total": item["score"]["fact_total"],
                    "selected_sections_sorted": item["score"]["selected_sections_sorted"],
                    "merged_count": item["merged_count"],
                    "supplement_count": item["supplement_count"],
                }
                for item in results[name]
            ]
            for name in variants
        },
        "elapsed_seconds": elapsed,
    }
    report["decision"] = (
        "metadata_wiring_effective"
        if report["gates"]["w6_recall_recovered"]
        else "metadata_wiring_ineffective"
    )
    REPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
    REPORT_JSON.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    REPORT_MD.write_text(render_markdown(report), encoding="utf-8")
    manifest = {
        "schema_version": "wr4-metadata-bench-manifest-v1",
        "report": REPORT_JSON.name,
        "report_sha256": hashlib.sha256(REPORT_JSON.read_bytes()).hexdigest(),
        "corpus_hash": task["corpus_hash"],
        "isolated_collection_count": collection.count(),
        "llm_calls": 0,
        "production_unchanged": True,
    }
    (RUNTIME_DIR / "benchmark-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "gates": report["gates"],
                "aggregates_vector_only": report["aggregates"]["vector_only"]["all"],
                "aggregates_metadata_rerank": report["aggregates"]["metadata_rerank"]["all"],
                "tier_b_metadata_rerank": report["aggregates"]["metadata_rerank"]["wr_key_evidence"],
                "decision": report["decision"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def render_markdown(report: dict[str, Any]) -> str:
    def table(name: str) -> str:
        agg = report["aggregates"][name]
        rows = []
        for label in ("all", "legacy_author_labeled", "wr_key_evidence"):
            values = agg[label]
            rows.append(
                f"| {label} | {values['section_precision_at_5']} | "
                f"{values['section_recall_at_5']} | {values['fact_mention_recall']} | "
                f"{values['zero_result_queries']} | {values['queries']} |"
            )
        return (
            "| segment | p@5 | r@5 | fact recall | zero | queries |\n"
            "|---|---:|---:|---:|---:|---:|\n" + "\n".join(rows)
        )

    lines = [
        "# World Runtime WR4：WR3.5 metadata 接线基准 结果报告",
        "",
        "日期：2026-08-07",
        "",
        f"- 语料：task `{report['corpus']['task_id']}`，{report['corpus']['chunk_count']} chunks，"
        f"corpus hash `{report['corpus']['corpus_hash'][:12]}…`",
        f"- metadata：`{report['metadata']['source']}`（字段 "
        f"{'/'.join(report['metadata']['fields'])}）；隔离 chroma 集合，零 LLM、"
        "生产 off、生产集合未触碰。",
        f"- 隔离集合与 live tuned 一致性 gate：{report['gates']['isolated_parity_with_live_tuned']}",
        "",
        "## 1. 变体",
        "",
        "| variant | reranker | supplement |",
        "|---|---|---|",
        "| vector_only | v1_025 | 无 |",
        "| metadata_supplement | v1_025 | 按查询人物/地点补充粗召回 |",
        "| metadata_rerank | wr35_metadata_020 | 补充 + metadata_evidence 权重 0.13、min_score 0.20 |",
        "",
        "## 2. 聚合",
        "",
        "### vector_only（隔离集合 = live tuned 复现）",
        "",
        table("vector_only"),
        "",
        "### metadata_supplement",
        "",
        table("metadata_supplement"),
        "",
        "### metadata_rerank",
        "",
        table("metadata_rerank"),
        "",
        "## 3. W6 类缺口查询（metadata_rerank）",
        "",
        "| query | p@5 | r@5 | gold hits | fact | selected sections |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for item in report["per_query"]["metadata_rerank"]:
        score = item
        if score["query_index"] not in ("W2", "W4", "W5", "W6", "W8"):
            continue
        lines.append(
            f"| {score['query_index']} | {score['section_precision']} | "
            f"{score['section_recall']} | {score['gold_section_hits']}/{score['gold_sections']} | "
            f"{score['fact_hits']}/{score['fact_total']} | "
            f"{','.join(map(str, score['selected_sections_sorted'])) or '-'} |"
        )
    lines += [
        "",
        "## 4. 门禁",
        "",
        f"- isolated_parity_with_live_tuned: {report['gates']['isolated_parity_with_live_tuned']}",
        f"- w6_recall_recovered: {report['gates']['w6_recall_recovered']}",
        "",
        f"## 5. 结论：{report['decision']}",
        "",
        "机器可读数据：[wr4-metadata-benchmark-2026-08-07.json]"
        "(E:/writer/my_writing_system/reports/wr4-metadata-benchmark-2026-08-07.json)",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
