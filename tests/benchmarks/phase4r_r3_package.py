"""Prepare, run, import, and evaluate the isolated Phase 4R R3 A/B/C package.

Only the explicit ``run --confirm-private-inputs`` command may call the LLM.
Prompts, arm mappings, and generated prose remain under a gitignored runtime
directory. Public manifests contain hashes and source metadata only.
"""

from __future__ import annotations

import argparse
import copy
import gc
import hashlib
import json
import random
import shutil
import time
from pathlib import Path
from statistics import mean
from typing import Any

from chromadb.api.client import SharedSystemClient

from app.config import settings
from app.context_ab_evaluation import deterministic_output_checks
from app.context_ab_shadow import assemble_shadow_messages, messages_hash, messages_tokens
from app.utils.llm_client import cost_label, get_llm_client, get_token_breakdown, reset_token_counter
from tests.benchmarks.benchmark_phase4r_r2_scene_spec import SCENES, compile_scene
from tests.benchmarks.run_minimal_restoration_experiment import _build_live_sample, _load_frozen


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RUNTIME = ROOT / ".phase4r_r3_runtime"
DEFAULT_PUBLIC_MANIFEST = ROOT / "reports" / "phase4r-batch-r3-package-manifest.json"
TARGET_QUERIES = (4, 6, 7, 8)
ARMS = ("legacy_full", "budgeted_broker", "broker_scene_spec")
TASK_ID = "07d1391e-06ff-4af3-8bd7-6a404d2f4fd6"
SCENE_HEADER = "\n\n## SceneSpec（结构化写作约束）\n"


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def attach_scene_spec(messages: list[dict[str, str]], rendered: str) -> list[dict[str, str]]:
    result = copy.deepcopy(messages)
    user_indices = [index for index, item in enumerate(result) if item.get("role") == "user"]
    if not user_indices:
        raise ValueError("Writer messages contain no user message")
    result[user_indices[-1]]["content"] += SCENE_HEADER + rendered
    return result


def _context_manifest(run: dict[str, Any], *, force_keep: bool = False) -> list[dict[str, Any]]:
    allowed = {
        "item_id", "source_id", "source_type", "requirement", "priority",
        "text_hash", "estimated_tokens", "injection_position", "section", "subsection",
        "keep", "keep_reason", "drop_reason", "provenance",
    }
    result = []
    for item in run["items"]:
        public = {key: value for key, value in item.items() if key in allowed}
        if force_keep:
            public.update({"keep": True, "keep_reason": "legacy_full", "drop_reason": None})
        result.append(public)
    return result


