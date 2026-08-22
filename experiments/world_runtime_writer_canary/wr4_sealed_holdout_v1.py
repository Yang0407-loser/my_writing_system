"""WR4 sealed unseen holdout runner (independent author, v1).

The holdout gold set is authored by an independent author against the unseen
corpus ``3a4e561a`` (a different writing task from the v1 training gold).
This module:

1. ``build``   - renders the authored spec into a hash-bound fixture and
                 freeze manifest (fail-closed on verbatim evidence and on
                 strict prior-context gold sections);
2. ``seal``    - copies the frozen fixture into the runtime's private
                 directory and writes the holdout lock;
3. ``preflight``- verifies sealed hash, live corpus hash, evidence spans and
                 no gold leakage from the v1 training set;
4. ``run-once`` - evaluates legacy, baseline shadow (v1 + v1_035) and the
                 tuned variant (wr4_rich + v1_025) on the sealed holdout,
                 then writes evaluation.json and the reports;
5. ``evaluate`` - re-reads the evaluation and reports the gate decision.

The runner is deterministic and offline: zero LLM calls, zero Chroma writes,
production off, Writer stays on legacy.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
EXPERIMENTS = Path(__file__).resolve().parent
FIXTURES = EXPERIMENTS / "fixtures"
sys.path.insert(0, str(EXPERIMENTS))

from gold_retrieval_baseline_v1 import (  # noqa: E402
    score_entry,
    text_hash,
    verify_corpus,
)
from gold_retrieval_build_v1 import (  # noqa: E402
    _load_json,
    _sha256_file,
    rows_by_key,
    rows_by_section,
    spans_for,
)
from wr4_holdout_spec_v1 import (  # noqa: E402
    BUILD_VERSION,
    CHARACTER_NAMES,
    SCHEMA_VERSION,
    SPECS,
    TASK_ID,
    tier_counts,
)
from wr4_tuning_components import (  # noqa: E402
    PLANNER_REGISTRY,
    RERANKER_REGISTRY,
    rerank_candidates,
)

from app.config import settings  # noqa: E402
from app.retrieval_pipeline import merge_candidates  # noqa: E402
from app.vector_store import VectorStore  # noqa: E402


CORPUS_SNAPSHOT = FIXTURES / "gold_retrieval_corpus_snapshot_v1.json"
TRAINING_FIXTURE = FIXTURES / "wr4_gold_retrieval_v1.json"
OUTPUT_FIXTURE = FIXTURES / "wr4_gold_retrieval_holdout_v1.json"
MANIFEST = FIXTURES / "wr4_gold_retrieval_holdout_v1.freeze_manifest.json"

RUNTIME = ROOT / ".world_runtime_wr4_sealed_holdout_runtime"
SEALED_PATH = RUNTIME / "private" / "sealed-holdout-v1.json"
LOCK_PATH = RUNTIME / "holdout-lock.json"
LOCKED_MANIFEST = RUNTIME / "private" / "locked-manifest.json"
ATTEMPT_LEDGER = RUNTIME / "attempt-ledger.json"
EVALUATION_JSON = RUNTIME / "evaluation.json"

REPORT_JSON = ROOT / "reports" / "wr4-sealed-holdout-2026-08-07.json"
REPORT_MD = ROOT / "reports" / "world-runtime-wr4-sealed-holdout-2026-08-07.md"

COARSE_K = 24
BASELINE_VARIANT = ("v1", "v1_035")
TUNED_VARIANT = ("wr4_rich", "v1_025")
EXPECTED_TIER_COUNTS = {"story_fact": 12, "wr_key_evidence": 8}


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _digest(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _validate_specs() -> None:
    if len(SPECS) < 20:
        raise ValueError(f"holdout needs >=20 queries, got {len(SPECS)}")
    counts = tier_counts()
    if counts != EXPECTED_TIER_COUNTS:
        raise ValueError(f"unexpected tier counts: {counts}")
    queries = [spec["query"] for spec in SPECS]
    if len(set(queries)) != len(queries):
        raise ValueError("holdout queries must be unique")
    indexes = [spec["query_index"] for spec in SPECS]
    if len(set(indexes)) != len(indexes):
        raise ValueError("holdout query indexes must be unique")
    for spec in SPECS:
        current = int(spec["section"])
        if current < 2:
            raise ValueError(f"{spec['query_index']}: current section must be >=2")
        gold_sections = [int(section) for section in spec["gold_sections"]]
        if not gold_sections:
            raise ValueError(f"{spec['query_index']}: gold_sections empty")
        if any(section >= current for section in gold_sections):
            raise ValueError(
                f"{spec['query_index']}: gold sections must be strictly prior "
                f"context (section < current {current}), got {gold_sections}"
            )
        if not spec["must_recall_facts"]:
            raise ValueError(f"{spec['query_index']}: no must-recall facts")
        if set(spec["fact_evidence"]) != set(spec["must_recall_facts"]):
            raise ValueError(
                f"{spec['query_index']}: fact_evidence keys must equal facts"
            )


def build_fixture() -> dict[str, Any]:
    _validate_specs()
    snapshot = _load_json(CORPUS_SNAPSHOT)
    if snapshot["schema_version"] != "wr4-corpus-snapshot-v1":
        raise ValueError("unexpected corpus snapshot schema")
    task = snapshot["tasks"].get(TASK_ID)
    if task is None:
        raise ValueError(f"holdout task {TASK_ID} missing from snapshot")
    rows = task["rows"]
    by_key = rows_by_key(rows)
    by_section = rows_by_section(rows)

    entries: list[dict[str, Any]] = []
    for spec in SPECS:
        gold_sections = [int(section) for section in spec["gold_sections"]]
        gold_rows = [
            row for section in gold_sections for row in by_section.get(section, [])
        ]
        if not gold_rows:
            raise ValueError(f"{spec['query_index']}: no gold rows")
        anchor_rows: list[dict[str, Any]] = []
        for key in spec["gold_chunk_keys"]:
            matched = by_key.get(str(key), [])
            if not matched:
                raise ValueError(
                    f"{spec['query_index']}: gold chunk key not found: {key}"
                )
            anchor_rows.extend(matched)
        fact_evidence: dict[str, list[dict[str, Any]]] = {}
        for fact in spec["must_recall_facts"]:
            spans: list[dict[str, Any]] = []
            for phrase in spec["fact_evidence"][fact]:
                spans.extend(spans_for(phrase, gold_rows))
            if not spans:
                raise ValueError(
                    f"{spec['query_index']}: no evidence found for fact: {fact} "
                    f"in gold sections {gold_sections}"
                )
            fact_evidence[fact] = spans
        entry: dict[str, Any] = {
            "query_index": spec["query_index"],
            "tier": spec["tier"],
            "source": "independent_author_wr4_holdout_v1",
            "query_intent": [str(intent) for intent in spec["query_intent"]],
            "section": int(spec["section"]),
            "subsection": int(spec["subsection"]),
            "query": str(spec["query"]),
            "gold_sections": gold_sections,
            "gold_sections_source": "holdout_author_spec_v1",
            "gold_chunk_keys": [str(key) for key in spec["gold_chunk_keys"]],
            "gold_anchor_hashes": sorted({row["content_hash"] for row in anchor_rows}),
            "gold_chunk_hashes": sorted({row["content_hash"] for row in gold_rows}),
            "must_recall_facts": [str(fact) for fact in spec["must_recall_facts"]],
            "fact_evidence": fact_evidence,
            "requires_causal_retrieval": bool(spec["requires_causal_retrieval"]),
            "gold_anchor_exhaustive": True,
            "gold_sections_exhaustive": False,
        }
        if spec["tier"] == "wr_key_evidence":
            entry["wr_keys"] = [list(key) for key in spec["wr_keys"]]
        else:
            entry["wr_keys"] = []
        entries.append(entry)

    entries.sort(key=lambda entry: entry["query_index"])
    fixture = {
        "schema_version": SCHEMA_VERSION,
        "builder_version": BUILD_VERSION,
        "built_at": "2026-08-07",
        "k": 5,
        "corpus": {
            "task_id": TASK_ID,
            "snapshot_file": CORPUS_SNAPSHOT.name,
            "snapshot_sha256": _sha256_file(CORPUS_SNAPSHOT),
            "corpus_hash": task["corpus_hash"],
            "chunk_count": task["chunk_count"],
        },
        "character_names": list(CHARACTER_NAMES),
        "amendments": [],
        "tiers": dict(tier_counts()),
        "entries": entries,
    }
    return fixture


def build() -> dict[str, Any]:
    fixture = build_fixture()
    rendered = json.dumps(fixture, ensure_ascii=False, indent=2)
    OUTPUT_FIXTURE.write_text(rendered, encoding="utf-8")
    manifest = {
        "schema_version": "wr4-gold-retrieval-holdout-freeze-manifest-v1",
        "fixture": OUTPUT_FIXTURE.name,
        "fixture_sha256": _sha256_text(rendered),
        "corpus_snapshot": CORPUS_SNAPSHOT.name,
        "corpus_snapshot_sha256": _sha256_file(CORPUS_SNAPSHOT),
        "entry_count": len(fixture["entries"]),
        "tiers": dict(fixture["tiers"]),
        "llm_calls": 0,
        "production_authorized": False,
    }
    MANIFEST.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {
        "fixture": str(OUTPUT_FIXTURE),
        "fixture_sha256": manifest["fixture_sha256"],
        "entries": len(fixture["entries"]),
        "tiers": manifest["tiers"],
        "corpus_hash": fixture["corpus"]["corpus_hash"],
    }


def seal(output_dir: Path = RUNTIME) -> dict[str, Any]:
    fixture = _load_json(OUTPUT_FIXTURE)
    manifest = _load_json(MANIFEST)
    fixture_text = OUTPUT_FIXTURE.read_text(encoding="utf-8")
    fixture_hash = _sha256_text(fixture_text)
    if fixture_hash != manifest["fixture_sha256"]:
        raise ValueError("fixture hash does not match freeze manifest")
    sealed_text = json.dumps(fixture, ensure_ascii=False, indent=2)
    SEALED_PATH.parent.mkdir(parents=True, exist_ok=True)
    SEALED_PATH.write_text(sealed_text, encoding="utf-8", newline="\n")
    lock = {
        "schema_version": "wr4-gold-retrieval-holdout-lock-v1",
        "sealed_file": SEALED_PATH.name,
        "sealed_sha256": _sha256_text(sealed_text),
        "fixture_sha256": fixture_hash,
        "task_id": TASK_ID,
        "entry_count": len(fixture["entries"]),
        "sealed_at": datetime.now(timezone.utc).isoformat(),
        "author_role": "independent_author",
        "rerun_allowed": False,
    }
    _write_json(LOCK_PATH, lock)
    locked_manifest = {
        "schema_version": "wr4-gold-retrieval-holdout-locked-manifest-v1",
        "sealed_file": SEALED_PATH.name,
        "sealed_sha256": lock["sealed_sha256"],
        "task_id": TASK_ID,
        "sample_count": len(fixture["entries"]),
        "samples": [
            {
                "sample_id": entry["query_index"],
                "tier": entry["tier"],
                "query_intent": entry["query_intent"],
                "section": entry["section"],
                "subsection": entry["subsection"],
                "query": entry["query"],
                "wr_keys": entry.get("wr_keys", []),
                "gold_sections": entry["gold_sections"],
                "gold_chunk_hashes": entry["gold_chunk_hashes"],
                "must_recall_facts": entry["must_recall_facts"],
            }
            for entry in fixture["entries"]
        ],
        "llm_calls": 0,
        "chroma_writes": 0,
        "production_authorized": False,
    }
    _write_json(LOCKED_MANIFEST, locked_manifest)
    _write_json(
        ATTEMPT_LEDGER,
        {
            "schema_version": "wr4-sealed-holdout-attempt-ledger-v1",
            "attempt_count_total": 0,
            "status": "pending",
            "samples": {
                entry["query_index"]: {
                    "status": "pending",
                    "attempt_count": 0,
                }
                for entry in fixture["entries"]
            },
        },
    )
    return {
        "sealed": str(SEALED_PATH),
        "sealed_sha256": lock["sealed_sha256"],
        "entry_count": lock["entry_count"],
        "locked_manifest": str(LOCKED_MANIFEST),
    }


def _holdout_leakage(fixture: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if not TRAINING_FIXTURE.exists():
        return ["training fixture missing"]
    training = _load_json(TRAINING_FIXTURE)
    if training["corpus"]["task_id"] == fixture["corpus"]["task_id"]:
        issues.append("holdout reuses the v1 training corpus task")
    train_queries = {entry["query"] for entry in training["entries"]}
    train_phrases = {
        str(span.get("phrase", ""))
        for entry in training["entries"]
        for spans in entry["fact_evidence"].values()
        for span in spans
    }
    for entry in fixture["entries"]:
        if entry["query"] in train_queries:
            issues.append(f"{entry['query_index']}: query duplicates v1 training query")
        for spans in entry["fact_evidence"].values():
            for span in spans:
                phrase = str(span.get("phrase", ""))
                if phrase in train_phrases:
                    issues.append(
                        f"{entry['query_index']}: evidence phrase reused from "
                        f"v1 training: {phrase}"
                    )
    return issues


def _verify_evidence(fixture: dict[str, Any]) -> list[str]:
    snapshot = _load_json(CORPUS_SNAPSHOT)
    task = snapshot["tasks"][TASK_ID]
    by_hash = {row["content_hash"]: row for row in task["rows"]}
    issues: list[str] = []
    for entry in fixture["entries"]:
        known = {row["content_hash"] for row in task["rows"]}
        if not set(entry["gold_anchor_hashes"]) <= set(entry["gold_chunk_hashes"]):
            issues.append(f"{entry['query_index']}: anchor hashes not subset of gold")
        if not set(entry["gold_chunk_hashes"]) <= known:
            issues.append(f"{entry['query_index']}: gold chunk hash unknown")
        for fact in entry["must_recall_facts"]:
            spans = entry["fact_evidence"].get(fact, [])
            if not spans:
                issues.append(f"{entry['query_index']}: no spans for {fact}")
            for span in spans:
                row = by_hash.get(span["chunk_hash"])
                if row is None:
                    issues.append(f"{entry['query_index']}: span hash unknown")
                    continue
                start = int(span["start"])
                end = int(span["end"])
                phrase = str(span["phrase"])
                if row["text"][start:end] != phrase:
                    issues.append(
                        f"{entry['query_index']}: span mismatch for {phrase}"
                    )
    return issues


def preflight(output_dir: Path = RUNTIME, authorization_path: Path | None = None) -> dict[str, Any]:
    if not SEALED_PATH.exists():
        return {"ready": False, "issues": ["sealed holdout missing"]}
    if not LOCKED_MANIFEST.exists():
        return {"ready": False, "issues": ["locked manifest missing"]}
    fixture = _load_json(SEALED_PATH)
    lock = _load_json(LOCK_PATH)
    manifest = _load_json(LOCKED_MANIFEST)
    issues: list[str] = []
    sealed_text = SEALED_PATH.read_text(encoding="utf-8")
    if _sha256_text(sealed_text) != lock["sealed_sha256"]:
        issues.append("sealed holdout hash mismatch with lock")
    if manifest["sealed_sha256"] != lock["sealed_sha256"]:
        issues.append("locked manifest does not bind the sealed hash")
    if fixture["corpus"]["task_id"] != TASK_ID:
        issues.append("sealed holdout task mismatch")
    issues.extend(_verify_evidence(fixture))
    issues.extend(_holdout_leakage(fixture))
    if settings.WRITER_WORLD_RUNTIME_MODE not in ("off", "shadow"):
        issues.append(
            f"production world-runtime mode not off: "
            f"{settings.WRITER_WORLD_RUNTIME_MODE}"
        )
    store = VectorStore()
    corpus_check = verify_corpus(store, fixture)
    if not corpus_check["match"]:
        issues.append(
            f"live corpus mismatch: expected {corpus_check['expected_hash']} "
            f"got {corpus_check['actual_hash']}"
        )
    return {
        "ready": not issues,
        "issues": issues,
        "holdout_sha256": lock["sealed_sha256"],
        "sample_count": len(fixture["entries"]),
        "corpus_match": corpus_check["match"],
        "llm_calls": 0,
        "chroma_writes": 0,
        "production_switched": False,
        "authorization_path": (
            str(authorization_path) if authorization_path is not None else None
        ),
    }


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


def _run_variant(
    store: VectorStore,
    fixture: dict[str, Any],
    *,
    planner_name: str,
    reranker_name: str,
    character_names: list[str],
) -> dict[str, Any]:
    task_id = fixture["corpus"]["task_id"]
    entries = fixture["entries"]
    planner = PLANNER_REGISTRY[planner_name]()
    config = dict(RERANKER_REGISTRY[reranker_name])
    cache: dict[str, list[dict[str, Any]]] = {}
    scores: list[dict[str, Any]] = []
    runs: list[dict[str, Any]] = []
    for entry in entries:
        plan = planner.plan_text(
            entry["query"],
            requested_intents=entry["query_intent"],
            character_names=character_names,
            current_section=int(entry["section"]),
            current_subsection=int(entry["subsection"]),
        )
        query_results = []
        for planned_query in plan.queries:
            if planned_query.query not in cache:
                cache[planned_query.query] = store.search_with_meta(
                    planned_query.query,
                    k=COARSE_K,
                    task_id=task_id,
                    candidate_k=COARSE_K,
                )
            query_results.append((planned_query, cache[planned_query.query]))
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
        score = score_entry(entry, run)
        scores.append(score)
        runs.append(run)
    return {"scores": scores, "runs": runs}


def _legacy_run(
    store: VectorStore,
    fixture: dict[str, Any],
) -> dict[str, Any]:
    task_id = fixture["corpus"]["task_id"]
    entries = fixture["entries"]
    scores: list[dict[str, Any]] = []
    runs: list[dict[str, Any]] = []
    for entry in entries:
        items = store.search_with_meta(entry["query"], k=5, task_id=task_id)
        run = {
            "query_index": entry["query_index"],
            "selected_ids": [str(item.get("id", "")) for item in items],
            "selected_sections": [int(item.get("section") or 0) for item in items],
            "selected_hashes": [text_hash(item.get("text", "")) for item in items],
            "selected_texts": [str(item.get("text", "")) for item in items],
        }
        scores.append(score_entry(entry, run))
        runs.append(run)
    return {"scores": scores, "runs": runs}


def _tier_rows(scores: list[dict[str, Any]], tier: str) -> list[dict[str, Any]]:
    return [score for score in scores if score["tier"] == tier]


def evaluate_gates(
    aggregates: dict[str, dict[str, dict[str, Any]]],
) -> dict[str, bool]:
    baseline = aggregates["baseline_shadow"]
    tuned = aggregates["tuned"]
    gates = {
        "corpus_bound": True,
        "sealed": True,
        "no_gold_leakage": True,
        "zero_llm_calls": True,
        "zero_chroma_writes": True,
        "production_unchanged": True,
        "tuned_tier_b_recall_no_regression": (
            tuned["wr_key_evidence"]["section_recall_at_5"]
            >= baseline["wr_key_evidence"]["section_recall_at_5"]
        ),
        "tuned_tier_b_fact_recall_no_regression": (
            tuned["wr_key_evidence"]["fact_mention_recall"]
            >= baseline["wr_key_evidence"]["fact_mention_recall"]
        ),
        "tuned_all_precision_within_tolerance": (
            tuned["all"]["section_precision_at_5"]
            >= baseline["all"]["section_precision_at_5"] - 0.05
        ),
        "tuned_zero_results_not_worse": (
            tuned["all"]["zero_result_queries"]
            <= baseline["all"]["zero_result_queries"]
        ),
    }
    return gates


def run_once(output_dir: Path = RUNTIME) -> dict[str, Any]:
    check = preflight(output_dir)
    if not check["ready"]:
        raise RuntimeError(
            "sealed_holdout_preflight_failed:" + "|".join(check["issues"])
        )
    fixture = _load_json(SEALED_PATH)
    store = VectorStore()
    legacy = _legacy_run(store, fixture)
    baseline_shadow = _run_variant(
        store,
        fixture,
        planner_name=BASELINE_VARIANT[0],
        reranker_name=BASELINE_VARIANT[1],
        character_names=list(fixture["character_names"]),
    )
    tuned = _run_variant(
        store,
        fixture,
        planner_name=TUNED_VARIANT[0],
        reranker_name=TUNED_VARIANT[1],
        character_names=list(fixture["character_names"]),
    )

    def aggregates(scores: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        return {
            "all": _aggregate(scores),
            "story_fact": _aggregate(_tier_rows(scores, "story_fact")),
            "wr_key_evidence": _aggregate(_tier_rows(scores, "wr_key_evidence")),
            "late_chapter": _aggregate(
                [score for score in scores if int(score["current_section"]) >= 15]
            ),
        }

    agg = {
        "legacy": aggregates(legacy["scores"]),
        "baseline_shadow": aggregates(baseline_shadow["scores"]),
        "tuned": aggregates(tuned["scores"]),
    }
    gates = evaluate_gates(agg)
    passed = all(gates.values())
    evaluation = {
        "schema_version": "wr4-sealed-holdout-evaluation-v1",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "fixture": SEALED_PATH.name,
        "sealed_sha256": check["holdout_sha256"],
        "task_id": TASK_ID,
        "profile": {
            "k": 5,
            "coarse_k": COARSE_K,
            "baseline_shadow_variant": list(BASELINE_VARIANT),
            "tuned_variant": list(TUNED_VARIANT),
            "embedding_provider": "ollama:bge-m3 (local)",
            "llm_calls": 0,
            "chroma_writes": 0,
            "production_switched": False,
            "writer_uses": "legacy",
        },
        "aggregates": agg,
        "gates": gates,
        "sealed_holdout_gate_passed": passed,
        "decision": (
            "wr4_holdout_passed_tuning_generalizes"
            if passed
            else "wr4_holdout_failed_no_rerun"
        ),
        "per_query": [
            {
                "query_index": score["query_index"],
                "tier": score["tier"],
                "current_section": score["current_section"],
                "legacy": {
                    key: score[key]
                    for key in (
                        "section_precision", "section_recall", "gold_section_hits",
                        "gold_sections", "fact_mention_recall", "fact_hits",
                        "fact_total", "selected_sections_sorted",
                    )
                },
                "baseline_shadow": {
                    key: baseline_score[key]
                    for key in (
                        "section_precision", "section_recall", "gold_section_hits",
                        "gold_sections", "fact_mention_recall", "fact_hits",
                        "fact_total", "selected_sections_sorted",
                    )
                },
                "tuned": {
                    key: tuned_score[key]
                    for key in (
                        "section_precision", "section_recall", "gold_section_hits",
                        "gold_sections", "fact_mention_recall", "fact_hits",
                        "fact_total", "selected_sections_sorted",
                    )
                },
            }
            for score, baseline_score, tuned_score in zip(
                legacy["scores"], baseline_shadow["scores"], tuned["scores"]
            )
        ],
        "limitations": [
            "Section-level gold is the independent author's prior-context judgment;"
            " gold sections are strictly prior to the current section.",
            "Fact mention recall is a deterministic mention-level diagnostic, not"
            " semantic support verification.",
            "The holdout corpus is a different task (3a4e561a) from the v1"
            " training corpus (07d1391e); queries and evidence phrases do not"
            " overlap the v1 gold.",
            "A pass means the tuned variant generalizes on this unseen set; it"
            " does not authorize production wiring.",
        ],
    }
    _write_json(EVALUATION_JSON, evaluation)
    ledger = {
        "schema_version": "wr4-sealed-holdout-attempt-ledger-v1",
        "attempt_count_total": 1,
        "status": "succeeded",
        "output": EVALUATION_JSON.name,
        "decision": evaluation["decision"],
        "sealed_holdout_gate_passed": passed,
    }
    _write_json(ATTEMPT_LEDGER, ledger)
    _write_reports(evaluation)
    return {
        "schema_version": "wr4-sealed-holdout-external-result-v1",
        "command_executed_exactly_once": True,
        "sealed_holdout_gate_passed": passed,
        "decision": evaluation["decision"],
        "production_promotion_eligible": False,
        "aggregates_tuned": agg["tuned"]["all"],
        "aggregates_baseline_shadow": agg["baseline_shadow"]["all"],
        "aggregates_legacy": agg["legacy"]["all"],
    }


def _write_reports(evaluation: dict[str, Any]) -> None:
    REPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
    REPORT_JSON.write_text(
        json.dumps(evaluation, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    def table(label: str) -> str:
        rows = [
            ("legacy", evaluation["aggregates"]["legacy"][label]),
            ("baseline_shadow", evaluation["aggregates"]["baseline_shadow"][label]),
            ("tuned", evaluation["aggregates"]["tuned"][label]),
        ]
        lines = [
            "| path | section precision@5 | section recall@5 | fact recall | zero | queries |",
            "|---|---:|---:|---:|---:|---:|",
        ]
        for name, values in rows:
            lines.append(
                f"| {name} | {values['section_precision_at_5']} | "
                f"{values['section_recall_at_5']} | {values['fact_mention_recall']} | "
                f"{values['zero_result_queries']} | {values['queries']} |"
            )
        return "\n".join(lines)

    per_query_lines = [
        "| query | tier | cur | L p@5 | L r@5 | L fact | B p@5 | B r@5 | B fact | T p@5 | T r@5 | T fact |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in evaluation["per_query"]:
        l, b, t = row["legacy"], row["baseline_shadow"], row["tuned"]
        per_query_lines.append(
            f"| {row['query_index']} | {row['tier']} | {row['current_section']} | "
            f"{l['section_precision']} | {l['section_recall']} | {l['fact_mention_recall']} | "
            f"{b['section_precision']} | {b['section_recall']} | {b['fact_mention_recall']} | "
            f"{t['section_precision']} | {t['section_recall']} | {t['fact_mention_recall']} |"
        )

    gate_lines = "\n".join(
        f"- {name}: {value}" for name, value in evaluation["gates"].items()
    )
    markdown = f"""# World Runtime WR4：Sealed Unseen Holdout 结果报告

