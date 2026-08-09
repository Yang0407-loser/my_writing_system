from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.agents.style_analyzer import StyleAnalyzer
from app.config import settings
from app.utils.json_parser import parse_json
from app.utils.llm_client import estimate_messages_tokens, get_llm_client

from .metrics import compute_metrics
from .models import (
    CompletionMetadata,
    HISTORICAL_STYLE_FIELDS,
    PreparedStyleInput,
    StyleContract,
)
from .prompts import (
    build_control_response_messages,
    build_generation_messages,
    contract_analysis_messages,
    historical_analysis_messages,
    historical_brief_messages,
)
from .ablation import (
    DEFAULT_ABLATION_MANIFEST,
    DEFAULT_ABLATION_PREPARED,
    DEFAULT_ABLATION_RUN_DIR,
    anonymise_ablation,
    build_ablation_plan,
    compute_ablation_metrics,
    estimate_ablation_cost,
    run_ablation_samples,
)


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = ROOT / "experiments" / "style_control" / "fixtures" / "experiment_manifest.json"
DEFAULT_PREPARED = ROOT / "experiments" / "style_control" / "fixtures" / "prepared_style_inputs.mock.json"
DEFAULT_RUN_DIR = ROOT / "outputs" / "style-control-experiment-2026-07-27"

CONTROL_DIMENSIONS: dict[str, dict[str, Any]] = {
    "dialogue_ratio": {
        "low": "让直接引语只占很小部分，主要通过动作和叙述推进。",
        "high": "让人物对话承担约一半篇幅，但每句仍须推进信息或权力关系。",
        "metric": "dialogue_ratio",
        "expected": "increase",
    },
    "short_sentence_ratio": {
        "low": "以完整中长句为主，避免连续短句。",
        "high": "显著增加十二字以内短句，并让短句承担动作转折。",
        "metric": "short_sentence_ratio",
        "expected": "increase",
    },
    "paragraph_length": {
        "low": "段落短促，多在一个动作或一句对话后换段。",
        "high": "使用较长段落组织完整动作链，避免频繁换段。",
        "metric": "paragraph_length_median",
        "expected": "increase",
    },
    "sensory_density": {
        "low": "感官描写保持稀疏，只保留完成场景所需的细节。",
        "high": "增加视觉、听觉、触觉和气味细节，但每项都必须与行动有关。",
        "metric": "sensory_terms_per_1k",
        "expected": "increase",
    },
    "metaphor_frequency": {
        "low": "不用明喻和装饰性比喻，语言保持字面、具体。",
        "high": "适度增加来自仓库、纸张、雨水和修复工具的现场比喻。",
        "metric": "metaphor_density",
        "expected": "increase",
    },
    "adjective_density": {
        "low": "压低形容词使用，优先选择准确名词和动词。",
        "high": "提高形容词密度，但避免同义词堆叠。",
        "metric": "adjective_density",
        "expected": "increase",
    },
    "dialogue_tag_style": {
        "low": "尽量使用零标记或动作替代，不密集写“他说/她说”。",
        "high": "大多数对白使用明确的说话人标记，保证归属清晰。",
        "metric": "human_dialogue_tag_explicitness",
        "expected": "increase",
    },
    "sentence_opening_style": {
        "low": "多用人物主语开头，允许形成相对单一的起句模式。",
        "high": "主动变化句首，在动作、环境、对话和物件之间切换。",
        "metric": "human_sentence_opening_diversity",
        "expected": "increase",
    },
}


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _clean_completion(text: str) -> str:
    value = text.strip()
    value = re.sub(r"^```(?:text|markdown)?\s*", "", value)
    value = re.sub(r"\s*```$", "", value)
    return value.strip()


def _load_reference(manifest_path: Path, style: dict[str, Any]) -> tuple[Path, str]:
    path = (manifest_path.parent / style["reference_path"]).resolve()
    return path, path.read_text(encoding="utf-8").strip()


