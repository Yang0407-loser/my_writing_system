"""Run the WR4 gold-retrieval baseline against the current retrieval paths.

Two paths are measured on the frozen WR4 gold set:

- legacy: ``VectorStore.search_with_meta(query, k=5, task_id=...)``, the path
  Writer actually consumes;
- shadow: ``QueryPlanner`` + ``ShadowRetriever`` + ``ExplainableReranker``,
  the Phase 3 shadow path (never wired into Writer).

The runner is deterministic and offline (local embedding only, zero LLM
calls, zero Chroma writes, zero production switches).  It fails closed when
the live corpus no longer matches the frozen snapshot hash.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.retrieval_pipeline import QueryPlanner, ShadowRetriever
from app.vector_store import VectorStore


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
GOLD_FIXTURE = FIXTURES / "wr4_gold_retrieval_v1.json"
CORPUS_SNAPSHOT = FIXTURES / "gold_retrieval_corpus_snapshot_v1.json"
DEFAULT_JSON = ROOT / "reports" / "wr4-gold-retrieval-baseline-2026-08-07.json"
DEFAULT_MD = ROOT / "reports" / "world-runtime-wr4-gold-retrieval-baseline-2026-08-07.md"

CHARACTER_NAMES = ("林晚", "周野", "季晴", "顾衍", "吴阿姨")


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def text_hash(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def verify_corpus(store: VectorStore, fixture: dict[str, Any]) -> dict[str, Any]:
    """Fail closed unless the live corpus matches the frozen snapshot."""
    task_id = fixture["corpus"]["task_id"]
    expected = fixture["corpus"]["corpus_hash"]
    got = store._collection.get(
        where={"task_id": task_id}, include=["documents", "metadatas"]
    )
    docs = got.get("documents") or []
    metas = got.get("metadatas") or []
    ordered = sorted(
        zip(metas, docs),
        key=lambda pair: (
            int(pair[0].get("section") or 0),
            int(pair[0].get("subsection") or 0),
            str(pair[0].get("title") or ""),
            hashlib.sha256(str(pair[1]).strip().encode("utf-8")).hexdigest(),
        ),
    )
    hashes = [
        hashlib.sha256(str(doc).strip().encode("utf-8")).hexdigest()
        for _, doc in ordered
    ]
    actual = hashlib.sha256(
        json.dumps(hashes, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    return {
        "task_id": task_id,
        "expected_hash": expected,
        "actual_hash": actual,
        "chunk_count": len(docs),
        "expected_chunk_count": fixture["corpus"]["chunk_count"],
        "match": actual == expected and len(docs) == fixture["corpus"]["chunk_count"],
    }


def legacy_run(store: VectorStore, entry: dict[str, Any], task_id: str, k: int) -> dict[str, Any]:
    items = store.search_with_meta(entry["query"], k=k, task_id=task_id)
    return {
        "query_index": entry["query_index"],
        "selected_ids": [str(item.get("id", "")) for item in items],
        "selected_sections": [int(item.get("section") or 0) for item in items],
        "selected_hashes": [text_hash(item.get("text", "")) for item in items],
        "selected_texts": [str(item.get("text", "")) for item in items],
        "candidate_count": store.last_search_trace.get("candidate_count", len(items)),
        "elapsed_ms": round(store.last_search_trace.get("elapsed_ms", 0.0), 3),
    }


def shadow_run(
    store: VectorStore,
    entry: dict[str, Any],
    task_id: str,
    *,
    max_queries: int,
    candidate_k: int,
    min_score: float,
) -> dict[str, Any]:
    plan = QueryPlanner(max_queries=max_queries).plan_text(
        entry["query"],
        requested_intents=entry["query_intent"],
        character_names=CHARACTER_NAMES,
        current_section=int(entry["section"]),
        current_subsection=int(entry["subsection"]),
    )
    result = ShadowRetriever(
        candidate_k=candidate_k,
        min_score=min_score,
        max_results=5,
    ).run(store, plan, task_id=task_id)
    selected_traces = [
        item for item in result["rerank"]["candidates"] if item.get("selected")
    ]
    selected_sections = [
        int(item.get("section") or 0) for item in selected_traces
    ]
    selected_hashes = [str(item.get("text_hash", "")) for item in selected_traces]
    # The shadow trace intentionally omits body text; fetch selected chunks
    # read-only by id so fact-mention scoring stays deterministic.
    selected_ids = list(result["selected_ids"])
    text_by_id: dict[str, str] = {}
    if selected_ids:
        got = store._collection.get(
            ids=[str(item_id) for item_id in selected_ids],
            include=["documents"],
        )
        for item_id, doc in zip(got.get("ids", []), got.get("documents", [])):
            text_by_id[str(item_id)] = str(doc or "")
    return {
        "query_index": entry["query_index"],
        "plan": result["plan"],
        "selected_ids": selected_ids,
        "selected_sections": selected_sections,
        "selected_hashes": selected_hashes,
        "selected_texts": [
            text_by_id.get(str(item_id), "") for item_id in selected_ids
        ],
        "candidate_count": result["merged_candidate_count"],
        "elapsed_ms": result["elapsed_ms"],
        "rerank_trace": selected_traces,
    }


def fact_mention_hits(entry: dict[str, Any], selected_texts: list[str]) -> dict[str, Any]:
    joined = "\n".join(selected_texts)
    per_fact: list[dict[str, Any]] = []
    for fact in entry["must_recall_facts"]:
        spans = list(entry["fact_evidence"].get(fact, []))
        matched_phrases: list[str] = []
        for span in spans:
            phrase = str(span.get("phrase", ""))
            if phrase and phrase in joined:
                matched_phrases.append(phrase)
        per_fact.append(
            {
                "fact": fact,
                "hit": bool(matched_phrases),
                "matched_phrases": matched_phrases,
            }
        )
    hits = sum(1 for item in per_fact if item["hit"])
    return {
        "hits": hits,
        "total": len(per_fact),
        "recall": round(hits / len(per_fact), 4) if per_fact else None,
        "per_fact": per_fact,
    }


def score_entry(
    entry: dict[str, Any],
    run: dict[str, Any],
) -> dict[str, Any]:
    selected_sections = set(int(section) for section in run["selected_sections"])
    gold_sections = set(int(section) for section in entry["gold_sections"])
    selected_hashes = set(run["selected_hashes"])
    gold_anchors = set(entry["gold_anchor_hashes"])
    gold_chunks = set(entry["gold_chunk_hashes"])
    section_hits = selected_sections & gold_sections
    anchor_hits = selected_hashes & gold_anchors
    chunk_hits = selected_hashes & gold_chunks
    fact = fact_mention_hits(entry, run["selected_texts"])
    return {
        "query_index": entry["query_index"],
        "tier": entry["tier"],
        "current_section": int(entry["section"]),
        "selected_count": len(selected_sections),
        "section_precision": (
            round(len(section_hits) / len(selected_sections), 4)
            if selected_sections
            else None
        ),
        "section_recall": (
            round(len(section_hits) / len(gold_sections), 4)
            if gold_sections
            else None
        ),
        "gold_section_hits": len(section_hits),
        "gold_sections": len(gold_sections),
        "anchor_recall": (
            round(len(anchor_hits) / len(gold_anchors), 4)
            if gold_anchors
            else None
        ),
        "gold_anchor_hits": len(anchor_hits),
        "gold_anchors": len(gold_anchors),
        "chunk_proxy_recall": (
            round(len(chunk_hits) / len(gold_chunks), 4)
            if gold_chunks
            else None
        ),
        "gold_chunk_hits": len(chunk_hits),
        "gold_chunks": len(gold_chunks),
        "fact_mention_recall": fact["recall"],
        "fact_hits": fact["hits"],
        "fact_total": fact["total"],
        "per_fact": fact["per_fact"],
        "selected_sections_sorted": sorted(selected_sections),
    }


def aggregate(scores: list[dict[str, Any]], *, late_only: bool = False) -> dict[str, Any]:
    rows = [
        score for score in scores
        if (not late_only or int(score["current_section"]) >= 13)
    ]
    if not rows:
        return {
            "queries": 0,
            "selected_candidates": 0,
            "section_precision_at_5": None,
            "section_recall_at_5": None,
            "anchor_recall_at_5": None,
            "chunk_proxy_recall_at_5": None,
            "fact_mention_recall": None,
        }
    def _avg(key: str) -> float | None:
        values = [float(row[key]) for row in rows if row.get(key) is not None]
        return round(sum(values) / len(values), 4) if values else None

    return {
        "queries": len(rows),
        "selected_candidates": sum(int(row["selected_count"]) for row in rows),
        "section_precision_at_5": _avg("section_precision"),
        "section_recall_at_5": _avg("section_recall"),
        "anchor_recall_at_5": _avg("anchor_recall"),
        "chunk_proxy_recall_at_5": _avg("chunk_proxy_recall"),
        "fact_mention_recall": _avg("fact_mention_recall"),
    }


def build_report(
    fixture: dict[str, Any],
    corpus_check: dict[str, Any],
    legacy_runs: list[dict[str, Any]],
    shadow_runs: list[dict[str, Any]],
    args: argparse.Namespace,
    fixture_sha256: str,
) -> dict[str, Any]:
    entries = fixture["entries"]
    legacy_scores = [
        score_entry(entry, run)
        for entry, run in zip(entries, legacy_runs)
    ]
    shadow_scores = [
        score_entry(entry, run)
        for entry, run in zip(entries, shadow_runs)
    ]

    def tiers(scores: list[dict[str, Any]], tier: str) -> list[dict[str, Any]]:
        return [score for score in scores if score["tier"] == tier]

    report = {
        "schema_version": "wr4-gold-retrieval-baseline-v1",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "corpus_check": corpus_check,
        "profile": {
            "fixture": GOLD_FIXTURE.name,
            "fixture_sha256": fixture_sha256,
            "k": int(args.k),
            "max_queries": int(args.max_queries),
            "candidate_k": int(args.candidate_k),
            "min_score": float(args.min_score),
            "embedding_provider": "ollama:bge-m3 (local)",
            "writer_uses": "legacy",
            "llm_calls": 0,
            "chroma_writes": 0,
            "production_switched": False,
        },
        "aggregates": {
            "all": {
                "legacy": aggregate(legacy_scores),
                "shadow": aggregate(shadow_scores),
            },
            "legacy_author_labeled": {
                "legacy": aggregate(tiers(legacy_scores, "legacy_author_labeled")),
                "shadow": aggregate(tiers(shadow_scores, "legacy_author_labeled")),
            },
            "wr_key_evidence": {
                "legacy": aggregate(tiers(legacy_scores, "wr_key_evidence")),
                "shadow": aggregate(tiers(shadow_scores, "wr_key_evidence")),
            },
            "late_chapter": {
                "legacy": aggregate(legacy_scores, late_only=True),
                "shadow": aggregate(shadow_scores, late_only=True),
            },
        },
        "per_query": [
            {
                "query_index": score["query_index"],
                "tier": score["tier"],
                "current_section": score["current_section"],
                "legacy": {key: score[key] for key in (
                    "section_precision", "section_recall", "gold_section_hits",
                    "gold_sections", "anchor_recall", "gold_anchor_hits",
                    "gold_anchors", "chunk_proxy_recall", "gold_chunk_hits",
                    "gold_chunks", "fact_mention_recall", "fact_hits", "fact_total",
                    "selected_sections_sorted",
                )},
                "shadow": {key: shadow_score[key] for key in (
                    "section_precision", "section_recall", "gold_section_hits",
                    "gold_sections", "anchor_recall", "gold_anchor_hits",
                    "gold_anchors", "chunk_proxy_recall", "gold_chunk_hits",
                    "gold_chunks", "fact_mention_recall", "fact_hits", "fact_total",
                    "selected_sections_sorted",
                )},
            }
            for score, shadow_score in zip(legacy_scores, shadow_scores)
        ],
        "wr_key_summary": [
            {
                "query_index": entry["query_index"],
                "wr_keys": entry.get("wr_keys", []),
                "legacy_fact_mention_recall": score["fact_mention_recall"],
                "shadow_fact_mention_recall": shadow_score["fact_mention_recall"],
                "legacy_section_recall": score["section_recall"],
                "shadow_section_recall": shadow_score["section_recall"],
            }
            for entry, score, shadow_score in zip(entries, legacy_scores, shadow_scores)
            if entry.get("tier") == "wr_key_evidence"
        ],
        "limitations": [
            "Section-level gold is a human-judged proxy; chunk-level gold is derived from section membership (Tier A) or exact evidence spans (Tier B).",
            "Fact mention recall is a deterministic mention-level diagnostic, not semantic support verification.",
            "Two Tier A entries (T5/T6) had gold_sections corrected with evidence sections; provenance is recorded per entry.",
            "Baseline does not tune, wire, or switch any production path.",
        ],
        "decision": "baseline_quantified_no_tuning_no_wiring",
    }
    return report


def render_markdown(report: dict[str, Any]) -> str:
    def table(label: str) -> str:
        agg = report["aggregates"][label]
        rows = [
            ("legacy", agg["legacy"]),
            ("shadow", agg["shadow"]),
        ]
        lines = [
            "| path | section precision@5 | section recall@5 | anchor recall@5 | chunk proxy recall@5 | fact mention recall | queries |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
        for name, values in rows:
            lines.append(
                f"| {name} | {values['section_precision_at_5']} | "
                f"{values['section_recall_at_5']} | {values['anchor_recall_at_5']} | "
                f"{values['chunk_proxy_recall_at_5']} | {values['fact_mention_recall']} | "
                f"{values['queries']} |"
            )
        return "\n".join(lines)

    per_query_lines = [
        "| query | tier | cur | L p@5 | L r@5 | L fact | S p@5 | S r@5 | S fact |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in report["per_query"]:
        legacy = row["legacy"]
        shadow = row["shadow"]
        per_query_lines.append(
            f"| {row['query_index']} | {row['tier']} | {row['current_section']} | "
            f"{legacy['section_precision']} | {legacy['section_recall']} | "
            f"{legacy['fact_mention_recall']} | {shadow['section_precision']} | "
            f"{shadow['section_recall']} | {shadow['fact_mention_recall']} |"
        )

    wr_lines = [
        "| query | wr keys | legacy fact recall | shadow fact recall |",
        "|---|---|---:|---:|",
    ]
    for row in report["wr_key_summary"]:
        keys = "; ".join("/".join(key for key in key_row if key) for key_row in row["wr_keys"])
        wr_lines.append(
            f"| {row['query_index']} | {keys} | "
            f"{row['legacy_fact_mention_recall']} | {row['shadow_fact_mention_recall']} |"
        )

    return f"""# World Runtime WR4：离线 gold-retrieval 评测集与基线 结果报告