def prepare_query(query_index: int, runtime_dir: Path) -> dict[str, Any]:
    if query_index not in TARGET_QUERIES:
        raise ValueError(f"query-index must be one of {TARGET_QUERIES}")
    batch1_sample, batch2_sample, frozen_budgeted = _load_frozen(query_index)
    sample, store, rag_items = _build_live_sample(query_index)
    try:
        assembled = assemble_shadow_messages(sample, frozen_budgeted)
        if assembled["legacy_hash"] != batch2_sample["legacy_messages_hash"]:
            raise AssertionError("legacy_full hash drifted from frozen Batch 2")
        if assembled["shadow_hash"] != batch2_sample["broker_messages_hash"]:
            raise AssertionError("budgeted_broker hash drifted from frozen Batch 2")
        spec, rendered_spec = compile_scene(query_index, SCENES[query_index], TASK_ID)
        scene_messages = attach_scene_spec(assembled["shadow_messages"], rendered_spec)
        arms = {
            "legacy_full": assembled["legacy_messages"],
            "budgeted_broker": assembled["shadow_messages"],
            "broker_scene_spec": scene_messages,
        }
        order = list(ARMS)
        random.Random(44000 + query_index).shuffle(order)
        candidate_for_arm = {arm: f"candidate_{position + 1:02d}" for position, arm in enumerate(order)}
        scene_sources = [item.model_dump(exclude={"excerpt"}) for item in spec.evidence]
        metadata = {
            "legacy_full": {
                "messages_hash": messages_hash(arms["legacy_full"]),
                "estimated_input_tokens": messages_tokens(arms["legacy_full"]),
                "context_items": _context_manifest(batch1_sample["profiles"]["legacy_full"], force_keep=True),
                "scene_spec_source_manifest": [],
            },
            "budgeted_broker": {
                "messages_hash": messages_hash(arms["budgeted_broker"]),
                "estimated_input_tokens": messages_tokens(arms["budgeted_broker"]),
                "context_items": _context_manifest(frozen_budgeted),
                "scene_spec_source_manifest": [],
            },
            "broker_scene_spec": {
                "messages_hash": messages_hash(arms["broker_scene_spec"]),
                "estimated_input_tokens": messages_tokens(arms["broker_scene_spec"]),
                "context_items": _context_manifest(frozen_budgeted),
                "scene_spec_hash": spec.spec_hash,
                "scene_spec_estimated_tokens": spec.estimated_tokens,
                "scene_spec_source_manifest": scene_sources,
            },
        }
        query_dir = runtime_dir / f"q{query_index:02d}"
        query_dir.mkdir(parents=True, exist_ok=True)
        private = {"arms": arms, "order": order, "candidate_for_arm": candidate_for_arm}
        prepare = {
            "schema_version": 1,
            "experiment": "phase4r_batch_r3_writer_abc",
            "query_index": query_index,
            "section": int(batch1_sample["section"]),
            "subsection": int(batch1_sample["subsection"]),
            "model": settings.LLM_MODEL,
            "base_url_host": settings.LLM_BASE_URL.split("//", 1)[-1].split("/", 1)[0],
            "temperature": 0.5,
            "top_p": 0.9,
            "max_tokens": 8000,
            "seed_supported_by_current_client": False,
            "logical_generation_calls": 3,
            "production_messages_hash_unchanged": (
                metadata["legacy_full"]["messages_hash"] == batch2_sample["legacy_messages_hash"]
            ),
            "rag_source_ids": next(
                item["source_ids"] for item in _read_json(
                    ROOT / "reports" / "phase4-batch1-context-broker-shadow.json"
                )["retrieval_runs"] if int(item["query_index"]) == query_index
            ),
            "arms": metadata,
        }
        _write_json(query_dir / "prepare.json", prepare)
        _write_json(query_dir / "messages.private.json", private)
        return prepare
    finally:
        try:
            store._client._system.stop()
            SharedSystemClient.clear_system_cache()
        except Exception:
            pass
        del store, rag_items, sample
        gc.collect()


def prepare_all(runtime_dir: Path, public_manifest: Path) -> dict[str, Any]:
    prepared = [prepare_query(index, runtime_dir) for index in TARGET_QUERIES]
    public = {
        "schema_version": 1,
        "phase": "Phase 4R Batch R3 preparation",
        "status": "prepared_not_generated",
        "target_queries": list(TARGET_QUERIES),
        "arms": list(ARMS),
        "planned_generation_calls": sum(item["logical_generation_calls"] for item in prepared),
        "max_output_token_budget": sum(item["logical_generation_calls"] for item in prepared) * 8000,
        "writer_generation_calls": 0,
        "llm_calls": 0,
        "model": prepared[0]["model"],
        "generation_settings": {"temperature": 0.5, "top_p": 0.9, "max_tokens": 8000},
        "estimated_input_tokens": {
            arm: sum(item["arms"][arm]["estimated_input_tokens"] for item in prepared)
            for arm in ARMS
        },
        "production_messages_hash_unchanged": all(
            item["production_messages_hash_unchanged"] for item in prepared
        ),
        "private_runtime_dir_gitignored": True,
        "anonymous_candidate_order": True,
        "runtime_evaluation_fields_used": [],
        "queries": [{
            "query_index": item["query_index"], "section": item["section"],
            "subsection": item["subsection"], "arms": item["arms"],
        } for item in prepared],
    }
    _write_json(public_manifest, public)
    _write_json(runtime_dir / "package_manifest.private.json", public)
    return public