def prepare_inputs(
    manifest_path: Path,
    output_path: Path,
    backend: str,
) -> dict[str, Any]:
    manifest = _read_json(manifest_path)
    if backend == "mock":
        payload = _read_json(DEFAULT_PREPARED)
        _write_json(output_path, payload)
        return payload

    llm = get_llm_client()
    current_analyzer = StyleAnalyzer()
    prepared: dict[str, Any] = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "backend": "llm",
        "historical_contract_label": "50D",
        "historical_recovered_field_count": len(HISTORICAL_STYLE_FIELDS),
        "styles": {},
    }
    for style in manifest["styles"]:
        _, reference = _load_reference(manifest_path, style)

        # B must call the current analyzer without manual polishing.
        four = current_analyzer.analyze(reference)

        historical_raw = llm.chat_completion(
            historical_analysis_messages(reference),
            temperature=0.3,
            max_tokens=3000,
            json_mode=True,
            prompt_name="style_experiment_historical_analysis",
        )
        historical_parsed = parse_json(historical_raw)
        historical_profile = {
            field: historical_parsed.get(field)
            for field in HISTORICAL_STYLE_FIELDS
        }
        unavailable = [
            field for field, value in historical_profile.items() if value is None
        ]
        historical_brief = llm.chat_completion(
            historical_brief_messages(historical_profile),
            temperature=0.5,
            max_tokens=800,
            prompt_name="style_experiment_historical_brief",
        )

        contract_raw = llm.chat_completion(
            contract_analysis_messages(reference),
            temperature=0.3,
            max_tokens=1800,
            json_mode=True,
            prompt_name="style_experiment_contract",
        )
        contract = StyleContract.model_validate(parse_json(contract_raw))
        item = PreparedStyleInput(
            style_id=style["id"],
            reference_sha256=_sha(reference),
            source="llm",
            four_dimensional=four,
            historical_profile=historical_profile,
            historical_unavailable_fields=unavailable,
            historical_brief=historical_brief.strip(),
            style_contract=contract,
        )
        prepared["styles"][style["id"]] = item.model_dump()
        _write_json(output_path, prepared)
    return prepared


def build_plan(
    manifest_path: Path,
    prepared_path: Path,
    run_dir: Path,
) -> dict[str, Any]:
    manifest = _read_json(manifest_path)
    prepared = _read_json(prepared_path)
    prompt_dir = run_dir / "prompts"
    result_dir = run_dir / "results"
    prompt_dir.mkdir(parents=True, exist_ok=True)
    result_dir.mkdir(parents=True, exist_ok=True)
    samples: list[dict[str, Any]] = []
    for style in manifest["styles"]:
        reference_path, reference = _load_reference(manifest_path, style)
        prepared_style = prepared["styles"][style["id"]]
        if prepared_style["reference_sha256"] != _sha(reference):
            raise ValueError(f"prepared reference hash mismatch: {style['id']}")
        for scene in manifest["scenes"]:
            for arm in manifest["experiment"]["arms"]:
                for repeat in range(1, manifest["experiment"]["repeats"] + 1):
                    sample_id = f"{style['id']}__{scene['id']}__{arm}__r{repeat}"
                    seed = manifest["experiment"]["base_seed"] + repeat
                    messages = build_generation_messages(
                        arm=arm,
                        prepared=prepared_style,
                        scene=scene,
                        shared_context=manifest["shared_context"],
                        target_chars=manifest["experiment"]["target_chars"],
                    )
                    prompt_path = prompt_dir / f"{sample_id}.json"
                    result_path = result_dir / f"{sample_id}.json"
                    _write_json(
                        prompt_path,
                        {
                            "sample_id": sample_id,
                            "messages": messages,
                            "arm": arm,
                            "style_id": style["id"],
                            "scene_id": scene["id"],
                            "repeat": repeat,
                            "seed": seed,
                            "reference_path": str(reference_path),
                            "style_input_source": prepared_style["source"],
                        },
                    )
                    style_content = messages[-1]["content"].split("## 风格输入", 1)[-1]
                    samples.append(
                        {
                            "sample_id": sample_id,
                            "style_id": style["id"],
                            "scene_id": scene["id"],
                            "arm": arm,
                            "repeat": repeat,
                            "seed": seed,
                            "target_chars": manifest["experiment"]["target_chars"],
                            "reference_path": str(reference_path),
                            "prompt_path": str(prompt_path),
                            "result_path": str(result_path),
                            "status": "planned",
                            "model": manifest["experiment"]["model"],
                            "temperature": manifest["experiment"]["temperature"],
                            "max_tokens": manifest["experiment"]["max_tokens"],
                            "estimated_input_tokens": estimate_messages_tokens(messages),
                            "style_input_hash": _sha(style_content),
                            "failure": None,
                            "metadata": None,
                        }
                    )
    payload = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "production_behavior_changed": False,
        "handover_enabled": False,
        "mock_results_are_route_evidence": False,
        "manifest_path": str(manifest_path.resolve()),
        "prepared_path": str(prepared_path.resolve()),
        "sample_count": len(samples),
        "samples": samples,
    }
    _write_json(run_dir / "run_manifest.json", payload)
    return payload


