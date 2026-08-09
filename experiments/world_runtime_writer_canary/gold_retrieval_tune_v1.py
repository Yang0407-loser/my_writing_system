"""WR4 tuning ablation: planner x reranker variants on the frozen gold set.

The runner caches coarse vector results per unique planner query text, then
applies every reranker configuration in memory.  No production code is
modified; Writer keeps consuming the legacy path.  The final tuned variant
can be written as a separate report with ``--final planner:reranker``.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.retrieval_pipeline import merge_candidates
from app.vector_store import VectorStore

from gold_retrieval_baseline_v1 import (
    score_entry,
    text_hash,
    verify_corpus,
)
from wr4_tuning_components import PLANNER_REGISTRY, RERANKER_REGISTRY, rerank_candidates


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
GOLD_FIXTURE = FIXTURES / "wr4_gold_retrieval_v1.json"
ABLATION_JSON = ROOT / "reports" / "wr4-tuning-ablation-2026-08-07.json"
ABLATION_MD = ROOT / "reports" / "world-runtime-wr4-tuning-ablation-2026-08-07.md"
TUNED_JSON = ROOT / "reports" / "wr4-gold-retrieval-tuned-2026-08-07.json"
TUNED_MD = ROOT / "reports" / "world-runtime-wr4-gold-retrieval-tuned-2026-08-07.md"

COARSE_K = 24

RESIDUAL_GAPS = [
    {
        "query_index": "W6",
        "finding": (
            "coarse recall failure: top-24 for every planner variant contains "
            "no section 1/2 chunk. The gold chunks (S1.1) phrase the facts as "
            "'隔断墙/面团/面包店', while the query uses '揉面/住在哪里'; "
            "text-only embedding cannot bridge the vocabulary gap. The "
            "intended fix is WR3.5 metadata wiring (characters/locations), "
            "which is out of scope for this batch (production stays off)."
        ),
    }
]


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _aggregate(scores: list[dict[str, Any]]) -> dict[str, Any]:
    rows = scores
    if not rows:
        return {
            "queries": 0,
            "selected_candidates": 0,
            "zero_result_queries": 0,
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
        "zero_result_queries": sum(1 for row in rows if row["selected_count"] == 0),
        "section_precision_at_5": _avg("section_precision"),
        "section_recall_at_5": _avg("section_recall"),
        "anchor_recall_at_5": _avg("anchor_recall"),
        "chunk_proxy_recall_at_5": _avg("chunk_proxy_recall"),
        "fact_mention_recall": _avg("fact_mention_recall"),
    }


def _selected_run(entry: dict[str, Any], selected: list[dict[str, Any]]) -> dict[str, Any]:
    sections = [int(item.get("section") or 0) for item in selected]
    hashes = [text_hash(str(item.get("text", ""))) for item in selected]
    texts = [str(item.get("text", "")) for item in selected]
    return {
        "query_index": entry["query_index"],
        "selected_ids": [str(item.get("id", "")) for item in selected],
        "selected_sections": sections,
        "selected_hashes": hashes,
        "selected_texts": texts,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", type=Path, default=GOLD_FIXTURE)
    parser.add_argument("--planners", default=",".join(PLANNER_REGISTRY))
    parser.add_argument("--rerankers", default=",".join(RERANKER_REGISTRY))
    parser.add_argument("--coarse-k", type=int, default=COARSE_K)
    parser.add_argument("--final", default="")
    parser.add_argument("--ablation-json", type=Path, default=ABLATION_JSON)
    parser.add_argument("--ablation-md", type=Path, default=ABLATION_MD)
    parser.add_argument("--tuned-json", type=Path, default=TUNED_JSON)
    parser.add_argument("--tuned-md", type=Path, default=TUNED_MD)
    args = parser.parse_args()

    fixture = _load_json(args.fixture)
    planner_names = [name.strip() for name in args.planners.split(",") if name.strip()]
    reranker_names = [name.strip() for name in args.rerankers.split(",") if name.strip()]
    unknown_planners = [name for name in planner_names if name not in PLANNER_REGISTRY]
    unknown_rerankers = [name for name in reranker_names if name not in RERANKER_REGISTRY]
    if unknown_planners or unknown_rerankers:
        raise SystemExit(
            f"unknown variants: planners={unknown_planners} rerankers={unknown_rerankers}"
        )

    store = VectorStore()
    corpus_check = verify_corpus(store, fixture)
    if not corpus_check["match"]:
        raise SystemExit("corpus mismatch, aborting ablation")
    task_id = fixture["corpus"]["task_id"]
    entries = fixture["entries"]
    character_names = fixture["character_names"]

    planners = {name: PLANNER_REGISTRY[name]() for name in planner_names}
    plans: dict[str, dict[str, Any]] = {}
    unique_texts: dict[str, str] = {}
    for entry in entries:
        plans[entry["query_index"]] = {}
        for name, planner in planners.items():
            plan = planner.plan_text(
                entry["query"],
                requested_intents=entry["query_intent"],
                character_names=character_names,
                current_section=int(entry["section"]),
                current_subsection=int(entry["subsection"]),
            )
            plans[entry["query_index"]][name] = plan
            for planned_query in plan.queries:
                unique_texts[planned_query.query] = planned_query.query

    cache: dict[str, list[dict[str, Any]]] = {}
    for query_text in unique_texts:
        cache[query_text] = store.search_with_meta(
            query_text, k=args.coarse_k, task_id=task_id, candidate_k=args.coarse_k
        )

    merged_by_entry: dict[str, dict[str, list[dict[str, Any]]]] = {}
    coarse_by_entry: dict[str, dict[str, dict[str, Any]]] = {}
    for entry in entries:
        merged_by_entry[entry["query_index"]] = {}
        coarse_by_entry[entry["query_index"]] = {}
        gold_sections = set(int(section) for section in entry["gold_sections"])
        for name in planner_names:
            plan = plans[entry["query_index"]][name]
            query_results = [
                (planned_query, cache[planned_query.query])
                for planned_query in plan.queries
            ]
            merged = merge_candidates(query_results)
            merged_by_entry[entry["query_index"]][name] = merged
            sections = {int(item.get("section") or 0) for item in merged}
            coarse_by_entry[entry["query_index"]][name] = {
                "coarse_candidates": len(merged),
                "coarse_sections": len(sections),
                "coarse_gold_hits": len(sections & gold_sections),
                "gold_sections": len(gold_sections),
                "coarse_section_recall": (
                    round(len(sections & gold_sections) / len(gold_sections), 4)
                    if gold_sections
                    else None
                ),
                "coarse_sections_sorted": sorted(sections),
            }

    combos: list[dict[str, Any]] = []
    per_combo: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for planner_name in planner_names:
        for reranker_name in reranker_names:
            config = dict(RERANKER_REGISTRY[reranker_name])
            scores: list[dict[str, Any]] = []
            for entry in entries:
                plan = plans[entry["query_index"]][planner_name]
                merged = merged_by_entry[entry["query_index"]][planner_name]
                ranked = rerank_candidates(plan, merged, **config)
                run = _selected_run(entry, ranked["selected"])
                score = score_entry(entry, run)
                scores.append(score)
            per_combo[(planner_name, reranker_name)] = scores
            tier_a = [score for score in scores if score["tier"] == "legacy_author_labeled"]
            tier_b = [score for score in scores if score["tier"] == "wr_key_evidence"]
            combos.append(
                {
                    "planner": planner_name,
                    "reranker": reranker_name,
                    "aggregate_all": _aggregate(scores),
                    "aggregate_tier_a": _aggregate(tier_a),
                    "aggregate_tier_b": _aggregate(tier_b),
                    "aggregate_late": _aggregate(
                        [score for score in scores if int(score["current_section"]) >= 13]
                    ),
                }
            )

    combos.sort(
        key=lambda combo: (
            -float(combo["aggregate_tier_b"]["section_recall_at_5"] or 0.0),
            -float(combo["aggregate_tier_a"]["section_recall_at_5"] or 0.0),
            -float(combo["aggregate_tier_a"]["section_precision_at_5"] or 0.0),
            -float(combo["aggregate_all"]["fact_mention_recall"] or 0.0),
            combo["aggregate_all"]["zero_result_queries"],
            list(PLANNER_REGISTRY).index(combo["planner"]),
            list(RERANKER_REGISTRY).index(combo["reranker"]),
        )
    )

    ablation = {
        "schema_version": "wr4-tuning-ablation-v1",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "corpus_check": corpus_check,
        "profile": {
            "coarse_k": args.coarse_k,
            "planners": planner_names,
            "rerankers": reranker_names,
            "embedding_provider": "ollama:bge-m3 (local)",
            "llm_calls": 0,
            "chroma_writes": 0,
            "production_switched": False,
            "gold_leakage": False,
        },
        "ranking_rule": [
            "tier B section recall@5 desc",
            "tier A section recall@5 desc",
            "tier A section precision@5 desc",
            "all fact mention recall desc",
            "zero-result queries asc",
            "registry order tie-break",
        ],
        "combos": combos,
        "coarse_by_entry": coarse_by_entry,
    }
    args.ablation_json.parent.mkdir(parents=True, exist_ok=True)
    args.ablation_json.write_text(
        json.dumps(ablation, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    args.ablation_md.write_text(
        render_ablation_markdown(ablation), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "ablation_json": str(args.ablation_json),
                "top5": combos[:5],
            },
            ensure_ascii=False,
            indent=2,
        )
    )

    if args.final:
        planner_name, _, reranker_name = args.final.partition(":")
        if planner_name not in PLANNER_REGISTRY or reranker_name not in RERANKER_REGISTRY:
            raise SystemExit(f"invalid --final {args.final!r}")
        scores = per_combo[(planner_name, reranker_name)]
        final_report = {
            "schema_version": "wr4-gold-retrieval-tuned-baseline-v1",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "corpus_check": corpus_check,
            "variant": {"planner": planner_name, "reranker": reranker_name},
            "profile": {
                "coarse_k": args.coarse_k,
                "reranker_config": RERANKER_REGISTRY[reranker_name],
                "embedding_provider": "ollama:bge-m3 (local)",
                "writer_uses": "legacy",
                "llm_calls": 0,
                "chroma_writes": 0,
                "production_switched": False,
            },
            "aggregates": {
                "all": _aggregate(scores),
                "legacy_author_labeled": _aggregate(
                    [score for score in scores if score["tier"] == "legacy_author_labeled"]
                ),
                "wr_key_evidence": _aggregate(
                    [score for score in scores if score["tier"] == "wr_key_evidence"]
                ),
                "late_chapter": _aggregate(
                    [score for score in scores if int(score["current_section"]) >= 13]
                ),
            },
            "per_query": [
                {
                    "query_index": score["query_index"],
                    "tier": score["tier"],
                    "current_section": score["current_section"],
                    "section_precision": score["section_precision"],
                    "section_recall": score["section_recall"],
                    "gold_section_hits": score["gold_section_hits"],
                    "gold_sections": score["gold_sections"],
                    "anchor_recall": score["anchor_recall"],
                    "fact_mention_recall": score["fact_mention_recall"],
                    "fact_hits": score["fact_hits"],
                    "fact_total": score["fact_total"],
                    "selected_sections_sorted": score["selected_sections_sorted"],
                }
                for score in scores
            ],
            "residual_gaps": RESIDUAL_GAPS,
            "decision": "tuned_shadow_baseline_no_production_switch",
        }
        args.tuned_json.parent.mkdir(parents=True, exist_ok=True)
        args.tuned_json.write_text(
            json.dumps(final_report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        args.tuned_md.write_text(
            render_tuned_markdown(final_report), encoding="utf-8"
        )
        print(
            json.dumps(
                {
                    "tuned_json": str(args.tuned_json),
                    "tuned_md": str(args.tuned_md),
                },
                ensure_ascii=False,
                indent=2,
            )
        )


def render_ablation_markdown(ablation: dict[str, Any]) -> str:
    lines = [
        "# World Runtime WR4：QueryPlanner/Reranker 调参消融 结果报告",
        "",
        "日期：2026-08-07",
        "",
        f"- 语料 hash 校验：{ablation['corpus_check']['match']}（fail-closed）",
        f"- coarse_k={ablation['profile']['coarse_k']}；零 LLM、零 Chroma 写入、生产 off；",
        "- 查询构造仅使用写作要求文本（无金标泄漏）。",
        "",
        "## 组合排名（按规则：Tier B recall → Tier A recall → Tier A precision → fact recall → 空选数）",
        "",
        "| # | planner | reranker | B r@5 | A r@5 | A p@5 | all fact | zero |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for index, combo in enumerate(ablation["combos"], start=1):
        a = combo["aggregate_all"]
        ta = combo["aggregate_tier_a"]
        tb = combo["aggregate_tier_b"]
        lines.append(
            f"| {index} | {combo['planner']} | {combo['reranker']} | "
            f"{tb['section_recall_at_5']} | {ta['section_recall_at_5']} | "
            f"{ta['section_precision_at_5']} | {a['fact_mention_recall']} | "
            f"{a['zero_result_queries']} |"
        )
    lines += [
        "",
        "## 粗召回诊断（每 entry × planner，coarse_k=24，含未来章节）",
        "",
        "| query | planner | coarse candidates | coarse sections | gold hits | coarse r@24 |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for query_index in sorted(ablation["coarse_by_entry"]):
        for planner_name, info in ablation["coarse_by_entry"][query_index].items():
            lines.append(
                f"| {query_index} | {planner_name} | {info['coarse_candidates']} | "
                f"{info['coarse_sections']} | {info['coarse_gold_hits']} | "
                f"{info['coarse_section_recall']} |"
            )
    lines.append("")
    lines.append(
        "机器可读数据：[wr4-tuning-ablation-2026-08-07.json]"
        "(E:/writer/my_writing_system/reports/wr4-tuning-ablation-2026-08-07.json)"
    )
    return "\n".join(lines)


def render_tuned_markdown(report: dict[str, Any]) -> str:
    variant = report["variant"]
    residual_lines = "\n".join(
        f"- **{gap['query_index']}**：{gap['finding']}"
        for gap in report.get("residual_gaps", [])
    )

    def table(label: str) -> str:
        values = report["aggregates"][label]
        return (
            f"| p@5 | r@5 | anchor r@5 | fact recall | zero-result | queries |\n"
            f"|---:|---:|---:|---:|---:|---:|\n"
            f"| {values['section_precision_at_5']} | {values['section_recall_at_5']} | "
            f"{values['anchor_recall_at_5']} | {values['fact_mention_recall']} | "
            f"{values['zero_result_queries']} | {values['queries']} |"
        )

    rows = [
        "| query | tier | cur | p@5 | r@5 | gold hits | fact |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in report["per_query"]:
        rows.append(
            f"| {row['query_index']} | {row['tier']} | {row['current_section']} | "
            f"{row['section_precision']} | {row['section_recall']} | "
            f"{row['gold_section_hits']}/{row['gold_sections']} | "
            f"{row['fact_hits']}/{row['fact_total']} |"
        )

    return f"""# World Runtime WR4：调参后 shadow 基线 结果报告

日期：2026-08-07

## 变体

- planner: `{variant['planner']}`；reranker: `{variant['reranker']}`
- coarse_k=24；零 LLM、零 Chroma 写入、生产 off（Writer 仍走 legacy）

## 全部 18 条

{table('all')}

## Tier A（10 条）

{table('legacy_author_labeled')}

## Tier B（8 条 WR-only）

{table('wr_key_evidence')}

## 后段章节

{table('late_chapter')}

## 逐条

{chr(10).join(rows)}

## 残余缺口

{residual_lines}

## 说明

- 本次在冻结金标上做变体消融与调参（4 planner × 7 reranker），结果存在对该
  18 条金标拟合的风险；晋级前需要独立作者创建新的 sealed holdout 金标。
- 生产保持 off，Writer 继续走 legacy；调参组件仅存在于 experiments/。

机器可读数据：[wr4-gold-retrieval-tuned-2026-08-07.json](E:/writer/my_writing_system/reports/wr4-gold-retrieval-tuned-2026-08-07.json)
"""


if __name__ == "__main__":
    main()
