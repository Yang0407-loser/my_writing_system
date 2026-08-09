from __future__ import annotations

import hashlib
import json
import random
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.utils.llm_client import estimate_messages_tokens, estimate_tokens, get_llm_client

from .ablation_prompts import ABLATION_ARMS, build_ablation_messages
from .dedupe import inspect_overlap, require_safe_demonstrations
from .models import CompletionMetadata, PreparedAblationStyle


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ABLATION_MANIFEST = (
    ROOT / "experiments" / "style_control" / "fixtures" / "style_contract_ablation_manifest.json"
)
DEFAULT_ABLATION_PREPARED = (
    ROOT / "experiments" / "style_control" / "fixtures" / "style_contract_ablation_prepared.json"
)
DEFAULT_ABLATION_RUN_DIR = ROOT / "outputs" / "style-contract-ablation"
LOCAL_ACCOUNTING_USD_PER_TOKEN = 0.000000435


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _blind_digits(text: str) -> str:
    return f"{int(_sha(text), 16) % 1_000_000:06d}"


def _clean_completion(text: str) -> str:
    value = text.strip()
    value = re.sub(r"^```(?:text|markdown)?\s*", "", value)
    value = re.sub(r"\s*```$", "", value)
    return value.strip()


def _reference(manifest_path: Path, style: dict[str, Any]) -> tuple[Path, str]:
    path = (manifest_path.parent / style["reference_path"]).resolve()
    return path, path.read_text(encoding="utf-8").strip()


def build_ablation_plan(
    manifest_path: Path = DEFAULT_ABLATION_MANIFEST,
    prepared_path: Path = DEFAULT_ABLATION_PREPARED,
    run_dir: Path = DEFAULT_ABLATION_RUN_DIR,
) -> dict[str, Any]:
    manifest = _read_json(manifest_path)
    prepared = _read_json(prepared_path)
    if manifest["schema_version"] != "2.0" or prepared["schema_version"] != "2.0":
        raise ValueError("contract ablation requires schema_version 2.0")
    prompt_dir = run_dir / "prompts"
    result_dir = run_dir / "results"
    prompt_dir.mkdir(parents=True, exist_ok=True)
    result_dir.mkdir(parents=True, exist_ok=True)

    samples: list[dict[str, Any]] = []
    safety_by_style: dict[str, Any] = {}
    for style in manifest["styles"]:
        reference_path, reference = _reference(manifest_path, style)
        prepared_style = PreparedAblationStyle.model_validate(prepared["styles"][style["id"]])
        if prepared_style.evidence.reference_sha256 != _sha(reference):
            raise ValueError(f"reference hash mismatch: {style['id']}")
        safety_by_style[style["id"]] = require_safe_demonstrations(
            prepared_style.style_demonstrations,
            reference=reference,
            protected_terms=prepared_style.evidence.protected_terms,
        )
        for scene in manifest["scenes"]:
            for arm in manifest["experiment"]["arms"]:
                if arm not in ABLATION_ARMS:
                    raise ValueError(f"unsupported arm in manifest: {arm}")
                for repeat in range(1, manifest["experiment"]["repeats"] + 1):
                    sample_id = f"{style['id']}__{scene['id']}__{arm}__r{repeat}"
                    messages, components = build_ablation_messages(
                        arm=arm,
                        signature=prepared_style.style_signature,
                        demonstrations=prepared_style.style_demonstrations,
                        scene=scene,
                        shared_context=manifest["shared_context"],
                        target_chars=manifest["experiment"]["target_chars"],
                    )
                    prompt_path = prompt_dir / f"{sample_id}.json"
                    result_path = result_dir / f"{sample_id}.json"
                    non_style_keys = {
                        "global_prose_rules",
                        "scene_task",
                        "characters",
                        "world_facts",
                        "mandatory_events",
                        "forbidden_events",
                        "length_and_output_contract",
                    }
                    non_style_hash = _sha(
                        json.dumps(
                            {
                                key: components[key]
                                for key in sorted(non_style_keys)
                            },
                            ensure_ascii=False,
                            sort_keys=True,
                        )
                    )
                    _write_json(
                        prompt_path,
                        {
                            "schema_version": "2.0",
                            "sample_id": sample_id,
                            "messages": messages,
                            "anonymous_experiment_components": list(components),
                            "component_telemetry": components,
                            "non_style_prompt_hash": non_style_hash,
                            "evidence_included": False,
                        },
                    )
                    samples.append(
                        {
                            "sample_id": sample_id,
                            "style_id": style["id"],
                            "scene_id": scene["id"],
                            "arm": arm,
                            "repeat": repeat,
                            "seed": manifest["experiment"]["base_seed"] + repeat,
                            "target_chars": manifest["experiment"]["target_chars"],
                            "reference_path": str(reference_path),
                            "prompt_path": str(prompt_path),
                            "result_path": str(result_path),
                            "status": "planned",
                            "model": manifest["experiment"]["model"],
                            "temperature": manifest["experiment"]["temperature"],
                            "max_tokens": manifest["experiment"]["max_tokens"],
                            "estimated_input_tokens": estimate_messages_tokens(messages),
                            "non_style_prompt_hash": non_style_hash,
                            "failure": None,
                            "metadata": None,
                        }
                    )
    payload = {
        "schema_version": "2.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "kind": "style_contract_causal_ablation",
        "experiment_enabled_by_default": bool(manifest["experiment"]["enabled"]),
        "production_behavior_changed": False,
        "handover_enabled": False,
        "mock_results_are_route_evidence": False,
        "manifest_path": str(manifest_path.resolve()),
        "prepared_path": str(prepared_path.resolve()),
        "sample_count": len(samples),
        "safety_by_style": safety_by_style,
        "samples": samples,
    }
    _write_json(run_dir / "contract_ablation_run_manifest.json", payload)
    return payload