def run_all(runtime_dir: Path, *, confirmed: bool) -> dict[str, Any]:
    if not confirmed:
        raise RuntimeError("private input transmission not confirmed; pass --confirm-private-inputs")
    if not settings.LLM_API_KEY:
        raise RuntimeError("LLM_API_KEY is not configured; generation aborted")
    reset_token_counter()
    generated = []
    for query_index in TARGET_QUERIES:
        query_dir = runtime_dir / f"q{query_index:02d}"
        prepare = _read_json(query_dir / "prepare.json")
        private = _read_json(query_dir / "messages.private.json")
        current_host = settings.LLM_BASE_URL.split("//", 1)[-1].split("/", 1)[0]
        if settings.LLM_MODEL != prepare["model"] or current_host != prepare["base_url_host"]:
            raise RuntimeError("LLM model or endpoint differs from prepared package")
        candidates = []
        mapping = {}
        for arm in private["order"]:
            candidate_id = private["candidate_for_arm"][arm]
            messages = private["arms"][arm]
            expected_hash = prepare["arms"][arm]["messages_hash"]
            if messages_hash(messages) != expected_hash:
                raise AssertionError(f"q{query_index} {arm}: prepared messages changed")
            started = time.perf_counter()
            with cost_label(f"q{query_index:02d}:{candidate_id}"):
                output = get_llm_client().chat_completion(
                    messages, temperature=prepare["temperature"], max_tokens=prepare["max_tokens"],
                    max_retries=0, top_p=prepare["top_p"], prompt_name="phase4r_batch_r3_shadow_abc",
                )
            elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
            output_path = query_dir / f"{candidate_id}.txt"
            output_path.write_text(output, encoding="utf-8")
            checks = deterministic_output_checks(output)
            candidates.append({
                "candidate_id": candidate_id, "output_sha256": checks["sha256"],
                "characters": checks["characters"], "estimated_output_tokens": checks["estimated_tokens"],
                "paragraph_count": checks["paragraph_count"],
                "duplicate_paragraph_count": checks["duplicate_paragraph_count"],
                "elapsed_ms": elapsed_ms,
            })
            mapping[candidate_id] = {
                "arm": arm, "messages_hash": expected_hash,
                "actual_total_tokens": get_token_breakdown().get(f"q{query_index:02d}:{candidate_id}"),
            }
        blind = {"query_index": query_index, "candidates": candidates}
        _write_json(query_dir / "blind.json", blind)
        _write_json(query_dir / "private_mapping.json", {"query_index": query_index, "mapping": mapping})
        generated.append(blind)
    manifest = {"schema_version": 1, "generation_calls": 12, "queries": generated}
    _write_json(runtime_dir / "run_manifest.json", manifest)
    return manifest


def import_results(source_dir: Path, runtime_dir: Path) -> dict[str, Any]:
    imported = []
    for query_index in TARGET_QUERIES:
        source_query = source_dir / f"q{query_index:02d}"
        target_query = runtime_dir / f"q{query_index:02d}"
        prepare = _read_json(target_query / "prepare.json")
        blind = _read_json(source_query / "blind.json")
        mapping = _read_json(source_query / "private_mapping.json")
        if mapping["query_index"] != query_index or blind["query_index"] != query_index:
            raise AssertionError("query index mismatch in imported results")
        candidate_ids = [item["candidate_id"] for item in blind["candidates"]]
        if len(candidate_ids) != 3 or len(set(candidate_ids)) != 3:
            raise AssertionError("import must contain exactly three unique candidates")
        if set(mapping["mapping"]) != set(candidate_ids):
            raise AssertionError("candidate mapping does not match blind results")
        if {item["arm"] for item in mapping["mapping"].values()} != set(ARMS):
            raise AssertionError("import must contain exactly one result for each A/B/C arm")
        for candidate in blind["candidates"]:
            candidate_id = candidate["candidate_id"]
            arm = mapping["mapping"][candidate_id]["arm"]
            if mapping["mapping"][candidate_id]["messages_hash"] != prepare["arms"][arm]["messages_hash"]:
                raise AssertionError(f"q{query_index} {candidate_id}: messages hash mismatch")
            source_text = source_query / f"{candidate_id}.txt"
            text = source_text.read_text(encoding="utf-8")
            if _sha256_text(text) != candidate["output_sha256"]:
                raise AssertionError(f"q{query_index} {candidate_id}: output hash mismatch")
            target_query.mkdir(parents=True, exist_ok=True)
            if source_text.resolve() != (target_query / source_text.name).resolve():
                shutil.copy2(source_text, target_query / source_text.name)
        if source_query.resolve() != target_query.resolve():
            shutil.copy2(source_query / "blind.json", target_query / "blind.json")
            shutil.copy2(source_query / "private_mapping.json", target_query / "private_mapping.json")
        imported.append({"query_index": query_index, "candidate_count": len(blind["candidates"])})
    manifest = {"schema_version": 1, "validated_candidates": 12, "queries": imported}
    _write_json(runtime_dir / "import_manifest.json", manifest)
    return manifest