def build_control_plan(manifest_path: Path, run_dir: Path) -> dict[str, Any]:
    manifest = _read_json(manifest_path)
    prompt_dir = run_dir / "control_prompts"
    result_dir = run_dir / "control_results"
    prompt_dir.mkdir(parents=True, exist_ok=True)
    result_dir.mkdir(parents=True, exist_ok=True)
    samples: list[dict[str, Any]] = []
    for dimension, config in CONTROL_DIMENSIONS.items():
        for scene in manifest["scenes"]:
            for repeat in range(1, manifest["experiment"]["repeats"] + 1):
                for level in ("low", "high"):
                    sample_id = f"CTRL__{dimension}__{scene['id']}__{level}__r{repeat}"
                    messages = build_control_response_messages(
                        dimension=dimension,
                        level=level,
                        instruction=config[level],
                        scene=scene,
                        shared_context=manifest["shared_context"],
                        target_chars=manifest["experiment"]["target_chars"],
                    )
                    prompt_path = prompt_dir / f"{sample_id}.json"
                    result_path = result_dir / f"{sample_id}.json"
                    _write_json(
                        prompt_path,
                        {
                            "sample_id": sample_id,
                            "messages": messages,
                            "dimension": dimension,
                            "level": level,
                            "scene_id": scene["id"],
                            "repeat": repeat,
                            "metric": config["metric"],
                            "expected": config["expected"],
                        },
                    )
                    samples.append(
                        {
                            "sample_id": sample_id,
                            "style_id": "CONTROL",
                            "scene_id": scene["id"],
                            "arm": "CONTROL",
                            "dimension": dimension,
                            "level": level,
                            "repeat": repeat,
                            "seed": manifest["experiment"]["base_seed"] + repeat,
                            "target_chars": manifest["experiment"]["target_chars"],
                            "reference_path": "",
                            "prompt_path": str(prompt_path),
                            "result_path": str(result_path),
                            "status": "planned",
                            "model": manifest["experiment"]["model"],
                            "temperature": manifest["experiment"]["temperature"],
                            "max_tokens": manifest["experiment"]["max_tokens"],
                            "estimated_input_tokens": estimate_messages_tokens(messages),
                            "style_input_hash": _sha(messages[-1]["content"].split("## 风格输入", 1)[-1]),
                            "failure": None,
                            "metadata": None,
                        }
                    )
    payload = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "kind": "single_variable_control_response",
        "production_behavior_changed": False,
        "handover_enabled": False,
        "dimensions": CONTROL_DIMENSIONS,
        "sample_count": len(samples),
        "samples": samples,
    }
    _write_json(run_dir / "control_run_manifest.json", payload)
    return payload


def _mock_completion(sample: dict[str, Any], prompt: dict[str, Any]) -> str:
    # Deliberately obvious synthetic text.  It validates persistence, metrics,
    # resume and anonymisation only and must never be used for route decisions.
    scene = sample["scene_id"]
    return (
        f"【MOCK：{scene}】雨停在玻璃外。许栀把钥匙放到桌面，"
        "沈闻没有立刻去拿。他们确认了仓库的门锁，也确认谁都不能替对方作决定。\n\n"
        "“今晚先到这里。”许栀说。\n\n"
        "灯影落在纸箱边缘，场景任务在占位文本中完成。"
    )