def _mock_completion(sample: dict[str, Any]) -> str:
    if sample["scene_id"] == "SC1":
        return (
            "【MOCK，仅验证管线】许栀把钥匙放到桌面中央。\n\n"
            "“租约不续了。”\n“明天谁去？”\n“你可以去。我不能替你答应。”\n"
            "沈闻没有拿钥匙。“九点再说。”\n\n"
            "钥匙仍在两人之间，分工暂定，责任没有被代领。"
        )
    return (
        "【MOCK，仅验证管线】两人进入受潮仓库，屋顶的漏水很快加重。\n\n"
        "“先动哪箱？”\n“顾客寄存的旧信。”\n"
        "他们先把旧信移到干处，其余货物只按受潮程度划出临时转移顺序。\n\n"
        "雨仍在漏，货物没有全部获救，临时方案到此收束。"
    )


def run_ablation_samples(
    run_dir: Path = DEFAULT_ABLATION_RUN_DIR,
    *,
    backend: str,
    rerun_id: str | None = None,
    allow_real_calls: bool = False,
) -> dict[str, Any]:
    plan_path = run_dir / "contract_ablation_run_manifest.json"
    plan = _read_json(plan_path)
    if backend == "llm" and not allow_real_calls:
        raise PermissionError(
            "real contract-ablation calls are disabled; explicit approval and "
            "--enable-real-calls are required"
        )
    llm = get_llm_client() if backend == "llm" else None
    for sample in plan["samples"]:
        if rerun_id and sample["sample_id"] != rerun_id:
            continue
        result_path = Path(sample["result_path"])
        if result_path.exists():
            existing = _read_json(result_path)
            if not rerun_id and existing.get("status") in {"completed", "mock_completed"}:
                hard_flags = existing.get("hard_gate_flags", {})
                if "protected_term_hit" in hard_flags:
                    hard_flags.pop("protected_term_hit", None)
                    metrics = existing.get("copy_safety_metrics", {})
                    existing["manual_copy_review_locators"] = {
                        "shared_8gram": metrics.get("shared_8gram_unique_count", 0) > 0,
                        "protected_term_hits": metrics.get("protected_term_hits", []),
                    }
                    _write_json(result_path, existing)
                sample["status"] = existing["status"]
                sample["metadata"] = existing.get("metadata")
                continue
            if rerun_id and existing.get("status") != "failed":
                raise ValueError("rerun-id is restricted to failed samples")
        prompt = _read_json(Path(sample["prompt_path"]))
        started = time.perf_counter()
        metadata: dict[str, Any] = {}
        try:
            if backend == "mock":
                raw = _mock_completion(sample)
                status = "mock_completed"
                metadata = {
                    "finish_reason": "mock",
                    "input_tokens": sample["estimated_input_tokens"],
                    "output_tokens": estimate_tokens(raw),
                    "latency_seconds": round(time.perf_counter() - started, 4),
                }
            else:
                raw = llm.chat_completion(
                    prompt["messages"],
                    temperature=sample["temperature"],
                    max_tokens=sample["max_tokens"],
                    prompt_name="style_contract_ablation_generation",
                    completion_metadata_sink=metadata.update,
                )
                status = "completed"
            cleaned = _clean_completion(raw)
            reference = Path(sample["reference_path"]).read_text(encoding="utf-8")
            prepared = _read_json(Path(plan["prepared_path"]))
            protected_terms = prepared["styles"][sample["style_id"]]["evidence"]["protected_terms"]
            copy_metrics = inspect_overlap(cleaned, reference, protected_terms)
            truncated = metadata.get("finish_reason") == "length"
            result = {
                "schema_version": "2.0",
                "sample_id": sample["sample_id"],
                "status": status,
                "backend": backend,
                "route_evidence": backend == "llm",
                "mock_warning": backend == "mock",
                "raw_completion": raw,
                "cleaned_text": cleaned,
                "metadata": metadata,
                "copy_safety_metrics": copy_metrics,
                "hard_gate_flags": {
                    "exact_sentence_copy": copy_metrics["exact_copied_sentence_count"] > 0,
                    "shared_12gram": copy_metrics["shared_12gram_unique_count"] > 0,
                    "truncated": truncated,
                },
                "manual_copy_review_locators": {
                    "shared_8gram": copy_metrics["shared_8gram_unique_count"] > 0,
                    "protected_term_hits": copy_metrics["protected_term_hits"],
                },
            }
            sample["status"] = status
            sample["metadata"] = CompletionMetadata.model_validate(metadata).model_dump()
            sample["failure"] = None
        except Exception as exc:
            result = {
                "schema_version": "2.0",
                "sample_id": sample["sample_id"],
                "status": "failed",
                "backend": backend,
                "route_evidence": False,
                "error": f"{type(exc).__name__}: {exc}",
            }
            sample["status"] = "failed"
            sample["failure"] = result["error"]
        _write_json(result_path, result)
        _write_json(plan_path, plan)
    return plan


