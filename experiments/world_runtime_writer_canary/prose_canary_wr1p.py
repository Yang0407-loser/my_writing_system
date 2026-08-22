"""WR1-P full-prose World Runtime canary preflight.

The module intentionally has no provider ``run`` command.  It can build and
audit a frozen eight-request ledger, and later evaluate externally supplied
outputs with the already frozen WR1-E2 evaluator V2.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from app.config import settings
from app.utils.llm_client import estimate_messages_tokens
from app.writing.world_runtime_compiler import WorldRuntimeCompiler
from app.writing.world_runtime_prompt import WorldRuntimePromptController
from experiments.world_runtime_writer_canary import adversarial_experiment as wr1r
from experiments.world_runtime_writer_canary import evaluator_v2_wr1e as evaluator_v2


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "experiments/world_runtime_writer_canary/fixtures/canary_wr1p_v1.json"
DEFAULT_OUTPUT = ROOT / ".world_runtime_wr1p_canary_runtime"
V2_MANIFEST = ROOT / "experiments/world_runtime_writer_canary/fixtures/wr1e_evaluator_v2_freeze_manifest.json"
SOURCE = Path(__file__).resolve()
SYSTEM_PROMPT = (
    "你是一名中文小说作者。根据材料续写一个完整小节，只输出小说正文，不输出标题、"
    "分析、规则、字段名、检查清单或说明。目标700—1200个可见字符。必须让场景要求"
    "在正文中形成明确结果，但不要用流程清单、状态播报或总结句代替叙事过程。"
)


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _digest(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _assert_v2_frozen() -> dict[str, Any]:
    manifest = _read(V2_MANIFEST)
    checks = {
        "evaluator_source_sha256": evaluator_v2.__file__,
        "calibration_fixture_sha256": evaluator_v2.CALIBRATION,
        "holdout_fixture_sha256": evaluator_v2.HOLDOUT,
        "calibration_report_sha256": ROOT / "reports/world-runtime-wr1e-evaluator-v2-calibration-2026-08-04.json",
        "holdout_report_sha256": ROOT / "reports/world-runtime-wr1e-evaluator-v2-holdout-2026-08-04.json",
    }
    for field, path in checks.items():
        if _sha256(Path(path)) != manifest[field]:
            raise RuntimeError(f"wr1p_v2_freeze_drift:{field}")
    if manifest["generation_authorized"] or manifest["production_authorized"]:
        raise RuntimeError("wr1p_invalid_v2_authorization_state")
    return manifest


def _baseline_messages(scene: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"场景：{scene['public_premise']}\n"
                f"人物：{scene['characters']}\n"
                "只写当前小节，不新增人物姓名、持续关系、项目背景、既往事件或世界规则。"
            ),
        },
    ]


def _evaluation_contract(fixture: dict[str, Any]) -> dict[str, Any]:
    return {
        "evaluator_schema": "world-runtime-evaluator-v2-wr1e",
        "evaluator_source_sha256": _sha256(Path(evaluator_v2.__file__)),
        "gates": fixture["preregistered_gates"],
        "scenes": [
            {
                "scene_id": scene["scene_id"],
                "required_event_id": scene["required_event_id"],
                "violation_checks": list(evaluator_v2.SCENE_CHECKS[scene["scene_id"]]),
            }
            for scene in fixture["scenes"]
        ],
    }


def build(output_dir: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    if (output_dir / "attempt-ledger.json").exists():
        raise FileExistsError("WR1-P attempt ledger exists; refusing rebuild")
    fixture = _read(FIXTURE)
    v2_manifest = _assert_v2_frozen()
    if len(fixture["scenes"]) != 4:
        raise ValueError("WR1-P requires exactly four scenes")
    _, states, resolved = wr1r._artifacts()
    samples = []
    ordinal = 0
    for index, scene in enumerate(fixture["scenes"], 1):
        state = states[scene["state_variant"]]
        event_scene = {
            **scene,
            "premise": scene["runtime_event_description"],
        }
        contract = wr1r._event_contract(event_scene, index)
        frame = WorldRuntimeCompiler().compile(
            resolved=resolved,
            state_before=state,
            event_contract=contract,
        )
        if frame.status != "complete":
            raise ValueError(f"WR1-P incomplete runtime frame: {scene['scene_id']}")
        baseline = _baseline_messages(scene)
        common_hash = _digest({"scene_id": scene["scene_id"], "messages": baseline})
        arms = ["A", "B"]
        random.Random(20260805 + index).shuffle(arms)
        for arm in arms:
            ordinal += 1
            sample_id = f"WR1P-{ordinal:02d}"
            task_id = f"world-runtime-wr1p:{sample_id}"
            controller = WorldRuntimePromptController(
                mode="shadow" if arm == "A" else "canary",
                canary_task_ids={task_id},
            )
            applied = controller.apply(
                baseline,
                task_id=task_id,
                frame=frame,
                resolved=resolved,
            )
            if applied.observation.injected != (arm == "B"):
                raise ValueError("WR1-P arm injection mismatch")
            messages = list(applied.messages)
            samples.append(
                {
                    "sample_id": sample_id,
                    "ordinal": ordinal,
                    "scene_id": scene["scene_id"],
                    "arm": arm,
                    "messages": messages,
                    "provider": fixture["provider"],
                    "common_input_hash": common_hash,
                    "request_hash": _digest({"messages": messages, "provider": fixture["provider"]}),
                    "frame_hash": frame.frame_hash,
                    "event_contract_hash": contract.artifact_hash,
                    "runtime_observation": applied.observation.model_dump(mode="json"),
                }
            )
    if len(samples) != fixture["preregistered_gates"]["sample_count"]:
        raise ValueError("WR1-P sample count mismatch")
    for scene in fixture["scenes"]:
        pair = [item for item in samples if item["scene_id"] == scene["scene_id"]]
        if len(pair) != 2 or {item["arm"] for item in pair} != {"A", "B"}:
            raise ValueError("WR1-P requires one A/B pair per scene")
        if len({item["common_input_hash"] for item in pair}) != 1:
            raise ValueError("WR1-P common input drift")
    manifest = {
        "schema_version": "world-runtime-writer-prose-canary-manifest-v1",
        "experiment_id": fixture["experiment_id"],
        "fixture_sha256": _sha256(FIXTURE),
        "runner_source_sha256": _sha256(SOURCE),
        "evaluator_source_sha256": _sha256(Path(evaluator_v2.__file__)),
        "evaluator_freeze_manifest_sha256": _sha256(V2_MANIFEST),
        "evaluation_contract_hash": _digest(_evaluation_contract(fixture)),
        "preregistered_gates": fixture["preregistered_gates"],
        "samples": samples,
        "sample_count": len(samples),
        "scene_count": len(fixture["scenes"]),
        "external_generation_authorized": False,
        "single_owner_review_only": True,
        "production_behavior_changed": False,
        "v2_holdout_blind_to_implementer": v2_manifest["holdout_blind_to_implementer"],
    }
    ledger = {
        "schema_version": "world-runtime-writer-prose-attempt-ledger-v1",
        "samples": {
            item["sample_id"]: {
                "request_hash": item["request_hash"],
                "status": "pending",
                "attempt_count": 0,
            }
            for item in samples
        },
    }
    _write(output_dir / "private/locked-manifest.json", manifest)
    _write(output_dir / "attempt-ledger.json", ledger)
    return manifest


def _assert_integrity(output_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    fixture = _read(FIXTURE)
    manifest = _read(output_dir / "private/locked-manifest.json")
    _assert_v2_frozen()
    checks = {
        "fixture_sha256": _sha256(FIXTURE),
        "runner_source_sha256": _sha256(SOURCE),
        "evaluator_source_sha256": _sha256(Path(evaluator_v2.__file__)),
        "evaluator_freeze_manifest_sha256": _sha256(V2_MANIFEST),
        "evaluation_contract_hash": _digest(_evaluation_contract(fixture)),
    }
    for field, actual in checks.items():
        if manifest[field] != actual:
            raise RuntimeError(f"wr1p_frozen_drift:{field}")
    return fixture, manifest


def audit(output_dir: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    fixture, manifest = _assert_integrity(output_dir)
    by_arm = {"A": [], "B": []}
    for sample in manifest["samples"]:
        by_arm[sample["arm"]].append(estimate_messages_tokens(sample["messages"]))
    ledger = _read(output_dir / "attempt-ledger.json")
    pending = sum(item["status"] == "pending" for item in ledger["samples"].values())
    attempts = sum(item["attempt_count"] for item in ledger["samples"].values())
    result = {
        "schema_version": "world-runtime-writer-prose-preflight-v1",
        "status": "ready_zero_call_external_generation_not_authorized",
        "sample_count": len(manifest["samples"]),
        "scene_count": manifest["scene_count"],
        "pending": pending,
        "attempt_count_total": attempts,
        "output_files": len(list((output_dir / "private/outputs").glob("*.txt"))) if (output_dir / "private/outputs").exists() else 0,
        "runtime_prompt_token_delta_mean": round((sum(by_arm["B"]) - sum(by_arm["A"])) / len(by_arm["B"]), 2),
        "paired_common_input_invariant": all(
            len({item["common_input_hash"] for item in manifest["samples"] if item["scene_id"] == scene["scene_id"]}) == 1
            for scene in fixture["scenes"]
        ),
        "frozen_integrity": True,
        "provider_host": urlparse(settings.LLM_BASE_URL).hostname,
        "model": settings.WRITER_LLM_MODEL,
        "production_default": settings.WRITER_WORLD_RUNTIME_MODE,
        "external_generation_authorized": manifest["external_generation_authorized"],
        "provider_calls_executed": 0,
    }
    maximum = fixture["preregistered_gates"]["maximum_runtime_prompt_token_delta_mean"]
    if result["runtime_prompt_token_delta_mean"] > maximum:
        result["status"] = "blocked_runtime_prompt_over_budget"
    if result["production_default"] != "off":
        result["status"] = "blocked_production_default_not_off"
    if not result["paired_common_input_invariant"]:
        result["status"] = "blocked_common_input_drift"
    if pending != len(manifest["samples"]) or attempts != 0 or result["output_files"] != 0:
        result["status"] = "blocked_nonzero_generation_state"
    _write(output_dir / "pre-generation-audit.json", result)
    return result


def _visible(text: str) -> int:
    return len(re.sub(r"\s+", "", text))


def evaluate(output_dir: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    fixture, manifest = _assert_integrity(output_dir)
    ledger = _read(output_dir / "attempt-ledger.json")
    items = []
    minimum, maximum = fixture["preregistered_gates"]["length_band"]
    for sample in manifest["samples"]:
        entry = ledger["samples"][sample["sample_id"]]
        if entry["status"] != "succeeded" or entry["attempt_count"] != 1:
            raise RuntimeError("WR1-P requires exactly one succeeded attempt per sample")
        output_path = output_dir / "private/outputs" / f"{sample['sample_id']}.txt"
        text = output_path.read_text(encoding="utf-8")
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if entry.get("output_sha256") != digest:
            raise RuntimeError(f"WR1-P output hash mismatch: {sample['sample_id']}")
        result = evaluator_v2.evaluate_text_v2(sample["scene_id"], text)
        items.append(
            {
                "sample_id": sample["sample_id"],
                "scene_id": sample["scene_id"],
                "arm": sample["arm"],
                "output_sha256": digest,
                "visible_characters": _visible(text),
                "within_length_band": minimum <= _visible(text) <= maximum,
                "v2": result.model_dump(mode="json"),
            }
        )
    aggregate = {}
    for arm in ("A", "B"):
        values = [item for item in items if item["arm"] == arm]
        aggregate[arm] = {
            "samples": len(values),
            "required_event_completed": sum(item["v2"]["required_event_completed"]["value"] for item in values),
            "scenes_with_hard_violation": sum(any(check["value"] for check in item["v2"]["hard_reality_violations"].values()) for item in values),
            "hard_violation_count": sum(sum(check["value"] for check in item["v2"]["hard_reality_violations"].values()) for item in values),
            "task_evasion_count": sum(item["v2"]["task_evasion"]["value"] for item in values),
            "unsourced_setting_count": sum(item["v2"]["unsourced_setting"]["value"] for item in values),
            "length_band_passes": sum(item["within_length_band"] for item in values),
        }
    gates = {
        "baseline_adversarial_activation": aggregate["A"]["scenes_with_hard_violation"] >= fixture["preregistered_gates"]["baseline_adversarial_activation_minimum_scenes"],
        "runtime_hard_violation_lower": aggregate["B"]["hard_violation_count"] < aggregate["A"]["hard_violation_count"],
        "event_completion_non_inferior": aggregate["B"]["required_event_completed"] >= aggregate["A"]["required_event_completed"],
        "task_evasion_non_inferior": aggregate["B"]["task_evasion_count"] <= aggregate["A"]["task_evasion_count"],
        "unsourced_setting_non_inferior": aggregate["B"]["unsourced_setting_count"] <= aggregate["A"]["unsourced_setting_count"],
        "length_non_inferior": aggregate["B"]["length_band_passes"] >= aggregate["A"]["length_band_passes"],
        "single_owner_prose_review_complete": False,
    }
    machine_pass = all(value for key, value in gates.items() if key != "single_owner_prose_review_complete")
    result = {
        "schema_version": "world-runtime-writer-prose-evaluation-v1",
        "items": items,
        "aggregate": aggregate,
        "gates": gates,
        "machine_gate_passed": machine_pass,
        "single_owner_review_required": machine_pass,
        "production_promotion_eligible": False,
        "real_task_canary_authorized": False,
        "decision": "machine_pass_pending_single_owner_review" if machine_pass else "hold_machine_gate_failed",
    }
    _write(output_dir / "evaluation-v2.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("build", "audit", "evaluate"))
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = globals()[args.command](args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