def run_samples(
    run_dir: Path,
    backend: str,
    rerun_id: str | None = None,
    plan_file: str = "run_manifest.json",
) -> dict[str, Any]:
    manifest_path = run_dir / plan_file
    plan = _read_json(manifest_path)
    llm = get_llm_client() if backend == "llm" else None
    for sample in plan["samples"]:
        if rerun_id and sample["sample_id"] != rerun_id:
            continue
        result_path = Path(sample["result_path"])
        if result_path.exists() and not rerun_id:
            existing = _read_json(result_path)
            if existing.get("status") in {"completed", "mock_completed"}:
                sample["status"] = existing["status"]
                sample["metadata"] = existing.get("metadata")
                continue
        prompt = _read_json(Path(sample["prompt_path"]))
        started = time.perf_counter()
        metadata: dict[str, Any] = {}
        try:
            if backend == "mock":
                raw = _mock_completion(sample, prompt)
                status = "mock_completed"
                metadata = {
                    "finish_reason": "mock",
                    "input_tokens": sample["estimated_input_tokens"],
                    "output_tokens": estimate_messages_tokens([{"role": "assistant", "content": raw}]),
                    "latency_seconds": round(time.perf_counter() - started, 4),
                }
            else:
                raw = llm.chat_completion(
                    prompt["messages"],
                    temperature=sample["temperature"],
                    max_tokens=sample["max_tokens"],
                    prompt_name="style_experiment_generation",
                    completion_metadata_sink=metadata.update,
                )
                status = "completed"
            cleaned = _clean_completion(raw)
            result = {
                "schema_version": 1,
                "sample_id": sample["sample_id"],
                "status": status,
                "backend": backend,
                "route_evidence": backend == "llm",
                "raw_completion": raw,
                "cleaned_text": cleaned,
                "metadata": metadata,
            }
            sample["status"] = status
            sample["metadata"] = CompletionMetadata.model_validate(metadata).model_dump()
            sample["failure"] = None
        except Exception as exc:
            result = {
                "schema_version": 1,
                "sample_id": sample["sample_id"],
                "status": "failed",
                "backend": backend,
                "route_evidence": False,
                "error": f"{type(exc).__name__}: {exc}",
            }
            sample["status"] = "failed"
            sample["failure"] = result["error"]
        _write_json(result_path, result)
        _write_json(manifest_path, plan)
    return plan


def compute_run_metrics(run_dir: Path) -> dict[str, Any]:
    plan = _read_json(run_dir / "run_manifest.json")
    source_manifest = _read_json(Path(plan["manifest_path"]))
    names = [
        item.split("：", 1)[0]
        for item in source_manifest["shared_context"]["characters"]
    ]
    rows = []
    for sample in plan["samples"]:
        result_path = Path(sample["result_path"])
        if not result_path.exists():
            continue
        result = _read_json(result_path)
        if result.get("status") not in {"completed", "mock_completed"}:
            continue
        reference = Path(sample["reference_path"]).read_text(encoding="utf-8")
        rows.append(
            {
                "sample_id": sample["sample_id"],
                "style_id": sample["style_id"],
                "scene_id": sample["scene_id"],
                "arm": sample["arm"],
                "repeat": sample["repeat"],
                "route_evidence": result["route_evidence"],
                "metrics": compute_metrics(result["cleaned_text"], reference, names),
                "metadata": result.get("metadata", {}),
            }
        )
    payload = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "route_decision_allowed": bool(rows) and all(row["route_evidence"] for row in rows),
        "proxy_limitations": {
            "sensory_terms_per_1k": "lexical proxy; not image quality",
            "psychological_exposition_per_1k": "observation only; not emotional intensity",
            "mechanical_and_repetition": "risk locators; human quality judgment required",
        },
        "rows": rows,
    }
    _write_json(run_dir / "style-control-experiment-results.json", payload)
    csv_path = run_dir / "style-control-experiment-results.csv"
    scalar_metric_names = sorted(
        {
            key
            for row in rows
            for key, value in row["metrics"].items()
            if isinstance(value, (int, float, str, bool)) or value is None
        }
    )
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "sample_id",
                "style_id",
                "scene_id",
                "arm",
                "repeat",
                "route_evidence",
                *scalar_metric_names,
            ],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    **{key: row[key] for key in writer.fieldnames if key in row},
                    **{
                        key: row["metrics"].get(key)
                        for key in scalar_metric_names
                    },
                }
            )
    return payload