def compute_ablation_metrics(run_dir: Path = DEFAULT_ABLATION_RUN_DIR) -> dict[str, Any]:
    plan = _read_json(run_dir / "contract_ablation_run_manifest.json")
    rows: list[dict[str, Any]] = []
    for sample in plan["samples"]:
        result_path = Path(sample["result_path"])
        if not result_path.exists():
            continue
        result = _read_json(result_path)
        if result.get("status") not in {"completed", "mock_completed"}:
            continue
        rows.append(
            {
                "sample_id": sample["sample_id"],
                "style_id": sample["style_id"],
                "scene_id": sample["scene_id"],
                "arm": sample["arm"],
                "repeat": sample["repeat"],
                "route_evidence": result["route_evidence"],
                "metadata": result.get("metadata", {}),
                "copy_safety_metrics": result["copy_safety_metrics"],
                "hard_gate_flags": result["hard_gate_flags"],
            }
        )
    payload = {
        "schema_version": "2.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "route_decision_allowed": bool(rows) and all(row["route_evidence"] for row in rows),
        "mock_results_excluded_from_route_conclusions": True,
        "single_total_score_prohibited": True,
        "human_review_required_for": [
            "style_identification",
            "target_closeness",
            "six_style_dimensions",
            "seven_quality_dimensions",
            "plot_character_error",
            "core_task_miss",
            "prompt_conflict",
        ],
        "rows": rows,
    }
    _write_json(run_dir / "contract-ablation-metrics.json", payload)
    return payload