def evaluate(runtime_dir: Path, review_path: Path | None = None) -> dict[str, Any]:
    reviews = _read_json(review_path) if review_path else None
    review_by_query = {
        int(item["query_index"]): item for item in reviews.get("reviews", [])
    } if reviews else {}
    samples = []
    for query_index in TARGET_QUERIES:
        query_dir = runtime_dir / f"q{query_index:02d}"
        blind = _read_json(query_dir / "blind.json")
        mapping = _read_json(query_dir / "private_mapping.json")["mapping"]
        candidates = []
        for candidate in blind["candidates"]:
            candidate_id = candidate["candidate_id"]
            text = (query_dir / f"{candidate_id}.txt").read_text(encoding="utf-8")
            if _sha256_text(text) != candidate["output_sha256"]:
                raise AssertionError("output changed after import")
            public_candidate = dict(candidate)
            if reviews:
                public_candidate["arm"] = mapping[candidate_id]["arm"]
            candidates.append(public_candidate)
        samples.append({
            "query_index": query_index, "candidates": candidates,
            "review": review_by_query.get(query_index),
        })
    if not reviews:
        template = {
            "schema_version": 1,
            "anonymous": True,
            "reviews": [{
                "query_index": sample["query_index"],
                "candidate_ids": [item["candidate_id"] for item in sample["candidates"]],
                "target_completion": {}, "hard_violations": {}, "relationship_violations": {},
                "continuity_defects": {}, "factual_errors": {}, "event_order_defects": {},
                "preference": None, "review_provenance": None, "notes": "",
            } for sample in samples],
        }
        _write_json(runtime_dir / "blind_review.template.json", template)
    result = {
        "schema_version": 1,
        "status": "evaluated" if reviews else "awaiting_blind_review",
        "sample_count": len(samples), "candidate_count": sum(len(item["candidates"]) for item in samples),
        "review_provenance": reviews.get("review_provenance") if reviews else None,
        "mean_generation_latency_ms": round(mean(
            candidate["elapsed_ms"] for sample in samples for candidate in sample["candidates"]
        ), 3),
        "samples": samples,
    }
    _write_json(runtime_dir / "evaluation.private.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("prepare", "run", "import", "evaluate"))
    parser.add_argument("--runtime-dir", type=Path, default=DEFAULT_RUNTIME)
    parser.add_argument("--public-manifest", type=Path, default=DEFAULT_PUBLIC_MANIFEST)
    parser.add_argument("--source-dir", type=Path)
    parser.add_argument("--review", type=Path)
    parser.add_argument("--confirm-private-inputs", action="store_true")
    args = parser.parse_args()
    if args.command == "prepare":
        result = prepare_all(args.runtime_dir, args.public_manifest)
    elif args.command == "run":
        result = run_all(args.runtime_dir, confirmed=args.confirm_private_inputs)
    elif args.command == "import":
        if args.source_dir is None:
            parser.error("import requires --source-dir")
        result = import_results(args.source_dir, args.runtime_dir)
    else:
        result = evaluate(args.runtime_dir, args.review)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
