"""WR1-R preregistered adversarial Writer canary.

The experiment is isolated from production and from the completed WR1 runtime.
It freezes four paired scenes and their deterministic reality checks before any
provider call is allowed.
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
from app.utils.llm_client import estimate_messages_tokens, get_llm_client
from app.writing.world_runtime_bakery_gold import (
    BAKERY_PROJECT_ID,
    build_saturday_bakery_gold_fixture,
)
from app.writing.world_runtime_compiler import WorldRuntimeCompiler
from app.writing.world_runtime_contracts import (
    CanonicalWorldState,
    ProjectWorldConstitution,
    ProvenanceRef,
    RuleScope,
    StatePredicate,
    WorldFact,
    WorldRule,
    canonical_hash,
)
from app.writing.world_runtime_event_contracts import (
    EventRequirement,
    EventRuntimeBinding,
    SubsectionEventContract,
)
from app.writing.world_runtime_kernel import build_minimal_universal_kernel
from app.writing.world_runtime_pack_modern_urban import (
    build_modern_urban_cn_2020s_candidate_pack,
)
from app.writing.world_runtime_prompt import WorldRuntimePromptController
from app.writing.world_runtime_resolver import WorldRuntimeResolver


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "experiments/world_runtime_writer_canary/fixtures/canary_wr1r_v1.json"
DEFAULT_OUTPUT = ROOT / ".world_runtime_wr1r_canary_runtime"
SOURCE = Path(__file__).resolve()
SYSTEM_PROMPT = (
    "你是一名中文小说作者。根据材料续写一个完整小节，只输出小说正文，不输出标题、"
    "分析、规则、字段名、检查清单或说明。目标450—1000个可见字符。保留场景要求的"
    "动作与压力，但不要用总结句代替过程。"
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


def _provenance(source_id: str, payload: Any) -> ProvenanceRef:
    return ProvenanceRef(
        source_id=source_id,
        source_type="wr1r_preregistered_fixture",
        source_hash=canonical_hash(payload),
        producer="adversarial_experiment",
    )


def _fact(
    fact_id: str,
    subject: str,
    predicate: str,
    value: Any,
    *,
    revision: int,
) -> WorldFact:
    body = {"subject": subject, "predicate": predicate, "value": value}
    return WorldFact(
        fact_id=fact_id,
        subject=subject,
        predicate=predicate,
        value=value,
        epistemic_status="confirmed_true",
        authority="project_explicit",
        provenance=_provenance(f"wr1r-fact:{fact_id}", body),
        revision=revision,
    )


def _artifacts() -> tuple[ProjectWorldConstitution, dict[str, CanonicalWorldState], Any]:
    base = build_saturday_bakery_gold_fixture()
    no_repeat = WorldRule(
        rule_id="wr1r.event.completed-no-repeat",
        semantic_key="event.completed.no_repeat",
        kind="precondition",
        authority="project_explicit",
        enforcement="block",
        scope=RuleScope(project_id=BAKERY_PROJECT_ID),
        prerequisites=(
            StatePredicate(
                subject="$event_repetition",
                predicate="has_reset_or_new_instance",
                operator="equals",
                expected=True,
            ),
        ),
        provenance=_provenance(
            "wr1r-rule:completed-no-repeat",
            {"completed_event_requires": "reset_or_new_instance"},
        ),
        version="wr1r-v1",
    )
    constitution_payload = base.constitution.model_dump()
    constitution_payload.update(
        version="wr1r-v1",
        rules=(*base.constitution.rules, no_repeat),
    )
    constitution = ProjectWorldConstitution(**constitution_payload)

    before = base.state_before
    after = base.state_after
    augmented_payload = after.model_dump()
    augmented_payload["facts"] = (
        *after.facts,
        _fact(
            "fact:event:publish-article:completion",
            "event:publish-article",
            "completion_state",
            "completed",
            revision=after.revision,
        ),
        _fact(
            "fact:event:deliver-resignation:completion",
            "event:deliver-resignation",
            "completion_state",
            "completed",
            revision=after.revision,
        ),
        _fact(
            "fact:bowl:location",
            "object:green-bean-soup-bowl",
            "location",
            "lin-wan-home:coffee-table",
            revision=after.revision,
        ),
        _fact(
            "fact:bowl:content-state",
            "object:green-bean-soup-bowl",
            "content_state",
            "contains_cold_soup",
            revision=after.revision,
        ),
        _fact(
            "fact:home:presence",
            "location:lin-wan-home",
            "present_characters",
            [],
            revision=after.revision,
        ),
    )
    states = {
        "before": before,
        "after": after,
        "after_augmented": CanonicalWorldState(**augmented_payload),
    }
    resolved = WorldRuntimeResolver().resolve(
        constitution=constitution,
        candidate_packs=(build_modern_urban_cn_2020s_candidate_pack(),),
        kernel=build_minimal_universal_kernel(),
    )
    return constitution, states, resolved


def _event_contract(scene: dict[str, Any], subsection: int) -> SubsectionEventContract:
    payload = {
        "scene_id": scene["scene_id"],
        "event_id": scene["required_event_id"],
        "fact_ids": scene["runtime_fact_ids"],
        "semantic_domains": scene["semantic_domains"],
    }
    return SubsectionEventContract(
        contract_id=f"wr1r-contract:{scene['scene_id']}",
        project_id=BAKERY_PROJECT_ID,
        section=2,
        subsection=subsection,
        requirements=(
            EventRequirement(
                event_id=scene["required_event_id"],
                description=scene["premise"],
                runtime_binding=EventRuntimeBinding(
                    fact_ids=tuple(scene["runtime_fact_ids"]),
                    semantic_domains=tuple(scene["semantic_domains"]),
                ),
            ),
        ),
        provenance=_provenance(f"wr1r-contract:{scene['scene_id']}", payload),
    )


def _baseline_messages(scene: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"场景：{scene['premise']}\n"
                f"人物：{scene['characters']}\n"
                "只写当前小节，不增加有持续影响的新关系、项目背景或世界规则。"
            ),
        },
    ]


def _evaluation_contract_payload(fixture: dict[str, Any]) -> dict[str, Any]:
    return {
        "gates": fixture["preregistered_gates"],
        "scenes": [
            {
                "scene_id": scene["scene_id"],
                "must_event_patterns": scene["must_event_patterns"],
                "violation_checks": scene["violation_checks"],
            }
            for scene in fixture["scenes"]
        ],
    }


def _assert_frozen_integrity(manifest: dict[str, Any]) -> None:
    if hashlib.sha256(FIXTURE.read_bytes()).hexdigest() != manifest["fixture_sha256"]:
        raise RuntimeError("wr1r_fixture_drift")
    if hashlib.sha256(SOURCE.read_bytes()).hexdigest() != manifest["evaluator_source_sha256"]:
        raise RuntimeError("wr1r_evaluator_source_drift")
    fixture = _read(FIXTURE)
    if _digest(_evaluation_contract_payload(fixture)) != manifest["evaluation_contract_hash"]:
        raise RuntimeError("wr1r_evaluation_contract_drift")


def build(output_dir: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    fixture = _read(FIXTURE)
    if (output_dir / "attempt-ledger.json").exists():
        raise FileExistsError("attempt ledger exists; refusing to rebuild")
    if len(fixture["scenes"]) != 4:
        raise ValueError("WR1-R requires exactly four adversarial scenes")
    _, states, resolved = _artifacts()
    samples: list[dict[str, Any]] = []
    ordinal = 0
    for scene_index, scene in enumerate(fixture["scenes"], 1):
        state = states[scene["state_variant"]]
        contract = _event_contract(scene, scene_index)
        frame = WorldRuntimeCompiler().compile(
            resolved=resolved,
            state_before=state,
            event_contract=contract,
        )
        if frame.status != "complete":
            raise ValueError(f"incomplete runtime frame: {scene['scene_id']}")
        baseline = _baseline_messages(scene)
        common_hash = _digest(
            {"messages": baseline, "scene_id": scene["scene_id"], "provider": fixture["provider"]}
        )
        arms = ["A", "B"]
        random.Random(20260804 + scene_index).shuffle(arms)
        for arm in arms:
            ordinal += 1
            sample_id = f"WR1R-{ordinal:02d}"
            task_id = f"world-runtime-wr1r:{sample_id}"
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
                raise ValueError("WR1-R arm injection mismatch")
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
                    "runtime_observation": applied.observation.model_dump(mode="json"),
                    "frame_hash": frame.frame_hash,
                    "event_contract_hash": contract.artifact_hash,
                }
            )
    if len(samples) != fixture["preregistered_gates"]["sample_count"]:
        raise ValueError("WR1-R sample count mismatch")
    for scene in fixture["scenes"]:
        paired = [item for item in samples if item["scene_id"] == scene["scene_id"]]
        if len(paired) != 2 or {item["arm"] for item in paired} != {"A", "B"}:
            raise ValueError("every WR1-R scene requires one A/B pair")
        if len({item["common_input_hash"] for item in paired}) != 1:
            raise ValueError("WR1-R paired common input drift")
    manifest = {
        "schema_version": "world-runtime-writer-adversarial-manifest-v1",
        "experiment_id": fixture["experiment_id"],
        "fixture_sha256": hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),
        "evaluator_source_sha256": hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
        "evaluation_contract_hash": _digest(_evaluation_contract_payload(fixture)),
        "evaluation_contract_authored_before_generation": fixture[
            "evaluation_contract_authored_before_generation"
        ],
        "preregistered_gates": fixture["preregistered_gates"],
        "sample_count": len(samples),
        "scene_count": len(fixture["scenes"]),
        "samples": samples,
        "production_behavior_changed": False,
        "silent_reruns_allowed": False,
    }
    ledger = {
        "schema_version": "world-runtime-writer-adversarial-attempt-ledger-v1",
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
    try:
        _assert_frozen_integrity(manifest)
        frozen_integrity = True
    except RuntimeError:
        frozen_integrity = False
    by_arm = {"A": [], "B": []}
    for item in manifest["samples"]:
        by_arm[item["arm"]].append(estimate_messages_tokens(item["messages"]))
    result = {
        "schema_version": "world-runtime-writer-adversarial-preflight-v1",
        "sample_count": len(manifest["samples"]),
        "scene_count": manifest["scene_count"],
        "provider_calls_planned": len(manifest["samples"]),
        "transport_retries": 0,
        "runtime_prompt_token_delta_mean": round(
            (sum(by_arm["B"]) - sum(by_arm["A"])) / len(by_arm["B"]), 2
        ),
        "paired_common_input_invariant": all(
            len({item["common_input_hash"] for item in manifest["samples"] if item["scene_id"] == scene_id}) == 1
            for scene_id in {item["scene_id"] for item in manifest["samples"]}
        ),
        "evaluation_contract_authored_before_generation": manifest[
            "evaluation_contract_authored_before_generation"
        ],
        "frozen_evaluator_integrity": frozen_integrity,
        "api_key_configured": bool(settings.LLM_API_KEY),
        "provider_host": urlparse(settings.LLM_BASE_URL).hostname,
        "model": settings.WRITER_LLM_MODEL,
        "production_default": settings.WRITER_WORLD_RUNTIME_MODE,
        "status": "ready" if settings.LLM_API_KEY else "blocked_missing_api_key",
    }
    if result["runtime_prompt_token_delta_mean"] > manifest["preregistered_gates"][
        "maximum_runtime_prompt_token_delta_mean"
    ]:
        result["status"] = "blocked_runtime_prompt_over_budget"
    if result["production_default"] != "off":
        result["status"] = "blocked_production_default_not_off"
    if not result["paired_common_input_invariant"]:
        result["status"] = "blocked_common_input_drift"
    if not result["frozen_evaluator_integrity"]:
        result["status"] = "blocked_frozen_evaluator_drift"
    _write(output_dir / "pre-generation-audit.json", result)
    return result


def run(output_dir: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    manifest = _read(output_dir / "private/locked-manifest.json")
    preflight = audit(output_dir)
    if preflight["status"] != "ready":
        raise RuntimeError(preflight["status"])
    ledger_path = output_dir / "attempt-ledger.json"
    ledger = _read(ledger_path)
    client = get_llm_client(settings.WRITER_LLM_MODEL)
    for sample in manifest["samples"]:
        entry = ledger["samples"][sample["sample_id"]]
        if entry["status"] != "pending" or entry["attempt_count"] != 0:
            raise RuntimeError(f"refusing silent rerun for {sample['sample_id']}")
        entry.update(status="started", attempt_count=1)
        _write(ledger_path, ledger)
        metadata: dict[str, Any] = {}
        try:
            text = client.chat_completion(
                sample["messages"],
                temperature=sample["provider"]["temperature"],
                max_tokens=sample["provider"]["max_tokens"],
                max_retries=sample["provider"]["max_retries"],
                prompt_name="world_runtime_writer_adversarial_canary_v1",
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


def _has(text: str, pattern: str) -> bool:
    return bool(re.search(pattern, text, flags=re.S))


def _violation(check_id: str, text: str) -> bool:
    if check_id == "storefront_public_open_before_0600":
        return _has(
            text,
            r"(?:卷帘门|临街门|店门|店面).{0,20}(?:打开|升起|拉开).{0,30}(?:路人|来客|顾客).{0,20}(?:进|买|接过)|(?:提前|现在).{0,8}(?:营业|卖你)|(?:收下|接过).{0,8}(?:现金|钱).{0,30}(?:递出|卖|交给).{0,12}(?:面包|可颂)",
        )
    if check_id == "public_reaction_before_publication":
        publication = re.search(r"(?:已发布|正式发布|审核通过|上线)", text)
        boundary = publication.start() if publication else len(text)
        return _has(text[:boundary], r"(?:评论|点赞|转发|阅读量).{0,12}(?:出现|增加|跳出|已经|有了)")
    if check_id == "coworker_knows_without_transmission_path":
        knows = _has(
            text,
            r"(?:同事|工作群|公司群|内网).{0,35}(?:已经|都|也)?(?:知道|看过|读过|说出|指出).{0,25}(?:文章|正文|标题|内容)|(?:同事|工作群|公司群|内网).{0,35}(?:你写的是|正文里|文章里)",
        )
        path = _has(text, r"(?:发到|贴到|转发到|上传到).{0,12}(?:工作群|公司群|内网)|(?:发给|转发给).{0,8}同事")
        return knows and not path
    if check_id == "object_changes_without_actor":
        changed = _has(text, r"(?<!没有)(?<!并非)(?:空碗|碗空了|碗里空了|洗净|洗干净|已经干净|汤没了|只剩空碗)")
        actor = _has(text, r"(?:喝|倒|洗|收|拿走|端走|碰|打翻).{0,12}(?:绿豆汤|汤碗|碗)|(?:绿豆汤|汤碗|碗).{0,12}(?:喝|倒|洗|收|拿走|端走|碰|打翻)")
        return changed and not actor
    if check_id == "completed_event_repeated":
        return _has(
            text,
            r"(?:再次|重新|又).{0,12}(?:提交|发布|发送辞职|发出辞职)|(?:她|林晚).{0,8}(?:点下|按下).{0,5}(?:提交|发布).{0,15}(?:已发布|发布成功)|(?:她|林晚).{0,8}(?:把|将).{0,8}辞职通知.{0,15}(?:再次发送|重新发送)",
        )
    if check_id == "employment_ended_without_acknowledgement":
        ended = False
        for match in re.finditer(
            r"(?:已经离职|正式离职|辞职(?:已经)?生效|劳动关系(?:已经)?终止|不再是公司的人)",
            text,
        ):
            prefix = text[max(0, match.start() - 16):match.start()]
            suffix = text[match.end():match.end() + 4]
            if re.search(r"(?:是否|是不是|问.{0,8}|并没有|没有|尚未|不是)$", prefix):
                continue
            if re.match(r"(?:吗|么|？|\?)", suffix):
                continue
            ended = True
            break
        ack = _has(text, r"(?:公司|人事).{0,15}(?:确认|批准|同意)|(?:约定|正式)生效日期.{0,8}(?:已到|到了)")
        return ended and not ack
    raise ValueError(f"unknown WR1-R violation check: {check_id}")


def evaluate_text(scene: dict[str, Any], text: str) -> dict[str, Any]:
    must_events = [
        {"pattern": pattern, "passed": _has(text, pattern)}
        for pattern in scene["must_event_patterns"]
    ]
    violations = {
        check_id: _violation(check_id, text)
        for check_id in scene["violation_checks"]
    }
    leakage_terms = (
        "本小节世界运行边界",
        "因果边界",
        "合法路径",
        "semantic_key",
        "completion_state",
        "event.completed.no_repeat",
    )
    visible = _visible(text)
    return {
        "visible_characters": visible,
        "within_length_band": 450 <= visible <= 1000,
        "must_events": must_events,
        "must_event_pass": all(item["passed"] for item in must_events),
        "hard_reality_violations": violations,
        "hard_reality_violation_count": sum(violations.values()),
        "field_leakage_detected": any(term in text for term in leakage_terms),
    }


def evaluate(output_dir: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    fixture = _read(FIXTURE)
    scenes = {item["scene_id"]: item for item in fixture["scenes"]}
    manifest = _read(output_dir / "private/locked-manifest.json")
    _assert_frozen_integrity(manifest)
    ledger = _read(output_dir / "attempt-ledger.json")
    items = []
    for sample in manifest["samples"]:
        entry = ledger["samples"][sample["sample_id"]]
        if entry["status"] != "succeeded":
            raise RuntimeError("all WR1-R samples must succeed before evaluation")
        text = (output_dir / "private/outputs" / f"{sample['sample_id']}.txt").read_text(encoding="utf-8")
        items.append(
            {
                "sample_id": sample["sample_id"],
                "scene_id": sample["scene_id"],
                "arm": sample["arm"],
                "output_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "checks": evaluate_text(scenes[sample["scene_id"]], text),
            }
        )
    aggregate = {}
    for arm in ("A", "B"):
        values = [item["checks"] for item in items if item["arm"] == arm]
        aggregate[arm] = {
            "samples": len(values),
            "scenes_with_violation": sum(item["hard_reality_violation_count"] > 0 for item in values),
            "hard_reality_violation_count": sum(item["hard_reality_violation_count"] for item in values),
            "must_event_passes": sum(item["must_event_pass"] for item in values),
            "length_band_passes": sum(item["within_length_band"] for item in values),
            "field_leakage_count": sum(item["field_leakage_detected"] for item in values),
        }
    gates = {
        "baseline_adversarial_activation": aggregate["A"]["scenes_with_violation"]
        >= fixture["preregistered_gates"]["baseline_adversarial_activation_minimum_scenes"],
        "runtime_hard_violation_count_lower": aggregate["B"]["hard_reality_violation_count"]
        < aggregate["A"]["hard_reality_violation_count"],
        "must_event_retention_non_inferior": aggregate["B"]["must_event_passes"]
        >= aggregate["A"]["must_event_passes"],
        "field_leakage_forbidden": aggregate["B"]["field_leakage_count"] == 0,
        "owner_prose_review_complete": False,
        "promotion_from_this_run_forbidden": True,
    }
    result = {
        "schema_version": "world-runtime-writer-adversarial-evaluation-v1",
        "evaluation_contract_authored_before_generation": True,
        "items": items,
        "aggregate": aggregate,
        "gates": gates,
        "promotion_eligible": False,
        "decision": "diagnostic_only_pending_owner_review",
    }
    _write(output_dir / "evaluation.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("build", "audit", "run", "evaluate"))
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    print(json.dumps(globals()[args.command](args.output), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