def anonymise_ablation(
    run_dir: Path = DEFAULT_ABLATION_RUN_DIR,
    *,
    seed: int = 20260728,
) -> dict[str, Any]:
    plan = _read_json(run_dir / "contract_ablation_run_manifest.json")
    source = _read_json(Path(plan["manifest_path"]))
    experiment = source["experiment"]
    baseline_arm = experiment.get("pair_baseline_arm", "D0")
    candidate_arms = experiment.get(
        "pair_candidate_arms",
        ["D1", "D2", "D3", "F0"],
    )
    completed: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for sample in plan["samples"]:
        path = Path(sample["result_path"])
        if path.exists():
            result = _read_json(path)
            if result.get("status") in {"completed", "mock_completed"}:
                completed.append((sample, result))
    rng = random.Random(seed)
    rng.shuffle(completed)
    public_samples = []
    private_samples = []
    for ordinal, (sample, result) in enumerate(completed, 1):
        blind_id = f"文本-{ordinal:03d}-{_blind_digits(sample['sample_id'])}"
        public_samples.append(
            {
                "blind_id": blind_id,
                "scene_code": "场景甲" if sample["scene_id"] == "SC1" else "场景乙",
                "text": result["cleaned_text"],
            }
        )
        private_samples.append(
            {
                "blind_id": blind_id,
                "sample_id": sample["sample_id"],
                "style_id": sample["style_id"],
                "scene_id": sample["scene_id"],
                "arm": sample["arm"],
                "repeat": sample["repeat"],
                "route_evidence": result["route_evidence"],
            }
        )

    lookup = {
        (sample["scene_id"], sample["repeat"], sample["arm"]): (sample, result)
        for sample, result in completed
    }
    public_pairs = []
    private_pairs = []
    ordinal = 0
    for scene_id in sorted({sample["scene_id"] for sample, _ in completed}):
        for repeat in sorted({sample["repeat"] for sample, _ in completed}):
            baseline = lookup.get((scene_id, repeat, baseline_arm))
            if not baseline:
                continue
            for arm in candidate_arms:
                candidate = lookup.get((scene_id, repeat, arm))
                if not candidate:
                    continue
                ordinal += 1
                options = [baseline, candidate]
                rng.shuffle(options)
                option_ids = [item[0]["sample_id"] for item in options]
                pair_id = f"配对-{ordinal:03d}-{_blind_digits('|'.join(option_ids))}"
                public_pairs.append(
                    {
                        "pair_id": pair_id,
                        "scene_code": "场景甲" if scene_id == "SC1" else "场景乙",
                        "text_1": options[0][1]["cleaned_text"],
                        "text_2": options[1][1]["cleaned_text"],
                    }
                )
                private_pairs.append(
                    {
                        "pair_id": pair_id,
                        "option_1_sample_id": option_ids[0],
                        "option_2_sample_id": option_ids[1],
                        "option_1_arm": options[0][0]["arm"],
                        "option_2_arm": options[1][0]["arm"],
                    }
                )
    route_evidence = bool(completed) and all(result["route_evidence"] for _, result in completed)
    public = {
        "schema_version": "2.0",
        "mock_warning": not route_evidence,
        "review_dimensions": {
            "single_sample": [
                "S1/S2/S3 三选一",
                "目标 S3 接近度",
                "风格六项评分",
                "文本质量七项评分",
                "情节或人物错误",
                "核心任务漏写",
                "严重 Prompt 冲突",
            ],
            "paired": ["更接近目标 S3", "文本质量偏好"],
        },
        "samples": public_samples,
        "pairs": public_pairs,
    }
    private = {
        "schema_version": "2.0",
        "samples": private_samples,
        "pairs": private_pairs,
    }
    template = {
        "reviewer_id": "",
        "review_scope": {
            "independent_blind_review": True,
            "private_key_accessed": False,
            "other_reviews_accessed": False,
        },
        "samples": [
            {
                "blind_id": item["blind_id"],
                "style_choice": None,
                "s3_closeness": None,
                "style_scores": {
                    field: None
                    for field in (
                        "narrative_distance",
                        "sentence_rhythm",
                        "paragraph_rhythm",
                        "dialogue_function",
                        "dialogue_texture",
                        "emotional_mediation",
                    )
                },
                "quality_scores": {
                    field: None
                    for field in (
                        "naturalness",
                        "scene_completion",
                        "character_credibility",
                        "emotional_layers",
                        "mechanical_problem",
                        "repetition_problem",
                        "overall_reading_preference",
                    )
                },
                "hard_flags": {
                    "plot_or_character_error": None,
                    "core_task_miss": None,
                    "severe_prompt_conflict": None,
                },
                "hard_error_evidence": "",
                "comment": "",
            }
            for item in public_samples
        ],
        "pairs": [
            {
                "pair_id": item["pair_id"],
                "closer_to_s3": None,
                "better_quality": None,
                "confidence": None,
                "comment": "",
            }
            for item in public_pairs
        ],
    }
    _write_json(run_dir / "blind-review-public.json", public)
    _write_json(run_dir / "blind-review-key.private.json", private)
    _write_json(run_dir / "blind-review-template.json", template)
    return public