def compute_control_metrics(run_dir: Path) -> dict[str, Any]:
    plan = _read_json(run_dir / "control_run_manifest.json")
    source_manifest = _read_json(DEFAULT_MANIFEST)
    names = [
        item.split("，", 1)[0]
        for item in source_manifest["shared_context"]["characters"]
    ]
    rows: list[dict[str, Any]] = []
    lookup: dict[tuple[str, str, int, str], dict[str, Any]] = {}
    for sample in plan["samples"]:
        result_path = Path(sample["result_path"])
        if not result_path.exists():
            continue
        result = _read_json(result_path)
        if result.get("status") not in {"completed", "mock_completed"}:
            continue
        row = {
            "sample_id": sample["sample_id"],
            "dimension": sample["dimension"],
            "scene_id": sample["scene_id"],
            "repeat": sample["repeat"],
            "level": sample["level"],
            "metric": CONTROL_DIMENSIONS[sample["dimension"]]["metric"],
            "route_evidence": result["route_evidence"],
            "metrics": compute_metrics(result["cleaned_text"], "", names),
            "metadata": result.get("metadata", {}),
        }
        rows.append(row)
        lookup[
            (
                sample["dimension"],
                sample["scene_id"],
                sample["repeat"],
                sample["level"],
            )
        ] = row

    comparisons = []
    for dimension, config in CONTROL_DIMENSIONS.items():
        for scene in source_manifest["scenes"]:
            for repeat in range(1, source_manifest["experiment"]["repeats"] + 1):
                low = lookup.get((dimension, scene["id"], repeat, "low"))
                high = lookup.get((dimension, scene["id"], repeat, "high"))
                if not low or not high:
                    continue
                metric = config["metric"]
                low_value = low["metrics"].get(metric)
                high_value = high["metrics"].get(metric)
                measurable = isinstance(low_value, (int, float)) and isinstance(
                    high_value, (int, float)
                )
                comparisons.append(
                    {
                        "dimension": dimension,
                        "scene_id": scene["id"],
                        "repeat": repeat,
                        "metric": metric,
                        "measurement": "automatic" if measurable else "human_required",
                        "low_value": low_value if measurable else None,
                        "high_value": high_value if measurable else None,
                        "direction_correct": (
                            high_value > low_value if measurable else None
                        ),
                        "route_evidence": low["route_evidence"] and high["route_evidence"],
                    }
                )

    automatic = [
        item for item in comparisons if item["measurement"] == "automatic"
    ]
    payload = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "route_decision_allowed": bool(rows)
        and all(row["route_evidence"] for row in rows),
        "automatic_direction_pass_rate": (
            round(
                sum(bool(item["direction_correct"]) for item in automatic)
                / len(automatic),
                4,
            )
            if automatic
            else None
        ),
        "human_required_dimensions": [
            name
            for name, config in CONTROL_DIMENSIONS.items()
            if config["metric"].startswith("human_")
        ],
        "rows": rows,
        "comparisons": comparisons,
    }
    _write_json(run_dir / "control-response-results.json", payload)
    return payload


