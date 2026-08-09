"""Offline legacy-vs-reranked A/B extractor for one real writing task.

Why this exists
---------------
The reranker's whole promotion argument is "does P@5 go up". That question is
only answerable if a single real run records BOTH orders for the same query.
It does: when ``RAG_RERANKER_ENABLED=true``, every retrieval writes a
``rerank`` block into ``retrieval_trace`` inside ``rag_recall_log`` containing
``legacy_top_k_ids`` and ``reranked_top_k_ids`` over one shared candidate pool.

So one task produces a complete paired comparison with **zero extra retrieval
and zero extra model calls**. This script pulls it out, computes the
deterministic movement statistics, and writes a paired review file whose
disagreement set is the only thing a judge actually needs to look at.

It does NOT decide whether the reranker is better. It produces the evidence and
the review task. Judging stays with the existing LLM-judge / human pass in
``tests/eval_quality.py``.

Usage
-----
    uv run python tests/benchmarks/benchmark_rag_reranker_ab.py <task_id>
    uv run python tests/benchmarks/benchmark_rag_reranker_ab.py <task_id> \
        --output reports/rag-reranker-ab.json
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPORT = ROOT / "reports" / "rag-reranker-ab.json"
DEFAULT_REVIEW = ROOT / "tests" / "quality" / "rag_reranker_ab_review.json"


def load_recall_log(task_id: str) -> list[dict[str, Any]]:
    from app.blackboard import Blackboard

    raw = Blackboard().get(task_id, "rag_recall_log")
    if not raw:
        raise SystemExit(
            "rag_recall_log 不存在。任务需要跑完，且 Redis 中该 key 仍在有效期内。"
        )
    entries = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(entries, list):
        raise SystemExit("rag_recall_log 格式异常，期望 list。")
    return entries


def extract(entries: list[dict[str, Any]]) -> dict[str, Any]:
    pairs: list[dict[str, Any]] = []
    skipped: Counter[str] = Counter()

    for entry in entries:
        trace = entry.get("retrieval_trace") or {}
        rerank = trace.get("rerank")
        if not isinstance(rerank, dict):
            skipped["no_rerank_block"] += 1
            continue
        if not rerank.get("applied"):
            skipped[str(rerank.get("reason") or "not_applied")] += 1
            continue

        legacy_ids = list(rerank.get("legacy_top_k_ids") or [])
        new_ids = list(rerank.get("reranked_top_k_ids") or [])
        # text for the judge comes from the coarse candidate pool in the trace
        by_id = {
            str(c.get("id")): c for c in (trace.get("candidates") or []) if c.get("id")
        }
        pairs.append(
            {
                "section": entry.get("section"),
                "subsection": entry.get("subsection"),
                "query": entry.get("query", ""),
                "candidate_pool_size": rerank.get("candidate_count"),
                "legacy_top_k_ids": legacy_ids,
                "reranked_top_k_ids": new_ids,
                "promoted_ids": list(rerank.get("promoted_ids") or []),
                "demoted_ids": list(rerank.get("demoted_ids") or []),
                "order_changed": bool(rerank.get("order_changed")),
                "rerank_elapsed_ms": rerank.get("elapsed_ms"),
                "dropped_by_min_score": rerank.get("dropped_by_min_score", 0),
                "candidate_scores": {
                    str(c.get("id")): c.get("rerank_score")
                    for c in (rerank.get("candidates") or [])
                },
                "has_metadata_for": sorted(set(legacy_ids + new_ids) & set(by_id)),
            }
        )

    changed = [p for p in pairs if p["order_changed"]]
    disagreement_ids = sorted(
        {i for p in pairs for i in p["promoted_ids"] + p["demoted_ids"]}
    )
    latencies = [
        p["rerank_elapsed_ms"]
        for p in pairs
        if isinstance(p["rerank_elapsed_ms"], (int, float))
    ]

    return {
        "retrievals_total": len(entries),
        "retrievals_with_rerank": len(pairs),
        "skipped": dict(skipped),
        "order_changed_count": len(changed),
        "order_changed_ratio": round(len(changed) / len(pairs), 4) if pairs else 0.0,
        "promoted_total": sum(len(p["promoted_ids"]) for p in pairs),
        "demoted_total": sum(len(p["demoted_ids"]) for p in pairs),
        "distinct_disagreement_items": len(disagreement_ids),
        "rerank_latency_ms": {
            "sum": round(sum(latencies), 3) if latencies else 0.0,
            "max": round(max(latencies), 3) if latencies else 0.0,
            "mean": round(sum(latencies) / len(latencies), 3) if latencies else 0.0,
        },
        "pairs": pairs,
    }


def build_review(report: dict[str, Any]) -> dict[str, Any]:
    """Only the items where the two orders disagree need judging."""
    rows = []
    for pair in report["pairs"]:
        if not pair["order_changed"]:
            continue
        for item_id in pair["promoted_ids"]:
            rows.append(
                {
                    "section": pair["section"],
                    "subsection": pair["subsection"],
                    "query": pair["query"],
                    "id": item_id,
                    "arm": "reranked_only",
                    "rerank_score": pair["candidate_scores"].get(item_id),
                    "human_relevant": None,
                    "review_note": "",
                }
            )
        for item_id in pair["demoted_ids"]:
            rows.append(
                {
                    "section": pair["section"],
                    "subsection": pair["subsection"],
                    "query": pair["query"],
                    "id": item_id,
                    "arm": "legacy_only",
                    "rerank_score": pair["candidate_scores"].get(item_id),
                    "human_relevant": None,
                    "review_note": "",
                }
            )
    return {
        "instructions": (
            "对每条判断 human_relevant=true/false：该片段是否与 query 主题相关"
            "（同一事件/人物/设定/情节点）。arm 字段不要作为判断依据。"
            "填完后：reranked_only 的 true 比例 > legacy_only 的 true 比例，"
            "才说明重排带来净收益。"
        ),
        "rows": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task_id")
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--review", type=Path, default=DEFAULT_REVIEW)
    args = parser.parse_args()

    report = extract(load_recall_log(args.task_id))
    report["task_id"] = args.task_id

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    review = build_review(report)
    args.review.parent.mkdir(parents=True, exist_ok=True)
    args.review.write_text(
        json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"检索次数            : {report['retrievals_total']}")
    print(f"其中带重排结果      : {report['retrievals_with_rerank']}")
    if report["skipped"]:
        print(f"跳过原因            : {report['skipped']}")
    print(
        f"顺序被改变          : {report['order_changed_count']} "
        f"({report['order_changed_ratio'] * 100:.1f}%)"
    )
    print(
        f"提升/挤出条目       : {report['promoted_total']} / {report['demoted_total']}"
    )
    print(f"重排延迟 (总/均/峰) : {report['rerank_latency_ms']}")
    print(f"需人工判定的条目    : {len(review['rows'])}")
    print()
    print(f"[ab] report -> {args.output}")
    print(f"[ab] review -> {args.review}")
    if report["order_changed_count"] == 0:
        print("\n顺序一次都没变：重排对本任务无影响，不需要判定，也不构成晋级证据。")


if __name__ == "__main__":
    main()
