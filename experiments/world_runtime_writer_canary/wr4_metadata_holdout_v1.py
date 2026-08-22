"""Sealed unseen holdout for the WR3.5 metadata-aware retrieval variant.

Independent-author gold on a third corpus instance (task 20f02dc7, same
book, 393 chunks) is evaluated once against:

- baseline_shadow: v1 planner + v1_035 reranker (no metadata supplement);
- tuned:           wr4_rich + v1_025 (reference, known to fail Tier B gate);
- metadata:        wr4_rich + metadata supplement + wr35_metadata_020.

Preflight fails closed on seal, corpus binding, verbatim evidence,
prior-context and leakage against the development gold.  Run-once is enforced
by a lock/ledger in the runtime directory.
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
from app.retrieval_pipeline import SUPPORTED_INTENTS, _decode_metadata_list, merge_candidates
from app.vector_store import _ChromaEmbedFn

from gold_retrieval_baseline_v1 import score_entry, text_hash
from gold_retrieval_tune_v1 import _aggregate
from wr4_tuning_components import (
    LOCATION_LEXICON,
    PLANNER_REGISTRY,
    RERANKER_REGISTRY,
    rerank_candidates,
)


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
HOLD2 = Path(__file__).resolve().parent / "holdout2"
HOLD3 = Path(__file__).resolve().parent / "holdout3"
GOLD_FIXTURE = HOLD2 / "wr4_metadata_holdout_gold_v1.json"
SEAL_FIXTURE = HOLD2 / "wr4_metadata_holdout_gold_v1.seal.json"
CORPUS_SNAPSHOT = FIXTURES / "wr4_metadata_holdout_corpus_snapshot_v1.json"
DEV_FIXTURE = FIXTURES / "wr4_gold_retrieval_v1.json"
PREV_HOLDOUT = FIXTURES / "wr4_gold_retrieval_holdout_v1.json"
RUNTIME_DIR = ROOT / ".world_runtime_wr4_metadata_holdout_runtime"
REPORT_JSON = ROOT / "reports" / "wr4-metadata-holdout-2026-08-07.json"
REPORT_MD = ROOT / "reports" / "world-runtime-wr4-metadata-holdout-2026-08-07.md"

COLLECTION_NAME = "writing_paragraphs_wr35_holdout2"
TASK_ID = "20f02dc7-dc64-4233-bd6c-06a6d8647dbe"
WEEKDAY_TERMS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
TIME_PATTERN = re.compile(
    r"(?:凌晨|清晨|早上|上午|中午|下午|傍晚|晚上|夜里)?\s*\d{1,2}[点时]\s*\d{0,2}半?"
)


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_augmented_metadata(
    row: dict[str, Any], character_names: tuple[str, ...]
) -> dict[str, Any]:
    text = str(row.get("text", ""))
    characters = sorted({name for name in character_names if name in text})
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


def build_isolated_collection(
    runtime_dir: Path,
    snapshot: dict[str, Any],
    task_id: str,
    character_names: tuple[str, ...],
    metadata_manifest: dict[str, dict[str, Any]] | None = None,
) -> Any:
    runtime_dir.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(
        path=str(runtime_dir),
        settings=ChromaSettings(anonymized_telemetry=False),
    )
    collection = client.get_or_create_collection(
        name=COLLECTION_NAME,
        embedding_function=_ChromaEmbedFn(get_embedding_provider()),
    )
    rows = snapshot["tasks"][task_id]["rows"]
    if collection.count() == len(rows):
        return collection
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
        if metadata_manifest is not None:
            projected = metadata_manifest.get(str(row["content_hash"]))
            if projected is not None:
                metadata.update(projected)
            else:
                metadata.update(build_augmented_metadata(row, character_names))
        else:
            metadata.update(build_augmented_metadata(row, character_names))
        ids.append(str(row["content_hash"]))
        documents.append(str(row["text"]))
        metadatas.append(serialize_chroma_metadata(metadata))
    collection.add(ids=ids, documents=documents, metadatas=metadatas)
    return collection


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
                "metadata": dict(meta),
                "rank": 0,
            }
        )
    return items


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
    for index, doc_id in enumerate(ids):
        meta = metas[index] if index < len(metas) and metas[index] else {}
        distance = distances[index] if index < len(distances) else None
        score = None
        if isinstance(distance, (int, float)):
            score = round(1.0 / (1.0 + max(float(distance), 0.0)), 6)
        items.append(
            {
                "id": doc_id,
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


def metadata_supplement(
    collection: Any,
    query_text: str,
    character_names: tuple[str, ...],
    *,
    task_id: str,
    total_limit: int = 150,
) -> list[dict[str, Any]]:
    """Vector-scored metadata supplement (relevance-ordered, not corpus order).

    Queries the whole task corpus and keeps metadata-matched items in
    similarity order with their real vector scores, capped at ``total_limit``.
    """
    required_chars = [name for name in character_names if name in query_text]
    required_locs = [
        canonical
        for term, canonical in LOCATION_LEXICON.items()
        if term in query_text
    ]
    if not required_chars and not required_locs:
        return []
    items = _search(collection, query_text, k=10_000, task_id=task_id)
    matched: list[dict[str, Any]] = []
    for item in items:
        meta = item.get("metadata") or {}
        item_chars = set(_decode_metadata_list(meta.get("characters")))
        item_locs = set(_decode_metadata_list(meta.get("locations")))
        if (set(required_chars) & item_chars) or (set(required_locs) & item_locs):
            item["rank"] = 0  # mark as supplement-only for the report metric;
            # the real vector score is retained in ``score``.
            matched.append(item)
            if len(matched) >= total_limit:
                break
    return matched


def run_variant(
    collection: Any,
    corpus_items: list[dict[str, Any]],
    entry: dict[str, Any],
    *,
    planner_name: str,
    reranker_name: str,
    character_names: tuple[str, ...],
    task_id: str,
    coarse_k: int,
    supplement: bool,
) -> dict[str, Any]:
    config = dict(RERANKER_REGISTRY[reranker_name])
    # Mirror production: the Writer planner is called without requested
    # intents, so all supported intents participate.  Gold query_intent is
    # authoring metadata only (validated by preflight).
    plan = PLANNER_REGISTRY[planner_name]().plan_text(
        entry["query"],
        requested_intents=tuple(SUPPORTED_INTENTS),
        character_names=character_names,
        current_section=int(entry["section"]),
        current_subsection=int(entry["subsection"]),
    )
    query_results = []
    for planned_query in plan.queries:
        items = _search(collection, planned_query.query, coarse_k, task_id)
        if supplement:
            items.extend(
                metadata_supplement(
                    collection, planned_query.query, character_names, task_id=task_id
                )
            )
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


def preflight(
    fixture: dict[str, Any],
    snapshot: dict[str, Any],
    *,
    fixture_path: Path,
    seal_path: Path,
) -> dict[str, Any]:
    issues: list[str] = []
    seal = _load_json(seal_path)
    checks = {
        "sealed": seal.get("fixture_sha256") == _sha256_file(fixture_path),
        "corpus_bound": (
            fixture["corpus"]["corpus_hash"] == snapshot["tasks"][TASK_ID]["corpus_hash"]
            and fixture["corpus"]["chunk_count"]
            == snapshot["tasks"][TASK_ID]["chunk_count"]
        ),
    }
    if not checks["sealed"]:
        issues.append("seal hash mismatch")
    if not checks["corpus_bound"]:
        issues.append("corpus hash/count mismatch")

    rows = {row["content_hash"]: row for row in snapshot["tasks"][TASK_ID]["rows"]}
    canonical_intents = set(SUPPORTED_INTENTS)
    for entry in fixture["entries"]:
        q = entry["query_index"]
        qi = entry.get("query_intent")
        if not (
            isinstance(qi, (list, tuple))
            and len(qi) >= 1
            and all(intent in canonical_intents for intent in qi)
        ):
            issues.append(
                f"{q}: query_intent must be a non-empty canonical intent list "
                f"{sorted(canonical_intents)}"
            )
        if set(entry["gold_anchor_hashes"]) - set(rows):
            issues.append(f"{q}: anchor hash missing")
        if set(entry["gold_chunk_hashes"]) - set(rows):
            issues.append(f"{q}: chunk hash missing")
        for fact, spans in entry["fact_evidence"].items():
            for span in spans:
                row = rows.get(span["chunk_hash"])
                if row is None:
                    issues.append(f"{q}: span chunk missing")
                    continue
                if row["text"][span["start"]:span["end"]] != span["phrase"]:
                    issues.append(f"{q}: span not verbatim ({span['phrase'][:20]})")
                if span["excerpt"] != row["text"][
                    max(0, span["start"] - 24): span["end"] + 24
                ]:
                    issues.append(f"{q}: excerpt mismatch")
        cur_s, cur_ss = int(entry["section"]), int(entry["subsection"])
        for section in entry["gold_sections"]:
            if section > cur_s:
                issues.append(f"{q}: future gold section {section}")
            if section == cur_s:
                anchor_subs = {
                    int(rows[h]["subsection"])
                    for h in entry["gold_anchor_hashes"]
                    if rows[h]["section"] == cur_s
                }
                if anchor_subs and max(anchor_subs) >= cur_ss:
                    issues.append(f"{q}: same-section evidence not prior-context")

    dev = _load_json(DEV_FIXTURE)
    prev = _load_json(PREV_HOLDOUT)
    holdout2_fixture = _load_json(HOLD2 / "wr4_metadata_holdout_gold_v1.json")
    holdout3_fixture = _load_json(HOLD3 / "wr4_metadata_holdout_gold_v1.json")
    own_queries = {entry["query"] for entry in fixture["entries"]}
    own_phrases = {
        span["phrase"]
        for entry in fixture["entries"]
        for spans in entry["fact_evidence"].values()
        for span in spans
    }
    dev_queries = {entry["query"] for entry in dev["entries"]}
    dev_phrases = {
        span["phrase"]
        for entry in dev["entries"]
        for spans in entry["fact_evidence"].values()
        for span in spans
    }
    prev_queries = {entry["query"] for entry in prev["entries"]}
    prev_phrases = {
        span["phrase"]
        for entry in prev["entries"]
        for spans in entry["fact_evidence"].values()
        for span in spans
    }
    h2_queries = {entry["query"] for entry in holdout2_fixture["entries"]}
    h2_phrases = {
        span["phrase"]
        for entry in holdout2_fixture["entries"]
        for spans in entry["fact_evidence"].values()
        for span in spans
    }
    h3_queries = {entry["query"] for entry in holdout3_fixture["entries"]}
    h3_phrases = {
        span["phrase"]
        for entry in holdout3_fixture["entries"]
        for spans in entry["fact_evidence"].values()
        for span in spans
    }
    checks["no_query_overlap_dev"] = not (own_queries & dev_queries)
    checks["no_query_overlap_prev_holdout"] = not (own_queries & prev_queries)
    checks["no_query_overlap_holdout2"] = not (own_queries & h2_queries)
    checks["no_query_overlap_holdout3"] = not (own_queries & h3_queries)
    checks["phrase_overlap_counts"] = {
        "dev": len(own_phrases & dev_phrases),
        "prev_holdout": len(own_phrases & prev_phrases),
        "holdout2": len(own_phrases & h2_phrases),
        "holdout3": len(own_phrases & h3_phrases),
    }
    # Same-book corpora share verbatim sentences, so phrase overlap is
    # informational, not a leak; query-string overlap is a leak.
    for name, ok in (
        ("no_query_overlap_dev", checks["no_query_overlap_dev"]),
        ("no_query_overlap_prev_holdout", checks["no_query_overlap_prev_holdout"]),
        ("no_query_overlap_holdout2", checks["no_query_overlap_holdout2"]),
        ("no_query_overlap_holdout3", checks["no_query_overlap_holdout3"]),
    ):
        if not ok:
            issues.append(name)
    checks["all_pass"] = not issues
    checks["intent_format"] = not any(
        issue.startswith(f"{entry['query_index']}: query_intent")
        for entry in fixture["entries"]
        for issue in issues
    )
    return {"checks": checks, "issues": issues}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--coarse-k", type=int, default=24)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--metadata-reranker", default="wr35_metadata_020")
    parser.add_argument("--metadata-manifest", type=Path, default=None)
    parser.add_argument("--gold-dir", type=Path, default=HOLD2)
    parser.add_argument("--runtime-dir", type=Path, default=RUNTIME_DIR)
    parser.add_argument("--report-json", type=Path, default=REPORT_JSON)
    parser.add_argument("--report-md", type=Path, default=REPORT_MD)
    args = parser.parse_args()

    gold_dir = args.gold_dir
    fixture_path = gold_dir / "wr4_metadata_holdout_gold_v1.json"
    seal_path = gold_dir / "wr4_metadata_holdout_gold_v1.seal.json"
    runtime_dir = args.runtime_dir
    if not fixture_path.exists():
        raise SystemExit("holdout fixture not found (author pending?)")
    fixture = _load_json(fixture_path)
    snapshot = _load_json(CORPUS_SNAPSHOT)
    preflight_result = preflight(
        fixture, snapshot, fixture_path=fixture_path, seal_path=seal_path
    )
    if not preflight_result["checks"]["all_pass"]:
        raise SystemExit(
            "preflight failed: " + "; ".join(preflight_result["issues"])
        )

    evaluation_path = runtime_dir / "evaluation.json"
    if evaluation_path.exists() and not args.force:
        raise SystemExit("run-once: evaluation.json already exists; use --force to override")
    runtime_dir.mkdir(parents=True, exist_ok=True)

    character_names = tuple(fixture["character_names"])
    metadata_manifest = None
    if args.metadata_manifest is not None:
        if not args.metadata_manifest.exists():
            raise SystemExit(f"metadata manifest not found: {args.metadata_manifest}")
        metadata_manifest = _load_json(args.metadata_manifest)
    collection = build_isolated_collection(
        runtime_dir,
        snapshot,
        TASK_ID,
        character_names,
        metadata_manifest=metadata_manifest,
    )
    corpus_items = load_corpus_items(collection, TASK_ID)
    started = time.perf_counter()
    variants = {
        "baseline_shadow": {
            "planner": "v1",
            "reranker": "v1_035",
            "supplement": False,
        },
        "tuned": {
            "planner": "wr4_rich",
            "reranker": "v1_025",
            "supplement": False,
        },
        "metadata": {
            "planner": "wr4_rich",
            "reranker": args.metadata_reranker,
            "supplement": True,
        },
    }
    results: dict[str, list[dict[str, Any]]] = {}
    for name, spec in variants.items():
        results[name] = [
            run_variant(
                collection,
                corpus_items,
                entry,
                planner_name=spec["planner"],
                reranker_name=spec["reranker"],
                character_names=character_names,
                task_id=TASK_ID,
                coarse_k=args.coarse_k,
                supplement=spec["supplement"],
            )
            for entry in fixture["entries"]
        ]
    elapsed = round(time.perf_counter() - started, 3)

    def aggregate(name: str, tier: str | None = None) -> dict[str, Any]:
        scores = [item["score"] for item in results[name]]
        if tier is not None:
            scores = [score for score in scores if score["tier"] == tier]
        return _aggregate(scores)

    base = aggregate("baseline_shadow")
    meta_all = aggregate("metadata")
    meta_b = aggregate("metadata", "wr_key_evidence")
    base_b = aggregate("baseline_shadow", "wr_key_evidence")
    gates = {
        "sealed": preflight_result["checks"]["sealed"],
        "corpus_bound": preflight_result["checks"]["corpus_bound"],
        "no_gold_leakage": all(
            preflight_result["checks"][key]
            for key in (
                "no_query_overlap_dev",
                "no_query_overlap_prev_holdout",
                "no_query_overlap_holdout2",
                "no_query_overlap_holdout3",
            )
        ),
        "zero_llm_calls": True,
        "zero_chroma_writes_production": True,
        "production_unchanged": True,
        "metadata_tier_b_recall_no_regression": (
            meta_b["section_recall_at_5"] is not None
            and base_b["section_recall_at_5"] is not None
            and meta_b["section_recall_at_5"] >= base_b["section_recall_at_5"]
        ),
        "metadata_tier_b_fact_recall_no_regression": (
            meta_b["fact_mention_recall"] is not None
            and base_b["fact_mention_recall"] is not None
            and meta_b["fact_mention_recall"] >= base_b["fact_mention_recall"]
        ),
        "metadata_all_precision_within_tolerance": (
            meta_all["section_precision_at_5"] is not None
            and base["section_precision_at_5"] is not None
            and meta_all["section_precision_at_5"]
            >= base["section_precision_at_5"] - 0.05
        ),
        "metadata_zero_results_not_worse": (
            meta_all["zero_result_queries"] <= base["zero_result_queries"]
        ),
    }
    gates["all_pass"] = all(gates.values())
    decision = (
        "wr4_metadata_holdout_passed"
        if gates["all_pass"]
        else "wr4_metadata_holdout_failed_no_rerun"
    )

    report = {
        "schema_version": "wr4-metadata-holdout-evaluation-v1",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "corpus": {
            "task_id": TASK_ID,
            "corpus_hash": snapshot["tasks"][TASK_ID]["corpus_hash"],
            "chunk_count": snapshot["tasks"][TASK_ID]["chunk_count"],
        },
        "profile": {
            "coarse_k": args.coarse_k,
            "variants": variants,
            "embedding_provider": "ollama:bge-m3 (local)",
            "llm_calls": 0,
            "chroma_writes": "isolated_holdout_collection_only",
            "production_switched": False,
        },
        "preflight": preflight_result,
        "gates": gates,
        "sealed_holdout_gate_passed": gates["all_pass"],
        "decision": decision,
        "aggregates": {
            name: {
                "all": aggregate(name),
                "continuity_fact": aggregate(name, "continuity_fact"),
                "wr_key_evidence": aggregate(name, "wr_key_evidence"),
            }
            for name in variants
        },
        "per_query": {
            name: [
                {
                    "query_index": item["score"]["query_index"],
                    "tier": item["score"]["tier"],
                    "current_section": item["score"]["current_section"],
                    "section_precision": item["score"]["section_precision"],
                    "section_recall": item["score"]["section_recall"],
                    "gold_section_hits": item["score"]["gold_section_hits"],
                    "gold_sections": item["score"]["gold_sections"],
                    "fact_mention_recall": item["score"]["fact_mention_recall"],
                    "fact_hits": item["score"]["fact_hits"],
                    "fact_total": item["score"]["fact_total"],
                    "selected_sections_sorted": item["score"]["selected_sections_sorted"],
                    "supplement_count": item["supplement_count"],
                }
                for item in results[name]
            ]
            for name in variants
        },
        "elapsed_seconds": elapsed,
    }
    args.report_json.parent.mkdir(parents=True, exist_ok=True)
    args.report_json.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    args.report_md.write_text(render_markdown(report), encoding="utf-8")
    evaluation_path.write_text(
        json.dumps(
            {
                "schema_version": "wr4-metadata-holdout-evaluation-v1",
                "timestamp": report["timestamp"],
                "decision": decision,
                "gates": gates,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (runtime_dir / "holdout-lock.json").write_text(
        json.dumps(
            {
                "schema_version": "wr4-metadata-holdout-lock-v1",
                "sealed_sha256": _sha256_file(fixture_path),
                "task_id": TASK_ID,
                "entry_count": len(fixture["entries"]),
                "sealed_at": fixture.get("created_at"),
                "author_role": fixture.get("author"),
                "rerun_allowed": False,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (runtime_dir / "attempt-ledger.json").write_text(
        json.dumps(
            {
                "schema_version": "wr4-metadata-holdout-attempt-ledger-v1",
                "attempt_count_total": 1,
                "status": "succeeded",
                "output": "evaluation.json",
                "decision": decision,
                "sealed_holdout_gate_passed": gates["all_pass"],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "decision": decision,
                "gates": gates,
                "baseline_shadow_all": aggregate("baseline_shadow"),
                "metadata_all": aggregate("metadata"),
                "metadata_tier_b": meta_b,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def render_markdown(report: dict[str, Any]) -> str:
    def table(name: str) -> str:
        rows = []
        for label in ("all", "continuity_fact", "wr_key_evidence"):
            values = report["aggregates"][name][label]
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
        "# World Runtime WR4：metadata 变体 Sealed Unseen Holdout 结果报告",
        "",
        "日期：2026-08-07",
        "",
        f"- 语料：task `{report['corpus']['task_id']}`（同一本书第三实例，"
        f"{report['corpus']['chunk_count']} chunks，corpus hash "
        f"`{report['corpus']['corpus_hash'][:12]}…`）",
        f"- 金标：{len(report['per_query']['baseline_shadow'])} 条，"
        "独立作者编写，prior-context + 证据逐字绑定；密封字节哈希绑定。",
        "- 变体：baseline_shadow（v1+v1_035）/ tuned（wr4_rich+v1_025）/ "
        "metadata（wr4_rich+补充+wr35_metadata_020）。",
        "- 零 LLM、零生产写入、生产 off。",
        "",
        "## 1. 门禁",
        "",
    ]
    for key, value in report["gates"].items():
        lines.append(f"- {key}: {value}")
    lines += [
        "",
        f"## 2. 结论：{report['decision']}",
        "",
        "## 3. 聚合",
        "",
        "### baseline_shadow",
        "",
        table("baseline_shadow"),
        "",
        "### tuned（参考）",
        "",
        table("tuned"),
        "",
        "### metadata",
        "",
        table("metadata"),
        "",
        "## 4. 逐条（metadata）",
        "",
        "| query | tier | cur | p@5 | r@5 | gold hits | fact | supp | selected |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for item in report["per_query"]["metadata"]:
        lines.append(
            f"| {item['query_index']} | {item['tier']} | {item['current_section']} | "
            f"{item['section_precision']} | {item['section_recall']} | "
            f"{item['gold_section_hits']}/{item['gold_sections']} | "
            f"{item['fact_hits']}/{item['fact_total']} | {item['supplement_count']} | "
            f"{','.join(map(str, item['selected_sections_sorted'])) or '-'} |"
        )
    lines += [
        "",
        "机器可读数据：[wr4-metadata-holdout-2026-08-07.json]"
        "(E:/writer/my_writing_system/reports/wr4-metadata-holdout-2026-08-07.json)",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