def estimate_ablation_cost(run_dir: Path = DEFAULT_ABLATION_RUN_DIR) -> dict[str, Any]:
    plan = _read_json(run_dir / "contract_ablation_run_manifest.json")
    source = _read_json(Path(plan["manifest_path"]))
    input_tokens = sum(item["estimated_input_tokens"] for item in plan["samples"])
    expected_output = (
        len(plan["samples"]) * source["experiment"]["expected_output_tokens_per_sample"]
    )
    output_cap = sum(item["max_tokens"] for item in plan["samples"])
    expected_total = input_tokens + expected_output
    estimate = {
        "schema_version": "2.0",
        "model": source["experiment"]["model"],
        "planned_real_calls": len(plan["samples"]),
        "includes_f0": "F0" in source["experiment"]["arms"],
        "exception_regressions": source["exception_regressions"],
        "estimated_input_tokens": input_tokens,
        "expected_output_tokens": expected_output,
        "output_token_hard_cap": output_cap,
        "local_accounting_cost_proxy_usd": round(
            expected_total * LOCAL_ACCOUNTING_USD_PER_TOKEN, 6
        ),
        "local_accounting_cost_proxy_at_hard_cap_usd": round(
            (input_tokens + output_cap) * LOCAL_ACCOUNTING_USD_PER_TOKEN, 6
        ),
        "cost_proxy_limitation": (
            "Uses the repository's existing $0.000000435/token accounting constant; "
            "the provider has no separate current input/output price table configured locally."
        ),
        "runtime_estimate_minutes": [
            max(1, round(len(plan["samples"]) * 24.4 / 60)),
            max(2, round(len(plan["samples"]) * 42 / 60)),
        ],
        "runtime_basis": (
            f"{len(plan['samples'])} sequential generation calls at the first-round "
            "observed 24.4s average plus service variance."
        ),
        "max_tokens": source["experiment"]["max_tokens"],
        "truncation_prevention": [
            "raise max_tokens from 2200 to 3000 while retaining target_chars=1000",
            "explicitly require a complete final sentence and core-task closure",
            "preserve finish_reason=length samples as hard-gate failures",
            "do not auto-hide or replace truncated samples",
        ],
        "resume_supported": True,
        "failed_sample_rerun_supported": True,
        "output_directory": str(run_dir.resolve()),
    }
    _write_json(run_dir / "real-run-cost-estimate.json", estimate)
    return estimate
