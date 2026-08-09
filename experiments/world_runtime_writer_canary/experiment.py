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
from app.utils.llm_client import estimate_messages_tokens, get_llm_client
from app.writing.world_runtime_bakery_gold import (
    build_saturday_bakery_gold_fixture,
)
from app.writing.world_runtime_compiler import WorldRuntimeCompiler
from app.writing.world_runtime_kernel import build_minimal_universal_kernel
from app.writing.world_runtime_pack_modern_urban import (
    build_modern_urban_cn_2020s_candidate_pack,
)
from app.writing.world_runtime_prompt import WorldRuntimePromptController
from app.writing.world_runtime_resolver import WorldRuntimeResolver


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "experiments/world_runtime_writer_canary/fixtures/canary_v1.json"
DEFAULT_OUTPUT = ROOT / ".world_runtime_writer_canary_v2_runtime"
SYSTEM_PROMPT = (
    "你是一名中文小说作者。根据材料写一个完整的小节，只输出小说正文，不输出标题、"
    "分析、规则、字段名、检查清单或写作说明。目标为500—1100个可见字符。必须保留"
    "所有指定动作，并用自然过程连接；不要照抄输入句子。"
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


def _runtime():
    fixture = build_saturday_bakery_gold_fixture()
    resolved = WorldRuntimeResolver().resolve(
        constitution=fixture.constitution,
        candidate_packs=(build_modern_urban_cn_2020s_candidate_pack(),),
        kernel=build_minimal_universal_kernel(),
    )
    frame = WorldRuntimeCompiler().compile(
        resolved=resolved,
        state_before=fixture.state_before,
        event_contract=fixture.event_contract,
    )
    if frame.status != "complete":
        raise ValueError("WR1 canary requires a complete frozen runtime frame")
    return resolved, frame


def _baseline_messages(scene: dict[str, Any]) -> list[dict[str, str]]:
    content = (
        f"场景：{scene['premise']}\n"
        f"人物：{scene['characters']}\n"
        "内容硬边界：四个必写动作都必须实际发生；不能为了避开现实问题删除、略过或"
        "改写成未发生。只写本小节，不增加新人物。"
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": content},
    ]


def build(output_dir: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    fixture = _read(FIXTURE)
    if (output_dir / "attempt-ledger.json").exists():
        raise FileExistsError("attempt ledger exists; refusing to rebuild")
    if len(fixture["scenes"]) != 2:
        raise ValueError("WR1 v1 requires exactly two scenes")
    resolved, frame = _runtime()
    samples = []
    ordinal = 0
    for scene_index, scene in enumerate(fixture["scenes"], 1):
        baseline = _baseline_messages(scene)
        common_hash = _digest(
            {"messages": baseline, "scene_id": scene["scene_id"], "provider": fixture["provider"]}
        )
        for repeat in (1, 2):
            arms = ["A", "B"]
            random.Random(20260803 + scene_index * 10 + repeat).shuffle(arms)
            for arm in arms:
                ordinal += 1
                sample_id = f"WR1-{ordinal:02d}"
                task_id = f"world-runtime-wr1:{sample_id}"
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
                if arm == "A" and applied.observation.injected:
                    raise ValueError("baseline arm must not inject runtime")
                if arm == "B" and not applied.observation.injected:
                    raise ValueError("runtime arm must inject runtime")
                messages = list(applied.messages)
                samples.append(
                    {
                        "sample_id": sample_id,
                        "ordinal": ordinal,
                        "scene_id": scene["scene_id"],
                        "repeat": repeat,
                        "arm": arm,
                        "task_id_hash": applied.observation.task_id_hash,
                        "messages": messages,
                        "provider": fixture["provider"],
                        "common_input_hash": common_hash,
                        "request_hash": _digest({"messages": messages, "provider": fixture["provider"]}),
                        "runtime_observation": applied.observation.model_dump(mode="json"),
                    }
                )
    if len(samples) != 8 or len({item["sample_id"] for item in samples}) != 8:
        raise ValueError("WR1 canary must contain eight unique samples")
    for scene_id in {item["scene_id"] for item in samples}:
        values = {item["common_input_hash"] for item in samples if item["scene_id"] == scene_id}
        if len(values) != 1:
            raise ValueError("A/B common input drift")
    manifest = {
        "schema_version": "world-runtime-writer-canary-manifest-v1",
        "experiment_id": fixture["experiment_id"],
        "fixture_hash": hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),
        "runtime_frame_hash": frame.frame_hash,
        "preregistered_gates": fixture["preregistered_gates"],
        "sample_count": len(samples),
        "scenes": 2,
        "repeats_per_arm": 2,
        "samples": samples,
        "production_behavior_changed": False,
        "silent_reruns_allowed": False,
    }
    ledger = {
        "schema_version": "world-runtime-writer-attempt-ledger-v1",
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


def audit(output_dir: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    manifest = _read(output_dir / "private/locked-manifest.json")
    by_arm = {"A": [], "B": []}
    common: dict[str, set[str]] = {}
    for item in manifest["samples"]:
        by_arm[item["arm"]].append(estimate_messages_tokens(item["messages"]))
        common.setdefault(item["scene_id"], set()).add(item["common_input_hash"])
    result = {
        "schema_version": "world-runtime-writer-pre-generation-audit-v1",
        "sample_count": len(manifest["samples"]),
        "estimated_input_tokens_by_arm": {key: sum(value) for key, value in by_arm.items()},
        "runtime_prompt_token_delta_mean": round(
            (sum(by_arm["B"]) - sum(by_arm["A"])) / len(by_arm["B"]), 2
        ),
        "provider_calls_planned": len(manifest["samples"]),
        "transport_retries": 0,
        "common_input_invariant": all(len(value) == 1 for value in common.values()),
        "gates_frozen_before_generation": True,
        "api_key_configured": bool(settings.LLM_API_KEY),
        "provider_host": urlparse(settings.LLM_BASE_URL).hostname,
        "model": settings.WRITER_LLM_MODEL,
        "production_default": settings.WRITER_WORLD_RUNTIME_MODE,
        "status": "ready" if settings.LLM_API_KEY else "blocked_missing_api_key",
    }
    if (
        result["runtime_prompt_token_delta_mean"]
        > manifest["preregistered_gates"]["maximum_runtime_prompt_token_delta_mean"]
    ):
        result["status"] = "blocked_runtime_prompt_over_budget"
    if result["production_default"] != "off":
        result["status"] = "blocked_production_default_not_off"
    _write(output_dir / "pre-generation-audit.json", result)
    return result


def run(output_dir: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    manifest = _read(output_dir / "private/locked-manifest.json")
    audit_result = audit(output_dir)
    if audit_result["status"] != "ready":
        raise RuntimeError(audit_result["status"])
    ledger_path = output_dir / "attempt-ledger.json"
    ledger = _read(ledger_path)
    client = get_llm_client(settings.WRITER_LLM_MODEL)
    for sample in manifest["samples"]:
        entry = ledger["samples"][sample["sample_id"]]
        if entry["status"] != "pending" or entry["attempt_count"] != 0:
            raise RuntimeError(f"refusing silent rerun for {sample['sample_id']}")
        entry["attempt_count"] = 1
        entry["status"] = "started"
        _write(ledger_path, ledger)
        metadata: dict[str, Any] = {}
        try:
            text = client.chat_completion(
                sample["messages"],
                temperature=sample["provider"]["temperature"],
                max_tokens=sample["provider"]["max_tokens"],
                max_retries=sample["provider"]["max_retries"],
                prompt_name="world_runtime_writer_canary_v1",
                completion_metadata_sink=metadata.update,
            )
            output_path = output_dir / "private/outputs" / f"{sample['sample_id']}.txt"
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(text, encoding="utf-8", newline="\n")
            entry.update(
                status="succeeded",
                output_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                completion_metadata=metadata,
            )
        except Exception as exc:
            entry.update(status="failed", error_type=type(exc).__name__, error=str(exc)[:500])
            _write(ledger_path, ledger)
            raise
        _write(ledger_path, ledger)
    return {"status": "complete", "attempted": len(manifest["samples"]), "retries": 0}


def _visible(text: str) -> int:
    return len(re.sub(r"\s+", "", text))


def _unnegated(text: str, pattern: str) -> bool:
    for match in re.finditer(pattern, text):
        prefix = text[max(0, match.start() - 5):match.start()]
        if not re.search(r"(?:没|没有|未|不|尚未)$", prefix):
            return True
    return False


def _checks(text: str, scene: dict[str, Any]) -> dict[str, Any]:
    groups = [
        {"terms": terms, "passed": any(term in text for term in terms)}
        for terms in scene["required_term_groups"]
    ]
    publish_positions = [text.find(term) for term in ("发布", "上线", "审核通过") if term in text]
    first_publish = min(publish_positions) if publish_positions else len(text) + 1
    comment_before_publish = "评论" in text[:first_publish]
    public_open = any(
        _unnegated(text, pattern)
        for pattern in (
            r"开门营业", r"开始营业", r"店面开门", r"店门打开",
            r"顾客.{0,4}(?:进店|走进)", r"招呼顾客",
        )
    )
    termination = any(term in text for term in ("辞职生效", "离职生效", "劳动关系终止", "已经离职"))
    acknowledgement = any(term in text for term in ("人事确认", "公司确认", "批准辞职", "生效日期"))
    coworker_path_missing = any(term in text for term in ("内网", "同事评论", "同事们都", "同事已经"))
    violations = {
        "storefront_open_before_0600": public_open,
        "public_comment_before_publication": comment_before_publish,
        "employment_ended_without_effective_transition": termination and not acknowledgement,
        "coworker_knows_without_transmission_path": coworker_path_missing,
    }
    unsourced = [
        term for term in ("临时营业", "提前营业", "公司批准", "辞职生效", "内网", "同事评论")
        if term in text
    ]
    leakage_terms = (
        "状态锚点", "因果边界", "合法状态路径", "事件保留边界",
        "semantic_key", "state_revision", "event:publish-article",
    )
    return {
        "visible_characters": _visible(text),
        "within_length_band": 500 <= _visible(text) <= 1100,
        "required_term_groups": groups,
        "must_event_proxy_pass": all(item["passed"] for item in groups),
        "hard_reality_violations": violations,
        "hard_reality_violation_count": sum(violations.values()),
        "unsourced_setting_terms": unsourced,
        "unsourced_setting_proxy_pass": not unsourced,
        "field_leakage_detected": any(term in text for term in leakage_terms),
    }


def evaluate(output_dir: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    fixture = _read(FIXTURE)
    scenes = {item["scene_id"]: item for item in fixture["scenes"]}
    manifest = _read(output_dir / "private/locked-manifest.json")
    ledger = _read(output_dir / "attempt-ledger.json")
    items = []
    for sample in manifest["samples"]:
        entry = ledger["samples"][sample["sample_id"]]
        if entry["status"] != "succeeded":
            raise RuntimeError("all frozen samples must succeed before evaluation")
        text = (output_dir / "private/outputs" / f"{sample['sample_id']}.txt").read_text(encoding="utf-8")
        items.append({
            "sample_id": sample["sample_id"],
            "scene_id": sample["scene_id"],
            "repeat": sample["repeat"],
            "arm": sample["arm"],
            "checks": _checks(text, scenes[sample["scene_id"]]),
        })
    aggregate = {}
    for arm in ("A", "B"):
        values = [item["checks"] for item in items if item["arm"] == arm]
        aggregate[arm] = {
            "samples": len(values),
            "must_event_proxy_passes": sum(item["must_event_proxy_pass"] for item in values),
            "hard_reality_violation_count": sum(item["hard_reality_violation_count"] for item in values),
            "unsourced_setting_proxy_passes": sum(item["unsourced_setting_proxy_pass"] for item in values),
            "length_band_passes": sum(item["within_length_band"] for item in values),
            "field_leakage_count": sum(item["field_leakage_detected"] for item in values),
        }
    gates = {
        "sample_size_sufficient": len(items) >= fixture["preregistered_gates"]["minimum_samples_for_promotion"],
        "must_event_retention_non_inferior": aggregate["B"]["must_event_proxy_passes"] >= aggregate["A"]["must_event_proxy_passes"],
        "hard_reality_violation_rate_lower": aggregate["B"]["hard_reality_violation_count"] < aggregate["A"]["hard_reality_violation_count"],
        "unsourced_setting_rate_non_inferior": aggregate["B"]["unsourced_setting_proxy_passes"] >= aggregate["A"]["unsourced_setting_proxy_passes"],
        "field_leakage_forbidden": aggregate["B"]["field_leakage_count"] == 0,
        "human_prose_review_complete": False,
        "runtime_prompt_token_budget_pass": (
            _read(output_dir / "pre-generation-audit.json")["runtime_prompt_token_delta_mean"]
            <= fixture["preregistered_gates"]["maximum_runtime_prompt_token_delta_mean"]
        ),
    }
    result = {
        "schema_version": "world-runtime-writer-canary-evaluation-v1",
        "items": items,
        "aggregate": aggregate,
        "gates": gates,
        "promotion_eligible": all(gates.values()),
        "decision": "hold_insufficient_sample_and_human_review",
        "machine_metrics_are_proxies": True,
    }
    _write(output_dir / "evaluation.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("build", "audit", "run", "evaluate"))
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = globals()[args.command](args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