def anonymise(run_dir: Path, seed: int) -> dict[str, Any]:
    plan = _read_json(run_dir / "run_manifest.json")
    completed = []
    for sample in plan["samples"]:
        path = Path(sample["result_path"])
        if path.exists():
            result = _read_json(path)
            if result.get("status") in {"completed", "mock_completed"}:
                completed.append((sample, result))
    rng = random.Random(seed)
    rng.shuffle(completed)
    public_rows = []
    private_key = []
    for ordinal, (sample, result) in enumerate(completed, 1):
        blind_id = f"样本-{ordinal:03d}-{_sha(sample['sample_id'])[:6].upper()}"
        public_rows.append(
            {
                "blind_id": blind_id,
                "scene_code": sample["scene_id"],
                "text": result["cleaned_text"],
                "route_evidence": result["route_evidence"],
            }
        )
        private_key.append(
            {
                "blind_id": blind_id,
                "sample_id": sample["sample_id"],
                "style_id": sample["style_id"],
                "scene_id": sample["scene_id"],
                "arm": sample["arm"],
                "repeat": sample["repeat"],
            }
        )

    # Pair each non-baseline arm with A under identical style/scene/repeat.
    lookup = {
        (sample["style_id"], sample["scene_id"], sample["repeat"], sample["arm"]): (
            sample,
            result,
        )
        for sample, result in completed
    }
    pairs = []
    pair_key = []
    pair_ordinal = 0
    for key, (sample, result) in sorted(lookup.items()):
        style_id, scene_id, repeat, arm = key
        if arm == "A":
            continue
        baseline = lookup.get((style_id, scene_id, repeat, "A"))
        if not baseline:
            continue
        pair_ordinal += 1
        options = [
            ("A", baseline[1]["cleaned_text"], baseline[0]["sample_id"]),
            (arm, result["cleaned_text"], sample["sample_id"]),
        ]
        rng.shuffle(options)
        pair_id = f"配对-{pair_ordinal:03d}-{_sha('|'.join(item[2] for item in options))[:6].upper()}"
        pairs.append(
            {
                "pair_id": pair_id,
                "target_style_code": style_id,
                "scene_code": scene_id,
                "text_1": options[0][1],
                "text_2": options[1][1],
            }
        )
        pair_key.append(
            {
                "pair_id": pair_id,
                "option_1_arm": options[0][0],
                "option_2_arm": options[1][0],
                "option_1_sample_id": options[0][2],
                "option_2_sample_id": options[1][2],
            }
        )
    public = {
        "schema_version": 1,
        "mock_warning": not all(row["route_evidence"] for row in public_rows),
        "style_identification_answer_is_private": True,
        "samples": public_rows,
        "pairs": pairs,
    }
    private = {"schema_version": 1, "samples": private_key, "pairs": pair_key}
    _write_json(run_dir / "blind-review-public.json", public)
    _write_json(run_dir / "blind-review-key.private.json", private)
    return public


def estimate_cost(run_dir: Path) -> dict[str, Any]:
    plan = _read_json(run_dir / "run_manifest.json")
    generation_input = sum(item["estimated_input_tokens"] for item in plan["samples"])
    generation_output_cap = sum(item["max_tokens"] for item in plan["samples"])
    preprocessing_calls = 0
    prepared = _read_json(Path(plan["prepared_path"]))
    if prepared.get("backend") == "mock":
        # Real preparation: B one call, C two calls, D one call per style.
        preprocessing_calls = len(prepared["styles"]) * 4
    control_path = run_dir / "control_run_manifest.json"
    control = _read_json(control_path) if control_path.exists() else {"samples": []}
    control_input = sum(item["estimated_input_tokens"] for item in control["samples"])
    control_output_cap = sum(item["max_tokens"] for item in control["samples"])
    estimate = {
        "model": settings.LLM_MODEL,
        "main_generation_calls": len(plan["samples"]),
        "control_response_calls": len(control["samples"]),
        "preprocessing_calls": preprocessing_calls,
        "main_experiment_total_calls": len(plan["samples"]) + preprocessing_calls,
        "full_experiment_total_calls": len(plan["samples"]) + len(control["samples"]) + preprocessing_calls,
        "estimated_main_generation_input_tokens": generation_input,
        "main_generation_output_token_cap": generation_output_cap,
        "estimated_control_input_tokens": control_input,
        "control_output_token_cap": control_output_cap,
        "estimated_preprocessing_tokens": {
            "status": "rough_range",
            "input": [24000, 36000],
            "output": [9000, 15000],
        },
        "estimated_total_token_range": [
            generation_input + control_input + 24000 + 9000,
            generation_input + control_input + generation_output_cap + control_output_cap + 36000 + 15000,
        ],
        "usd_cost": None,
        "usd_cost_reason": "No model price table is configured locally; do not guess current provider pricing.",
        "runtime_estimate_minutes": [17, 70],
        "runtime_basis": "124 sequential calls when the main and control-response matrices both run; actual rate limits and response lengths dominate.",
    }
    _write_json(run_dir / "real-run-cost-estimate.json", estimate)
    return estimate


