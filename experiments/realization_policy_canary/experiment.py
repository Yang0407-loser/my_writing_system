from __future__ import annotations

import hashlib
import json
import random
import re
import secrets
import time
from pathlib import Path
from typing import Any

from app.config import settings
from app.realization_policy import compile_realization_policy, render_realization_policy
from app.style_evaluation import evaluate_style_drift
from app.utils.llm_client import get_llm_client
from app.utils.llm_client import estimate_messages_tokens
from app.utils.style_brief import StyleSummarizer
from experiments.style_control.metrics import overlap_metrics


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "experiments/realization_policy_canary/fixtures/canary_v1.json"
DEFAULT_OUTPUT = ROOT / "outputs/realization-policy-canary-v1"
SYSTEM_PROMPT = """你是一名中文小说作者。请根据材料写一段完整的小说正文。
只输出正文，不输出标题、分析、提纲、规则、字段名、检查清单或写作说明。
正文目标为900—1150个汉字，使用第三人称近距离叙述。
必须守住内容边界，但不要让人物朗读规则，不要照抄输入句子。"""


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
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _style_guidance(scene: dict[str, Any], arm: str) -> str:
    baseline = StyleSummarizer.for_writer(scene["style"])
    if arm == "A":
        return baseline
    policy = render_realization_policy(
        compile_realization_policy(scene["style"])
    )
    kernel = json.dumps(scene["kernel"], ensure_ascii=False, indent=2)
    return (
        f"{baseline}\n\n{policy}\n\n"
        "以下 Sparse Decision Kernel 只规定结果边界和叙事压力，不是段落顺序，"
        "不必逐项展示，也不要复述字段名：\n"
        f"{kernel}"
    )


