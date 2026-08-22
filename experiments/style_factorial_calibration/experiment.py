from __future__ import annotations

import hashlib
import json
import random
import re
import statistics
import time
from itertools import combinations
from pathlib import Path
from typing import Any

from app.config import settings
from app.style_axes import (
    ANTI_AI_SURFACE_VERSION,
    POV_DISCLOSURE_VERSION,
    compile_anti_ai_surface,
    compile_pov_disclosure,
    render_style_axis,
)
from app.style_evaluation import evaluate_style_drift
from app.utils.llm_client import (
    estimate_messages_tokens,
    get_llm_client,
)
from app.utils.style_brief import StyleSummarizer
from experiments.style_control.metrics import overlap_metrics


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = (
    ROOT
    / "experiments/style_factorial_calibration/fixtures/calibration_v1.json"
)
DEFAULT_OUTPUT = ROOT / "outputs/style-factorial-calibration-v1"
ARMS = {
    "F00": {"language_surface": False, "pov_disclosure": False},
    "F10": {"language_surface": True, "pov_disclosure": False},
    "F01": {"language_surface": False, "pov_disclosure": True},
    "F11": {"language_surface": True, "pov_disclosure": True},
}
SYSTEM_PROMPT = """你是一名中文小说作者。请根据材料写一段完整的小说正文。
只输出正文，不输出标题、分析、提纲、规则、字段名、检查清单或写作说明。
正文控制在1100—1400个不计空白的汉字，使用第三人称近距离叙述。
写到眼前事件完成即可，不要为凑篇幅补充背景；必须守住内容边界。"""


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def digest(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _common_scene_contract(scene: dict[str, Any]) -> dict[str, Any]:
    return {
        "场景": scene["premise"],
        "视点人物": scene["focal_character"],
        "人物": scene["characters"],
        "此刻可观察事实": scene["observable_now"],
        "已知背景": scene["known_background"],
        "尚未确认且不得写成事实": scene["unknown_or_unconfirmed"],
        "必须发生的动作结果": scene["required_action_outcomes"],
        "禁止新增或解决": scene["forbidden_new_events"],
        "结尾客观状态": scene["final_objective_state"],
        "目标篇幅": "1100—1400个不计空白的汉字",
    }


def _factor_guidance(arm: str) -> str:
    factors = ARMS[arm]
    blocks: list[str] = []
    if factors["language_surface"]:
        blocks.append(
            render_style_axis(
                compile_anti_ai_surface(),
                heading="表达取舍",
            )
        )
    if factors["pov_disclosure"]:
        blocks.append(
            render_style_axis(
                compile_pov_disclosure(),
                heading="人物视点与披露",
            )
        )
    return "\n\n".join(blocks)


def _messages(scene: dict[str, Any], arm: str) -> list[dict[str, str]]:
    common = json.dumps(
        _common_scene_contract(scene),
        ensure_ascii=False,
        indent=2,
    )
    style = StyleSummarizer.for_writer(scene["style"])
    factor_guidance = _factor_guidance(arm)
    user = common + "\n\n## 风格\n" + style
    if factor_guidance:
        user += "\n\n" + factor_guidance
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def build(output_dir: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    fixture = load_json(FIXTURE)
    if (output_dir / "attempt-ledger.json").exists():
        raise FileExistsError(
            "attempt ledger exists; refusing to rebuild calibration"
        )
    scene = fixture["scene"]
    common_hash = digest(
        {
            "system": SYSTEM_PROMPT,
            "common_scene_contract": _common_scene_contract(scene),
            "style": scene["style"],
            "provider": fixture["provider"],
        }
    )
    ordered_arms = list(ARMS)
    random.Random(20260731).shuffle(ordered_arms)
    samples = []
    for ordinal, arm in enumerate(ordered_arms, 1):
        messages = _messages(scene, arm)
        sample_id = f"FX-CAL-{ordinal:02d}"
        samples.append(
            {
                "sample_id": sample_id,
                "ordinal": ordinal,
                "scene_id": scene["scene_id"],
                "arm": arm,
                "factors": ARMS[arm],
                "messages": messages,
                "provider": fixture["provider"],
                "common_input_hash": common_hash,
                "request_hash": digest(
                    {
                        "messages": messages,
                        "provider": fixture["provider"],
                    }
                ),
            }
        )
    if len(samples) != 4 or {item["arm"] for item in samples} != set(ARMS):
        raise ValueError("calibration must contain all four arms exactly once")

    manifest = {
        "schema_version": "style-factorial-calibration-manifest-v1",
        "experiment_id": fixture["experiment_id"],
        "fixture_hash": hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),
        "calibration_rules": fixture["calibration_rules"],
        "sample_count": 4,
        "scene_count": 1,
        "repeats_per_arm": 1,
        "samples": samples,
        "calibration_only": True,
        "excluded_from_formal_analysis": True,
        "production_behavior_changed": False,
        "silent_reruns_allowed": False,
    }
    ledger = {
        "schema_version": "style-factorial-calibration-ledger-v1",
        "samples": {
            item["sample_id"]: {
                "request_hash": item["request_hash"],
                "status": "pending",
                "attempt_count": 0,
            }
            for item in samples
        },
    }
    write_json(output_dir / "private/locked-manifest.json", manifest)
    write_json(output_dir / "attempt-ledger.json", ledger)
    return manifest


def audit(output_dir: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    manifest = load_json(output_dir / "private/locked-manifest.json")
    samples_by_arm = {item["arm"]: item for item in manifest["samples"]}
    token_counts = {
        arm: estimate_messages_tokens(item["messages"])
        for arm, item in samples_by_arm.items()
    }
    prompt_text = {
        arm: "\n".join(
            message["content"] for message in item["messages"]
        )
        for arm, item in samples_by_arm.items()
    }
    isolation = {
        "F00_has_language": "### 表达取舍" in prompt_text["F00"],
        "F00_has_pov": "### 人物视点与披露" in prompt_text["F00"],
        "F10_has_language": "### 表达取舍" in prompt_text["F10"],
        "F10_has_pov": "### 人物视点与披露" in prompt_text["F10"],
        "F01_has_language": "### 表达取舍" in prompt_text["F01"],
        "F01_has_pov": "### 人物视点与披露" in prompt_text["F01"],
        "F11_has_language": "### 表达取舍" in prompt_text["F11"],
        "F11_has_pov": "### 人物视点与披露" in prompt_text["F11"],
    }
    isolation_pass = isolation == {
        "F00_has_language": False,
        "F00_has_pov": False,
        "F10_has_language": True,
        "F10_has_pov": False,
        "F01_has_language": False,
        "F01_has_pov": True,
        "F11_has_language": True,
        "F11_has_pov": True,
    }
    common_hashes = {
        item["common_input_hash"] for item in manifest["samples"]
    }
    prompt_token_ratio = (
        max(token_counts.values()) / min(token_counts.values())
    )
    policy_sizes = {
        ANTI_AI_SURFACE_VERSION: len(compile_anti_ai_surface().guidance),
        POV_DISCLOSURE_VERSION: len(compile_pov_disclosure().guidance),
    }
    result = {
        "schema_version": "style-factorial-calibration-preflight-v1",
        "sample_count": len(manifest["samples"]),
        "estimated_input_tokens_by_arm": token_counts,
        "prompt_token_ratio_max_to_min": round(prompt_token_ratio, 4),
        "prompt_token_ratio_pass": prompt_token_ratio <= 1.25,
        "common_input_hash_count": len(common_hashes),
        "common_input_invariant": len(common_hashes) == 1,
        "factor_isolation": isolation,
        "factor_isolation_pass": isolation_pass,
        "policy_character_counts": policy_sizes,
        "policy_size_pass": all(value <= 180 for value in policy_sizes.values()),
        "provider_calls_planned": 4,
        "transport_retries": 0,
        "silent_reruns": 0,
        "gates_frozen_before_generation": True,
        "calibration_excluded_from_formal_analysis": True,
    }
    result["status"] = (
        "ready"
        if all(
            (
                result["prompt_token_ratio_pass"],
                result["common_input_invariant"],
                result["factor_isolation_pass"],
                result["policy_size_pass"],
            )
        )
        else "blocked"
    )
    write_json(output_dir / "preflight.json", result)
    return result


def _visible_characters(text: str) -> int:
    return len(re.sub(r"\s+", "", text))


def _basic_checks(
    text: str,
    scene: dict[str, Any],
    finish_reason: str | None,
    prompt_text: str,
) -> dict[str, Any]:
    visible = _visible_characters(text)
    overlap = overlap_metrics(text, prompt_text)
    required = [
        {
            "terms": group,
            "passed": any(term in text for term in group),
        }
        for group in scene["required_term_groups_diagnostic"]
    ]
    forbidden = [
        term
        for term in scene["forbidden_terms_diagnostic"]
        if term in text
    ]
    leakage_terms = (
        ANTI_AI_SURFACE_VERSION,
        POV_DISCLOSURE_VERSION,
        "language_surface",
        "pov_disclosure",
        "unknown_or_unconfirmed",
        "required_action_outcomes",
        "forbidden_new_events",
        "final_objective_state",
        "### 表达取舍",
        "### 人物视点与披露",
    )
    field_leakage = [term for term in leakage_terms if term in text]
    machine_hard_pass = bool(text.strip()) and all(
        (
            finish_reason in (None, "stop"),
            not field_leakage,
            overlap["exact_copied_sentence_count"] == 0,
        )
    )
    return {
        "machine_hard_pass": machine_hard_pass,
        "nonempty": bool(text.strip()),
        "visible_characters": visible,
        "within_requested_band": 1100 <= visible <= 1400,
        "within_gross_valid_band": 800 <= visible <= 2200,
        "finish_reason": finish_reason,
        "truncated": finish_reason not in (None, "stop"),
        "field_leakage_terms": field_leakage,
        "field_leakage_detected": bool(field_leakage),
        "exact_copied_sentence_count": overlap[
            "exact_copied_sentence_count"
        ],
        "longest_common_contiguous_chars": overlap[
            "longest_common_contiguous_chars"
        ],
        "required_term_groups_diagnostic": required,
        "required_term_groups_diagnostic_pass": all(
            item["passed"] for item in required
        ),
        "forbidden_terms_diagnostic_found": forbidden,
        "forbidden_terms_are_not_semantic_hard_gate": True,
    }


def _validate_runtime(manifest: dict[str, Any]) -> None:
    provider = manifest["samples"][0]["provider"]
    if not settings.LLM_API_KEY:
        raise ValueError("LLM credential unavailable")
    if settings.LLM_BASE_URL != provider["base_url"]:
        raise ValueError("LLM base URL differs from frozen contract")
    if settings.LLM_MODEL != provider["model"]:
        raise ValueError("LLM model differs from frozen contract")
    if provider["transport_max_retries"] != 0:
        raise ValueError("transport retries must be zero")


TERMINAL_STATUSES = frozenset({"succeeded", "failed", "attempted"})


def _existing_receipts(output_dir: Path) -> dict[str, dict[str, Any]]:
    path = output_dir / "private/receipts.json"
    if not path.exists():
        return {}
    items = load_json(path)
    return {
        item["sample_id"]: item
        for item in items
        if isinstance(item, dict) and "sample_id" in item
    }


def execute(output_dir: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    manifest = load_json(output_dir / "private/locked-manifest.json")
    preflight = load_json(output_dir / "preflight.json")
    if preflight.get("status") != "ready":
        raise ValueError("calibration preflight is not ready")
    _validate_runtime(manifest)
    fixture = load_json(FIXTURE)
    scene = fixture["scene"]
    ledger_path = output_dir / "attempt-ledger.json"
    ledger = load_json(ledger_path)
    client = get_llm_client()
    previous_receipts = _existing_receipts(output_dir)
    receipts: list[dict[str, Any]] = []
    already_terminal = 0
    newly_attempted = 0
    for sample in sorted(
        manifest["samples"],
        key=lambda value: value["ordinal"],
    ):
        sample_id = sample["sample_id"]
        state = ledger["samples"][sample_id]
        expected_pending = {
            "request_hash": sample["request_hash"],
            "status": "pending",
            "attempt_count": 0,
        }
        if state == expected_pending:
            pass
        elif state.get("status") in TERMINAL_STATUSES:
            receipts.append(
                previous_receipts.get(
                    sample_id,
                    {
                        "sample_id": sample_id,
                        "status": f"already_{state['status']}",
                    },
                )
            )
            already_terminal += 1
            continue
        else:
            raise RuntimeError(
                f"unexpected ledger state for {sample_id}: {state}"
            )

        state["status"] = "attempted"
        state["attempt_count"] = 1
        write_json(ledger_path, ledger)
        newly_attempted += 1
        metadata: dict[str, Any] = {}
        started = time.perf_counter()
        try:
            provider = sample["provider"]
            text = client.chat_completion(
                sample["messages"],
                temperature=provider["temperature"],
                max_tokens=provider["max_tokens"],
                max_retries=0,
                json_mode=False,
                prompt_name="style_factorial_calibration_v1",
                completion_metadata_sink=metadata.update,
            )
            prompt_text = "\n".join(
                item["content"] for item in sample["messages"]
            )
            checks = _basic_checks(
                text,
                scene,
                metadata.get("finish_reason"),
                prompt_text,
            )
            record = {
                "schema_version": "style-factorial-calibration-text-v1",
                "sample_id": sample_id,
                "scene_id": sample["scene_id"],
                "arm": sample["arm"],
                "factors": sample["factors"],
                "text": text,
                "text_hash": hashlib.sha256(
                    text.encode("utf-8")
                ).hexdigest(),
                "metadata": metadata
                | {"wall_seconds": round(time.perf_counter() - started, 3)},
                "checks": checks,
                "style_evaluation": evaluate_style_drift(
                    text,
                    scene["style"],
                ),
                "excluded_from_formal_analysis": True,
            }
            write_json(
                output_dir / f"private/texts/{sample_id}.json",
                record,
            )
            state["status"] = "succeeded"
            receipt = {
                "sample_id": sample_id,
                "arm": sample["arm"],
                "status": "succeeded",
                "text_hash": record["text_hash"],
                "finish_reason": metadata.get("finish_reason"),
                "input_tokens": metadata.get("input_tokens"),
                "output_tokens": metadata.get("output_tokens"),
                "visible_characters": checks["visible_characters"],
            }
        except Exception as error:
            state["status"] = "failed"
            state["error_type"] = type(error).__name__
            state["error_hash"] = hashlib.sha256(
                str(error).encode("utf-8")
            ).hexdigest()
            receipt = {
                "sample_id": sample_id,
                "arm": sample["arm"],
                "status": "failed",
                "error_type": type(error).__name__,
                "error_hash": state["error_hash"],
            }
        write_json(ledger_path, ledger)
        receipts.append(receipt)
        write_json(output_dir / "private/receipts.json", receipts)
        print(json.dumps(receipt, ensure_ascii=False), flush=True)

    succeeded = sum(item["status"] == "succeeded" for item in receipts)
    failed = sum(item["status"] == "failed" for item in receipts)
    summary = {
        "schema_version": "style-factorial-calibration-run-summary-v1",
        "requested": len(manifest["samples"]),
        "already_terminal": already_terminal,
        "newly_attempted": newly_attempted,
        "succeeded": succeeded,
        "failed": failed,
        "pending": len(manifest["samples"])
        - already_terminal
        - newly_attempted,
        "transport_retries": 0,
        "silent_reruns": 0,
        "excluded_from_formal_analysis": True,
    }
    write_json(output_dir / "run-summary.json", summary)
    return summary


def _sentence_set(text: str) -> set[str]:
    return {
        re.sub(r"\s+", "", item)
        for item in re.split(r"(?<=[。！？])", text)
        if len(re.sub(r"\s+", "", item)) >= 12
    }


def _formal_target_band(
    median_visible: float,
    rules: list[dict[str, Any]],
) -> list[int] | None:
    for rule in rules:
        if median_visible <= rule["median_visible_characters_lte"]:
            return rule["formal_target_visible_character_band"]
    return None


def report(output_dir: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    summary = load_json(output_dir / "run-summary.json")
    manifest = load_json(output_dir / "private/locked-manifest.json")
    rules = manifest["calibration_rules"]
    records = []
    for sample in manifest["samples"]:
        path = output_dir / f"private/texts/{sample['sample_id']}.json"
        if path.exists():
            records.append(load_json(path))

    by_arm = {
        item["arm"]: {
            "sample_id": item["sample_id"],
            "visible_characters": item["checks"]["visible_characters"],
            "within_requested_band": item["checks"][
                "within_requested_band"
            ],
            "within_gross_valid_band": item["checks"][
                "within_gross_valid_band"
            ],
            "machine_hard_pass": item["checks"]["machine_hard_pass"],
            "truncated": item["checks"]["truncated"],
            "field_leakage_detected": item["checks"][
                "field_leakage_detected"
            ],
            "required_term_proxy_pass": item["checks"][
                "required_term_groups_diagnostic_pass"
            ],
            "forbidden_term_proxy_hits": item["checks"][
                "forbidden_terms_diagnostic_found"
            ],
        }
        for item in records
    }
    lengths = [
        item["checks"]["visible_characters"] for item in records
    ]
    pairwise_exact_sentence_overlap = []
    for left, right in combinations(records, 2):
        overlap = sorted(
            _sentence_set(left["text"]) & _sentence_set(right["text"])
        )
        if overlap:
            pairwise_exact_sentence_overlap.append(
                {
                    "left_arm": left["arm"],
                    "right_arm": right["arm"],
                    "count": len(overlap),
                    "sentence_hashes": [
                        hashlib.sha256(value.encode("utf-8")).hexdigest()
                        for value in overlap
                    ],
                }
            )

    median_visible = statistics.median(lengths) if lengths else 0
    length_ratio = (
        max(lengths) / min(lengths) if len(lengths) == 4 and min(lengths) else None
    )
    hard_pass = (
        summary["succeeded"] == 4
        and len(records) == 4
        and all(item["checks"]["machine_hard_pass"] for item in records)
    )
    no_truncation = all(
        not item["checks"]["truncated"] for item in records
    )
    gross_length_pass = all(
        item["checks"]["within_gross_valid_band"] for item in records
    )
    length_balance_pass = (
        length_ratio is not None
        and length_ratio <= rules["maximum_arm_length_ratio"]
    )
    formal_ready = all(
        (hard_pass, no_truncation, gross_length_pass, length_balance_pass)
    )
    result = {
        "schema_version": "style-factorial-calibration-report-v1",
        "status": (
            "formal_preflight_recommended"
            if formal_ready
            else "formal_experiment_blocked"
        ),
        "calibration_only": True,
        "excluded_from_formal_analysis": True,
        "sample_count": len(records),
        "by_arm": by_arm,
        "length": {
            "median_visible_characters": median_visible,
            "minimum": min(lengths) if lengths else None,
            "maximum": max(lengths) if lengths else None,
            "max_to_min_ratio": (
                round(length_ratio, 4)
                if length_ratio is not None
                else None
            ),
            "balance_pass": length_balance_pass,
            "recommended_formal_target_visible_character_band": (
                _formal_target_band(
                    median_visible,
                    rules["formal_target_band_rule"],
                )
            ),
            "recommended_formal_max_tokens": (
                rules["formal_max_tokens_if_no_truncation"]
                if no_truncation
                else None
            ),
        },
        "machine_hard_pass": hard_pass,
        "no_truncation": no_truncation,
        "gross_length_pass": gross_length_pass,
        "pairwise_exact_sentence_overlap": pairwise_exact_sentence_overlap,
        "lexical_required_and_forbidden_checks_are_diagnostic_only": True,
        "human_quality_claim": None,
        "production_behavior_changed": False,
    }
    write_json(output_dir / "calibration-report.json", result)
    return result


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action",
        choices=("build", "audit", "run", "report"),
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = (
        build(args.output)
        if args.action == "build"
        else audit(args.output)
        if args.action == "audit"
        else execute(args.output)
        if args.action == "run"
        else report(args.output)
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