日期：2026-08-07

## 1. 评测集

- 语料：task `{report['corpus_check']['task_id']}`，{report['corpus_check']['chunk_count']} chunks，
  corpus hash 与快照绑定校验 {report['corpus_check']['match']}（fail-closed）。
- 金标：18 条 = Tier A 10 条（legacy 作者标注，证据章节修正 2 条）+ Tier B 8 条（WR-only 键，
  全部绑定精确文本证据）。
- 零 LLM、零 Chroma 写入、生产 off、Writer 继续走 legacy。

## 2. 总基线

### 全部 18 条

{table('all')}

### Tier A（10 条 legacy 标注）

{table('legacy_author_labeled')}

### Tier B（8 条 WR-only 键）

{table('wr_key_evidence')}

### 后段章节（current section >= 13）

{table('late_chapter')}

## 3. 逐条

{chr(10).join(per_query_lines)}

## 4. WR-only 键命中

{chr(10).join(wr_lines)}

## 5. 说明

- section precision/recall 为人类金标章节代理（与历史基线一致）；
- anchor/chunk recall 为 chunk 级诊断；fact mention recall 为确定性短语命中，非语义支持；
- 本次仅量化基线，不调参、不接线、不切换生产路径。

机器可读数据：[wr4-gold-retrieval-baseline-2026-08-07.json](E:/writer/my_writing_system/reports/wr4-gold-retrieval-baseline-2026-08-07.json)
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", type=Path, default=GOLD_FIXTURE)
    parser.add_argument("--output-json", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--output-md", type=Path, default=DEFAULT_MD)
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--max-queries", type=int, default=4)
    parser.add_argument("--candidate-k", type=int, default=12)
    parser.add_argument("--min-score", type=float, default=0.35)
    args = parser.parse_args()

    fixture = _load_json(args.fixture)
    store = VectorStore()
    corpus_check = verify_corpus(store, fixture)
    if not corpus_check["match"]:
        raise SystemExit(
            "corpus mismatch: expected "
            f"{corpus_check['expected_hash']} got {corpus_check['actual_hash']}"
        )
    task_id = fixture["corpus"]["task_id"]
    legacy_runs = [
        legacy_run(store, entry, task_id, args.k) for entry in fixture["entries"]
    ]
    shadow_runs = [
        shadow_run(
            store,
            entry,
            task_id,
            max_queries=args.max_queries,
            candidate_k=args.candidate_k,
            min_score=args.min_score,
        )
        for entry in fixture["entries"]
    ]
    fixture_sha256 = hashlib.sha256(args.fixture.read_bytes()).hexdigest()
    report = build_report(
        fixture, corpus_check, legacy_runs, shadow_runs, args, fixture_sha256
    )
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    args.output_md.parent.mkdir(parents=True, exist_ok=True)
    args.output_md.write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "corpus_check": corpus_check["match"],
                "output_json": str(args.output_json),
                "output_md": str(args.output_md),
                "aggregates_all_legacy": report["aggregates"]["all"]["legacy"],
                "aggregates_all_shadow": report["aggregates"]["all"]["shadow"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
