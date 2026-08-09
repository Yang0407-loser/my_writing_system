# -*- coding: utf-8 -*-
"""WR4 metadata reranker controlled ablation (dev gold, production-parity harness).

Retrieval pools are precomputed once per entry (wr4_rich planner with all
supported intents + vector-scored metadata supplement), then a grid of
reranker weight/min_score configs is evaluated.  Baseline is v1 + v1_035 with
the same production-parity harness.  Isolated dev collection only; zero LLM;
production off; no writes to any sealed runtime.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import chromadb
from chromadb.config import Settings as ChromaSettings

from app.embedding.factory import get_embedding_provider
from app.retrieval_pipeline import SUPPORTED_INTENTS, merge_candidates
from app.vector_store import _ChromaEmbedFn

from gold_retrieval_baseline_v1 import score_entry, text_hash
from gold_retrieval_tune_v1 import _aggregate
from wr4_tuning_components import CHARACTER_NAMES, PLANNER_REGISTRY, RERANKER_REGISTRY, rerank_candidates
import wr4_metadata_holdout_v1 as runner


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
CORPUS_SNAPSHOT = FIXTURES / "gold_retrieval_corpus_snapshot_v1.json"
DEV_FIXTURE = FIXTURES / "wr4_gold_retrieval_v1.json"
RUNTIME_DIR = ROOT / ".world_runtime_wr4_metadata_bench_runtime"
COLLECTION_NAME = "writing_paragraphs_wr35_bench"
DEV_TASK_ID = "07d1391e-06ff-4af3-8bd7-6a404d2f4fd6"
REPORT_JSON = ROOT / "reports" / "wr4-reranker-ablation-2026-08-07.json"
REPORT_MD = ROOT / "reports" / "world-runtime-wr4-reranker-ablation-2026-08-07.md"


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _search(collection: Any, query: str, k: int, task_id: str) -> list[dict[str, Any]]:
    return runner._search(collection, query, k, task_id)


def precompute_entry(collection, entry, *, supplement: bool, character_names, task_id, coarse_k=24):
    """Return (plan, merged) for one entry with the fixed production-parity harness."""
    plan = PLANNER_REGISTRY["wr4_rich"]().plan_text(
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
                runner.metadata_supplement(
                    collection, planned_query.query, character_names,
                    task_id=task_id, total_limit=150,
                )
            )
        query_results.append((planned_query, items))
    return plan, merge_candidates(query_results)


def precompute_baseline(collection, entry, *, character_names, task_id, coarse_k=24):
    plan = PLANNER_REGISTRY["v1"]().plan_text(
        entry["query"],
        requested_intents=tuple(SUPPORTED_INTENTS),
        character_names=character_names,
        current_section=int(entry["section"]),
        current_subsection=int(entry["subsection"]),
    )
    query_results = [
        (pq, _search(collection, pq.query, coarse_k, task_id))
        for pq in plan.queries
    ]
    return plan, merge_candidates(query_results)


def evaluate(entry, plan, merged, config):
    ranked = rerank_candidates(plan, merged, **config)
    selected = ranked["selected"]
    run = {
        "query_index": entry["query_index"],
        "selected_ids": [str(item.get("id", "")) for item in selected],
        "selected_sections": [int(item.get("section") or 0) for item in selected],
        "selected_hashes": [text_hash(str(item.get("text", ""))) for item in selected],
        "selected_texts": [str(item.get("text", "")) for item in selected],
    }
    return score_entry(entry, run)


def aggregate_scores(scores):
    return _aggregate(scores)


def tier(scores, tier_name):
    return _aggregate([s for s in scores if s["tier"] == tier_name])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--coarse-k", type=int, default=24)
    args = parser.parse_args()

    snapshot = _load_json(CORPUS_SNAPSHOT)
    fixture = _load_json(DEV_FIXTURE)
    task = snapshot["tasks"][DEV_TASK_ID]
    if fixture["corpus"]["corpus_hash"] != task["corpus_hash"]:
        raise SystemExit("dev fixture corpus hash mismatch")

    client = chromadb.PersistentClient(
        path=str(RUNTIME_DIR), settings=ChromaSettings(anonymized_telemetry=False)
    )
    collection = client.get_collection(
        COLLECTION_NAME,
        embedding_function=_ChromaEmbedFn(get_embedding_provider()),
    )
    character_names = tuple(fixture.get("character_names") or CHARACTER_NAMES)
    entries = fixture["entries"]
    started = time.perf_counter()

    # ---- precompute pools once per entry ----
    pools: dict[str, dict[str, Any]] = {}
    for entry in entries:
        q = entry["query_index"]
        plan_v1, merged_v1 = precompute_baseline(
            collection, entry, character_names=character_names, task_id=DEV_TASK_ID,
            coarse_k=args.coarse_k,
        )
        plan_rich, merged_nosupp = precompute_entry(
            collection, entry, supplement=False, character_names=character_names,
            task_id=DEV_TASK_ID, coarse_k=args.coarse_k,
        )
        plan_rich_s, merged_supp = precompute_entry(
            collection, entry, supplement=True, character_names=character_names,
            task_id=DEV_TASK_ID, coarse_k=args.coarse_k,
        )
        pools[q] = {
            "entry": entry,
            "plan_v1": plan_v1,
            "merged_v1": merged_v1,
            "plan_rich": plan_rich,
            "merged_rich_nosupp": merged_nosupp,
            "plan_rich_supp": plan_rich_s,
            "merged_rich_supp": merged_supp,
        }

    def run_all(key: str, config: dict[str, Any], plan_key: str, merged_key: str) -> list[dict[str, Any]]:
        scores = []
        for q, pool in pools.items():
            entry = pool["entry"]
            scores.append(evaluate(entry, pool[plan_key], pool[merged_key], config))
        return scores

    # ---- baseline (production parity) ----
    baseline_scores = run_all(
        "baseline", dict(RERANKER_REGISTRY["v1_035"]), "plan_v1", "merged_v1"
    )
    base_all = aggregate_scores(baseline_scores)
    base_b = tier(baseline_scores, "wr_key_evidence")

    # ---- reference rows ----
    refs: dict[str, dict[str, Any]] = {}
    refs["tuned_nosupp"] = {
        "scores": run_all(
            "tuned", dict(RERANKER_REGISTRY["v1_025"]), "plan_rich", "merged_rich_nosupp"
        )
    }
    refs["metadata_current"] = {
        "scores": run_all(
            "metadata", dict(RERANKER_REGISTRY["wr35_metadata_020"]),
            "plan_rich_supp", "merged_rich_supp",
        )
    }

    # ---- grid ----
    base_config = dict(RERANKER_REGISTRY["wr35_metadata_020"])
    grid = []
    for metadata_evidence, character, min_score, vector in itertools.product(
        (0.00, 0.05, 0.08, 0.10, 0.13, 0.16, 0.20),
        (0.08, 0.10, 0.12),
        (0.15, 0.20, 0.25),
        (0.50, 0.55),
    ):
        config = dict(base_config)
        config["weights"] = dict(base_config["weights"])
        config["weights"]["metadata_evidence"] = metadata_evidence
        config["weights"]["character"] = character
        config["weights"]["vector"] = vector
        config["min_score"] = min_score
        scores = run_all(
            f"me={metadata_evidence};ch={character};ms={min_score};vec={vector}",
            config, "plan_rich_supp", "merged_rich_supp",
        )
        all_agg = aggregate_scores(scores)
        b_agg = tier(scores, "wr_key_evidence")
        gates = {
            "tier_b_recall_no_regression": (
                b_agg["section_recall_at_5"] is not None
                and base_b["section_recall_at_5"] is not None
                and b_agg["section_recall_at_5"] >= base_b["section_recall_at_5"]
            ),
            "tier_b_fact_no_regression": (
                b_agg["fact_mention_recall"] is not None
                and base_b["fact_mention_recall"] is not None
                and b_agg["fact_mention_recall"] >= base_b["fact_mention_recall"]
            ),
            "all_precision_within_tolerance": (
                all_agg["section_precision_at_5"] is not None
                and base_all["section_precision_at_5"] is not None
                and all_agg["section_precision_at_5"]
                >= base_all["section_precision_at_5"] - 0.05
            ),
            "zero_not_worse": all_agg["zero_result_queries"] <= base_all["zero_result_queries"],
        }
        gates["all_pass"] = all(gates.values())
        tier_b_gain = (
            (b_agg["section_recall_at_5"] or 0.0) > (base_b["section_recall_at_5"] or 0.0)
            or (b_agg["fact_mention_recall"] or 0.0) > (base_b["fact_mention_recall"] or 0.0)
        )
        grid.append(
            {
                "name": f"me={metadata_evidence};ch={character};ms={min_score};vec={vector}",
                "config": config,
                "all": all_agg,
                "tier_b": b_agg,
                "gates": gates,
                "tier_b_gain": tier_b_gain,
            }
        )

    elapsed = round(time.perf_counter() - started, 3)
    passers = [row for row in grid if row["gates"]["all_pass"]]
    gainers = [row for row in grid if row["gates"]["all_pass"] and row["tier_b_gain"]]

    def row_summary(row):
        a, b = row["all"], row["tier_b"]
        return (
            f"{row['name']:<38} | all p={a['section_precision_at_5']} r={a['section_recall_at_5']} "
            f"fact={a['fact_mention_recall']} zero={a['zero_result_queries']} | "
            f"B p={b['section_precision_at_5']} r={b['section_recall_at_5']} "
            f"fact={b['fact_mention_recall']} | gates={sum(row['gates'].values())}/5 "
            f"gain={row['tier_b_gain']}"
        )

    print("== baseline (v1+v1_035, all intents, no supplement) ==")
    print(f"  all: {base_all}")
    print(f"  tierB: {base_b}")
    print("\n== references ==")
    for name, ref in refs.items():
        a = aggregate_scores(ref["scores"])
        b = tier(ref["scores"], "wr_key_evidence")
        print(f"  {name:<18} all p={a['section_precision_at_5']} r={a['section_recall_at_5']} "
              f"fact={a['fact_mention_recall']} | B p={b['section_precision_at_5']} "
              f"r={b['section_recall_at_5']} fact={b['fact_mention_recall']}")
    print(f"\ngrid configs: {len(grid)}; all-pass: {len(passers)}; all-pass+tierB gain: {len(gainers)}")
    print("\n== top all-pass by (tierB fact, tierB recall, all recall) ==")
    for row in sorted(
        gainers or passers,
        key=lambda r: (
            r["tier_b"]["fact_mention_recall"] or 0.0,
            r["tier_b"]["section_recall_at_5"] or 0.0,
            r["all"]["section_recall_at_5"] or 0.0,
        ),
        reverse=True,
    )[:12]:
        print("  " + row_summary(row))

    report = {
        "schema_version": "wr4-reranker-ablation-v1",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "corpus": {
            "task_id": DEV_TASK_ID,
            "corpus_hash": task["corpus_hash"],
            "chunk_count": task["chunk_count"],
        },
        "harness": {
            "planner": "wr4_rich (all supported intents, production parity)",
            "supplement": "vector-scored metadata supplement, limit 150",
            "coarse_k": args.coarse_k,
        },
        "baseline": {"all": base_all, "wr_key_evidence": base_b},
        "references": {
            name: {
                "all": aggregate_scores(ref["scores"]),
                "wr_key_evidence": tier(ref["scores"], "wr_key_evidence"),
            }
            for name, ref in refs.items()
        },
        "grid_count": len(grid),
        "all_pass_count": len(passers),
        "all_pass_tier_b_gain_count": len(gainers),
        "rows": [
            {
                "name": row["name"],
                "all": row["all"],
                "tier_b": row["tier_b"],
                "gates": row["gates"],
                "tier_b_gain": row["tier_b_gain"],
            }
            for row in grid
        ],
        "elapsed_seconds": elapsed,
    }
    REPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
    REPORT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    REPORT_MD.write_text(render_markdown(report), encoding="utf-8")
    print(f"\nreport: {REPORT_JSON} (sha256={hashlib.sha256(REPORT_JSON.read_bytes()).hexdigest()[:16]}…)")


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# WR4 metadata reranker 受控消融（dev 金标，生产对齐 harness）",
        "",
        "日期：2026-08-07",
        "",
        f"- 语料：task `{report['corpus']['task_id']}`，{report['corpus']['chunk_count']} chunks",
        f"- harness：wr4_rich（全部 intent，生产对齐）+ 向量打分 metadata 补充（上限 150）",
        "- 隔离 dev 集合；零 LLM；生产 off；未触碰任何 sealed runtime。",
        "",
        "## baseline（v1+v1_035，全部 intent，无补充）",
        "",
        "| segment | p@5 | r@5 | fact recall | zero |",
        "|---|---:|---:|---:|---:|",
    ]
    for label, agg in (
        ("all", report["baseline"]["all"]),
        ("wr_key_evidence", report["baseline"]["wr_key_evidence"]),
    ):
        lines.append(
            f"| {label} | {agg['section_precision_at_5']} | {agg['section_recall_at_5']} | "
            f"{agg['fact_mention_recall']} | {agg['zero_result_queries']} |"
        )
    lines += ["", "## 参考变体", "", "| variant | all p@5 | all r@5 | all fact | B p@5 | B r@5 | B fact |", "|---|---:|---:|---:|---:|---:|---:|"]
    for name, ref in report["references"].items():
        a, b = ref["all"], ref["wr_key_evidence"]
        lines.append(
            f"| {name} | {a['section_precision_at_5']} | {a['section_recall_at_5']} | "
            f"{a['fact_mention_recall']} | {b['section_precision_at_5']} | "
            f"{b['section_recall_at_5']} | {b['fact_mention_recall']} |"
        )
    lines += [
        "",
        "## 消融网格",
        "",
        f"- 配置数：{report['grid_count']}（metadata_evidence ∈ 0.00–0.20、character ∈ 0.08–0.12、"
        f"min_score ∈ 0.15–0.25、vector ∈ 0.50–0.55）",
        f"- 全门禁通过：{report['all_pass_count']}；通过且 Tier B 有增益：{report['all_pass_tier_b_gain_count']}",
        "",
        "### 通过门禁且 Tier B 有增益的配置（前 12，按 B fact / B recall / all recall 排序）",
        "",
        "| config | all p@5 | all r@5 | all fact | B p@5 | B r@5 | B fact | gates |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    rows = [r for r in report["rows"] if r["gates"]["all_pass"]]
    rows = [r for r in rows if r["tier_b_gain"]] or rows
    rows = sorted(
        rows,
        key=lambda r: (
            r["tier_b"]["fact_mention_recall"] or 0.0,
            r["tier_b"]["section_recall_at_5"] or 0.0,
            r["all"]["section_recall_at_5"] or 0.0,
        ),
        reverse=True,
    )[:12]
    for r in rows:
        a, b = r["all"], r["tier_b"]
        lines.append(
            f"| {r['name']} | {a['section_precision_at_5']} | {a['section_recall_at_5']} | "
            f"{a['fact_mention_recall']} | {b['section_precision_at_5']} | "
            f"{b['section_recall_at_5']} | {b['fact_mention_recall']} | "
            f"{sum(r['gates'].values())}/5 |"
        )
    lines += [
        "",
        "机器可读数据：[wr4-reranker-ablation-2026-08-07.json]"
        "(E:/writer/my_writing_system/reports/wr4-reranker-ablation-2026-08-07.json)",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