def _messages(scene: dict[str, Any], arm: str) -> list[dict[str, str]]:
    common = {
        "场景": scene["premise"],
        "人物": scene["characters"],
        "内容硬边界": scene["content_boundaries"],
        "目标篇幅": "900—1150个汉字",
    }
    user = (
        json.dumps(common, ensure_ascii=False, indent=2)
        + "\n\n## 风格与实现指引\n"
        + _style_guidance(scene, arm)
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def build(output_dir: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    fixture = load_json(FIXTURE)
    if len(fixture["scenes"]) != 4:
        raise ValueError("exactly four scenes are required")
    if (output_dir / "attempt-ledger.json").exists():
        raise FileExistsError("attempt ledger exists; refusing to rebuild")

    samples = []
    ordinal = 0
    for scene_index, scene in enumerate(fixture["scenes"], 1):
        common_hash = digest(
            {
                "system": SYSTEM_PROMPT,
                "scene": {
                    key: value
                    for key, value in scene.items()
                    if key not in {"kernel"}
                },
                "provider": fixture["provider"],
            }
        )
        for repeat in (1, 2):
            arms = ["A", "B"]
            random.Random(20260731 + scene_index * 10 + repeat).shuffle(arms)
            for arm in arms:
                ordinal += 1
                messages = _messages(scene, arm)
                sample_id = f"RP-{ordinal:02d}"
                samples.append(
                    {
                        "sample_id": sample_id,
                        "ordinal": ordinal,
                        "scene_id": scene["scene_id"],
                        "scene_type": scene["scene_type"],
                        "repeat": repeat,
                        "arm": arm,
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
    if len(samples) != 16 or len({item["sample_id"] for item in samples}) != 16:
        raise ValueError("canary must contain 16 unique samples")
    for scene_id in {item["scene_id"] for item in samples}:
        if len({item["common_input_hash"] for item in samples if item["scene_id"] == scene_id}) != 1:
            raise ValueError("A/B common input drift")

    manifest = {
        "schema_version": "realization-policy-canary-manifest-v1",
        "experiment_id": fixture["experiment_id"],
        "fixture_hash": hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),
        "preregistered_gates": fixture["preregistered_gates"],
        "sample_count": 16,
        "scenes": 4,
        "repeats_per_arm": 2,
        "samples": samples,
        "production_behavior_changed": False,
        "silent_reruns_allowed": False,
    }
    ledger = {
        "schema_version": "realization-policy-attempt-ledger-v1",
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
    write_json(
        output_dir / "preflight.json",
        {
            "status": "passed",
            "sample_count": 16,
            "common_input_hashes_per_scene": 1,
            "request_hashes_unique_by_arm": True,
            "gate_hash": digest(fixture["preregistered_gates"]),
            "llm_calls": 0,
        },
    )
    return manifest


def audit(output_dir: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    manifest = load_json(output_dir / "private/locked-manifest.json")
    by_arm: dict[str, list[int]] = {"A": [], "B": []}
    invariant = {}
    for sample in manifest["samples"]:
        by_arm[sample["arm"]].append(
            estimate_messages_tokens(sample["messages"])
        )
        invariant.setdefault(sample["scene_id"], set()).add(
            sample["common_input_hash"]
        )
    result = {
        "schema_version": "realization-policy-pre-generation-audit-v1",
        "sample_count": len(manifest["samples"]),
        "estimated_input_tokens_by_arm": {
            arm: sum(values) for arm, values in by_arm.items()
        },
        "estimated_input_tokens_total": sum(
            sum(values) for values in by_arm.values()
        ),
        "max_output_tokens_total": sum(
            item["provider"]["max_tokens"] for item in manifest["samples"]
        ),
        "common_input_invariant": all(
            len(values) == 1 for values in invariant.values()
        ),
        "scenes": len(invariant),
        "repeats_per_arm": 2,
        "provider_calls_planned": 16,
        "transport_retries": 0,
        "silent_reruns": 0,
        "gates_frozen_before_generation": True,
        "status": "ready",
    }
    write_json(output_dir / "pre-generation-audit.json", result)
    return result


def _visible_characters(text: str) -> int:
    return len(re.sub(r"\s+", "", text))


def _basic_checks(
    text: str,
    scene: dict[str, Any],
    finish_reason: str | None,
    prompt_text: str,
) -> dict[str, Any]:
    required = [
        {
            "terms": group,
            "passed": any(term in text for term in group),
        }
        for group in scene["required_term_groups"]
    ]
    forbidden = [term for term in scene["forbidden_terms"] if term in text]
    overlap = overlap_metrics(text, prompt_text)
    visible = _visible_characters(text)
    leakage = any(
        value in text
        for value in (
            "irreversible_micro_choice",
            "must_remain_unsaid",
            "relationship_pressure",
            "forbidden_shortcut",
            "ending_residue",
            "Sparse Decision Kernel",
            "叙述姿态",
        )
    )
    return {
        "nonempty": bool(text.strip()),
        "visible_characters": visible,
        "within_preregistered_band": 750 <= visible <= 1350,
        "finish_reason": finish_reason,
        "truncated": finish_reason not in (None, "stop"),
        "required_term_groups": required,
        "all_required_term_groups_pass": all(item["passed"] for item in required),
        "forbidden_terms_found": forbidden,
        "unauthorized_content_proxy_pass": not forbidden,
        "field_leakage_detected": leakage,
        "exact_copied_sentence_count": overlap["exact_copied_sentence_count"],
        "longest_common_contiguous_chars": overlap[
            "longest_common_contiguous_chars"
        ],
    }


def _validate_runtime(manifest: dict[str, Any]) -> None:
    provider = manifest["samples"][0]["provider"]
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
    try:
        items = load_json(path)
        if isinstance(items, list):
            return {item["sample_id"]: item for item in items}
    except (json.JSONDecodeError, KeyError):
        return {}
    return {}


def execute(output_dir: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    manifest = load_json(output_dir / "private/locked-manifest.json")
    ledger_path = output_dir / "attempt-ledger.json"
    ledger = load_json(ledger_path)
    client = None
    fixture = load_json(FIXTURE)
    scenes = {item["scene_id"]: item for item in fixture["scenes"]}
    prev_receipts = _existing_receipts(output_dir)
    receipts: list[dict[str, Any]] = []
    already_terminal = 0
    newly_attempted = 0
    for sample in sorted(manifest["samples"], key=lambda item: item["ordinal"]):
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
            receipt = prev_receipts.get(
                sample_id,
                {
                    "sample_id": sample_id,
                    "status": f"already_{state['status']}",
                },
            )
            receipts.append(receipt)
            already_terminal += 1
            print(
                json.dumps(
                    {
                        "sample_id": sample_id,
                        "scene_id": sample.get("scene_id", "?"),
                        "arm": sample.get("arm", "?"),
                        "status": f"skipped (already_{state['status']})",
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            continue
        else:
            raise RuntimeError(
                f"unexpected ledger state for {sample_id}: {state}"
            )
        if client is None:
            _validate_runtime(manifest)
            client = get_llm_client()
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
                prompt_name="realization_policy_canary_v1",
                completion_metadata_sink=metadata.update,
            )
            scene = scenes[sample["scene_id"]]
            prompt_text = "\n".join(item["content"] for item in sample["messages"])
            record = {
                "schema_version": "realization-policy-canary-text-v1",
                "sample_id": sample_id,
                "scene_id": sample["scene_id"],
                "repeat": sample["repeat"],
                "arm": sample["arm"],
                "text": text,
                "text_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "metadata": metadata
                | {"wall_seconds": round(time.perf_counter() - started, 3)},
                "checks": _basic_checks(
                    text, scene, metadata.get("finish_reason"), prompt_text
                ),
                "style_evaluation": evaluate_style_drift(
                    text, scene["style"]
                ),
            }
            write_json(
                output_dir / f"private/texts/{sample_id}.json",
                record,
            )
            state["status"] = "succeeded"
            receipt = {
                "sample_id": sample_id,
                "status": "succeeded",
                "text_hash": record["text_hash"],
                "finish_reason": metadata.get("finish_reason"),
                "input_tokens": metadata.get("input_tokens"),
                "output_tokens": metadata.get("output_tokens"),
            }
        except Exception as error:
            state["status"] = "failed"
            state["error_type"] = type(error).__name__
            state["error_hash"] = hashlib.sha256(
                str(error).encode("utf-8")
            ).hexdigest()
            receipt = {
                "sample_id": sample_id,
                "status": "failed",
                "error_type": type(error).__name__,
                "error_hash": state["error_hash"],
            }
        write_json(ledger_path, ledger)
        receipts.append(receipt)
        write_json(output_dir / "private/receipts.json", receipts)
        print(
            json.dumps(
                {
                    "sample_id": sample_id,
                    "scene_id": sample["scene_id"],
                    "arm": sample["arm"],
                    "status": receipt["status"],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    succeeded = sum(item["status"] == "succeeded" for item in receipts)
    failed = sum(item["status"] == "failed" for item in receipts)
    summary = {
        "schema_version": "realization-policy-canary-run-summary-v1",
        "requested": len(manifest["samples"]),
        "already_terminal": already_terminal,
        "newly_attempted": newly_attempted,
        "succeeded": succeeded,
        "failed": failed,
        "pending": len(manifest["samples"]) - already_terminal - newly_attempted,
        "transport_retries": 0,
        "silent_reruns": 0,
    }
    write_json(output_dir / "run-summary.json", summary)
    return summary


def _paragraphs(text: str) -> list[dict[str, str]]:
    parts = [item.strip() for item in re.split(r"\n+", text) if item.strip()]
    if len(parts) < 3:
        sentences = [
            item.strip()
            for item in re.findall(r"[^。！？]+[。！？]?", text)
            if item.strip()
        ]
        size = max(1, (len(sentences) + 5) // 6)
        parts = [
            "".join(sentences[index:index + size])
            for index in range(0, len(sentences), size)
        ]
    return [
        {"paragraph_id": f"P{index:02d}", "text": value}
        for index, value in enumerate(parts, 1)
    ]


def build_public(output_dir: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    summary = load_json(output_dir / "run-summary.json")
    if summary["succeeded"] != 16 or summary["failed"]:
        raise ValueError("all 16 generations must succeed")
    manifest = load_json(output_dir / "private/locked-manifest.json")
    fixture = load_json(FIXTURE)
    rng = random.Random(secrets.randbits(128))
    public_ids = [f"Q{index:02d}" for index in range(1, 17)]
    rng.shuffle(public_ids)
    key = []
    blocks = []
    for scene in fixture["scenes"]:
        for repeat in (1, 2):
            candidates = []
            group = [
                item
                for item in manifest["samples"]
                if item["scene_id"] == scene["scene_id"]
                and item["repeat"] == repeat
            ]
            for sample in group:
                record = load_json(
                    output_dir / f"private/texts/{sample['sample_id']}.json"
                )
                public_id = public_ids.pop()
                key.append(
                    {
                        "public_text_id": public_id,
                        "sample_id": sample["sample_id"],
                        "scene_id": sample["scene_id"],
                        "repeat": repeat,
                        "arm": sample["arm"],
                        "text_hash": record["text_hash"],
                    }
                )
                candidates.append(
                    {
                        "public_text_id": public_id,
                        "text_hash": record["text_hash"],
                        "paragraphs": _paragraphs(record["text"]),
                    }
                )
            rng.shuffle(candidates)
            blocks.append(
                {
                    "public_block_id": f"QB-{len(blocks) + 1:02d}",
                    "scene_id": scene["scene_id"],
                    "scene_type": scene["scene_type"],
                    "content_boundaries": scene["content_boundaries"],
                    "candidates": candidates,
                }
            )
    public = {
        "schema_version": "realization-policy-public-material-v1",
        "experiment_id": fixture["experiment_id"],
        "reviewer_notice": "只阅读匿名正文和内容边界；不要推测A/B身份。",
        "blocks": blocks,
    }
    write_json(output_dir / "private/blind-key.json", {"entries": key})
    write_json(output_dir / "public/blind-review-material.json", public)
    return {"blocks": len(blocks), "texts": len(key)}


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("build", "audit", "run", "public"))
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = (
        build(args.output)
        if args.action == "build"
        else audit(args.output)
        if args.action == "audit"
        else execute(args.output)
        if args.action == "run"
        else build_public(args.output)
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