def main() -> None:
    parser = argparse.ArgumentParser(description="Isolated A/B/C/D style-control experiment")
    sub = parser.add_subparsers(dest="command", required=True)

    prepare_parser = sub.add_parser("prepare")
    prepare_parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    prepare_parser.add_argument("--output", type=Path, required=True)
    prepare_parser.add_argument("--backend", choices=("mock", "llm"), default="mock")

    plan_parser = sub.add_parser("plan")
    plan_parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    plan_parser.add_argument("--prepared", type=Path, default=DEFAULT_PREPARED)
    plan_parser.add_argument("--run-dir", type=Path, default=DEFAULT_RUN_DIR)

    control_plan_parser = sub.add_parser("control-plan")
    control_plan_parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    control_plan_parser.add_argument("--run-dir", type=Path, default=DEFAULT_RUN_DIR)

    run_parser = sub.add_parser("run")
    run_parser.add_argument("--run-dir", type=Path, default=DEFAULT_RUN_DIR)
    run_parser.add_argument("--backend", choices=("mock", "llm"), default="mock")
    run_parser.add_argument("--rerun-id")
    run_parser.add_argument("--plan-file", default="run_manifest.json")

    metrics_parser = sub.add_parser("metrics")
    metrics_parser.add_argument("--run-dir", type=Path, default=DEFAULT_RUN_DIR)

    control_metrics_parser = sub.add_parser("control-metrics")
    control_metrics_parser.add_argument("--run-dir", type=Path, default=DEFAULT_RUN_DIR)

    blind_parser = sub.add_parser("anonymise")
    blind_parser.add_argument("--run-dir", type=Path, default=DEFAULT_RUN_DIR)
    blind_parser.add_argument("--seed", type=int, default=20260727)

    cost_parser = sub.add_parser("estimate")
    cost_parser.add_argument("--run-dir", type=Path, default=DEFAULT_RUN_DIR)

    ablation_parser = sub.add_parser("contract-ablation")
    ablation_parser.add_argument(
        "action",
        choices=("plan", "run", "metrics", "anonymise", "estimate", "mock-all"),
        nargs="?",
        default="plan",
    )
    ablation_parser.add_argument("--manifest", type=Path, default=DEFAULT_ABLATION_MANIFEST)
    ablation_parser.add_argument("--prepared", type=Path, default=DEFAULT_ABLATION_PREPARED)
    ablation_parser.add_argument("--run-dir", type=Path, default=DEFAULT_ABLATION_RUN_DIR)
    ablation_parser.add_argument("--backend", choices=("mock", "llm"), default="mock")
    ablation_parser.add_argument("--rerun-id")
    ablation_parser.add_argument("--seed", type=int, default=20260728)
    ablation_parser.add_argument("--enable-real-calls", action="store_true")

    args = parser.parse_args()
    if args.command == "prepare":
        result = prepare_inputs(args.manifest, args.output, args.backend)
    elif args.command == "plan":
        result = build_plan(args.manifest, args.prepared, args.run_dir)
    elif args.command == "control-plan":
        result = build_control_plan(args.manifest, args.run_dir)
    elif args.command == "run":
        result = run_samples(args.run_dir, args.backend, args.rerun_id, args.plan_file)
    elif args.command == "metrics":
        result = compute_run_metrics(args.run_dir)
    elif args.command == "control-metrics":
        result = compute_control_metrics(args.run_dir)
    elif args.command == "anonymise":
        result = anonymise(args.run_dir, args.seed)
    elif args.command == "estimate":
        result = estimate_cost(args.run_dir)
    elif args.action == "plan":
        result = build_ablation_plan(args.manifest, args.prepared, args.run_dir)
    elif args.action == "run":
        result = run_ablation_samples(
            args.run_dir,
            backend=args.backend,
            rerun_id=args.rerun_id,
            allow_real_calls=args.enable_real_calls,
        )
    elif args.action == "metrics":
        result = compute_ablation_metrics(args.run_dir)
    elif args.action == "anonymise":
        result = anonymise_ablation(args.run_dir, seed=args.seed)
    elif args.action == "estimate":
        result = estimate_ablation_cost(args.run_dir)
    else:
        build_ablation_plan(args.manifest, args.prepared, args.run_dir)
        run_ablation_samples(args.run_dir, backend="mock")
        compute_ablation_metrics(args.run_dir)
        anonymise_ablation(args.run_dir, seed=args.seed)
        result = estimate_ablation_cost(args.run_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