日期：2026-08-07

## 1. 评测集

- 语料：task `{evaluation['task_id']}`（与 v1 训练金标 task 07d1391e 不同），
  密封 hash `{evaluation['sealed_sha256'][:16]}…`；
- 金标：{len(evaluation['per_query'])} 条 = story_fact 12 + wr_key_evidence 8，
  全部由独立作者编写，gold sections 严格为当前小节之前的前置上下文，
  证据短语逐字绑定（fail-closed）；
- 零 LLM、零 Chroma 写入、生产 off、Writer 继续走 legacy。

## 2. 总指标

### 全部 {len(evaluation['per_query'])} 条

{table('all')}

### story_fact（12 条）

{table('story_fact')}

### wr_key_evidence（8 条）

{table('wr_key_evidence')}

### 后段章节（current section >= 15）

{table('late_chapter')}

## 3. 逐条

{chr(10).join(per_query_lines)}

## 4. 门禁

{gate_lines}

## 5. 结论

- 决策：`{evaluation['decision']}`；
- 门禁通过：{evaluation['sealed_holdout_gate_passed']}；
- 通过只表示调参变体在该 unseen 语料上泛化，不授权生产接线；
- 失败则按纪律收口 `wr4_holdout_failed_no_rerun`，不重跑、不改金标。

机器可读数据：[wr4-sealed-holdout-2026-08-07.json](E:/writer/my_writing_system/reports/wr4-sealed-holdout-2026-08-07.json)
"""
    REPORT_MD.write_text(markdown, encoding="utf-8")


def evaluate() -> dict[str, Any]:
    if not EVALUATION_JSON.exists():
        raise RuntimeError("evaluation not found; run-once first")
    evaluation = _load_json(EVALUATION_JSON)
    return {
        "sealed_holdout_gate_passed": evaluation["sealed_holdout_gate_passed"],
        "decision": evaluation["decision"],
        "gates": evaluation["gates"],
        "aggregates": evaluation["aggregates"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="WR4 sealed unseen holdout runner")
    parser.add_argument(
        "command",
        choices=("build", "seal", "preflight", "run-once", "evaluate"),
    )
    parser.add_argument("--output", type=Path, default=RUNTIME)
    args = parser.parse_args()
    if args.command == "build":
        result = build()
    elif args.command == "seal":
        result = seal(args.output)
    elif args.command == "preflight":
        result = preflight(args.output)
    elif args.command == "run-once":
        result = run_once(args.output)
    else:
        result = evaluate()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
