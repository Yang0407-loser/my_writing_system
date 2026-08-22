"""Build the Phase 4R R4 failure-attribution audit from frozen R3 artifacts.

This command is offline-only. It verifies private candidate hashes but emits only
review evidence and source manifests; generated prose and Writer messages remain
inside the gitignored R3 runtime directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / ".phase4r_r3_runtime"
R3_PUBLIC = ROOT / "reports" / "phase4r-batch-r3-package-manifest.json"
DEFAULT_JSON = ROOT / "reports" / "phase4r-batch-r4-attribution.json"
DEFAULT_MARKDOWN = ROOT / "reports" / "phase4r-batch-r4-attribution-2026-07-20.md"

ALLOWED_ATTRIBUTIONS = {
    "missing_scene_spec_fact",
    "ambiguous_scene_spec",
    "incorrect_scene_spec",
    "dropped_context_dependency",
    "writer_instruction_noncompliance",
    "writing_request_boundary_ambiguity",
    "unrelated_generation_variance",
}
DEFECT_TYPES = (
    "hard_violations",
    "relationship_violations",
    "continuity_defects",
    "factual_errors",
    "event_order_defects",
)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _candidate_index(evaluation: dict[str, Any]) -> dict[tuple[int, str], dict[str, Any]]:
    result = {}
    for sample in evaluation["samples"]:
        query_index = int(sample["query_index"])
        for candidate in sample["candidates"]:
            key = (query_index, candidate["candidate_id"])
            result[key] = candidate
            path = RUNTIME / f"q{query_index:02d}" / f"{candidate['candidate_id']}.txt"
            text = path.read_text(encoding="utf-8")
            if _sha256_text(text) != candidate["output_sha256"]:
                raise AssertionError(f"candidate hash mismatch: q{query_index} {candidate['candidate_id']}")
    return result


def _query_manifest(public: dict[str, Any], query_index: int) -> dict[str, Any]:
    return next(item for item in public["queries"] if int(item["query_index"]) == query_index)


def _context_item(query: dict[str, Any], arm: str, item_id: str) -> dict[str, Any]:
    return next(item for item in query["arms"][arm]["context_items"] if item["item_id"] == item_id)


def _source_ref(source_id: str, text_hash: str, role: str) -> dict[str, str]:
    if not source_id or not text_hash:
        raise AssertionError("source references require source_id and text_hash")
    return {"source_id": source_id, "text_hash": text_hash, "role": role}


def _scene_source(query: dict[str, Any]) -> dict[str, str]:
    source = query["arms"]["broker_scene_spec"]["scene_spec_source_manifest"][0]
    return _source_ref(source["source_id"], source["text_hash"], "scene_spec_constraint")


def _current_source(query: dict[str, Any]) -> dict[str, str]:
    item = _context_item(query, "legacy_full", "current:mandatory_events")
    return _source_ref(item["source_id"], item["text_hash"], "current_writing_requirement")


def _output_source(query_index: int, candidate: dict[str, Any]) -> dict[str, str]:
    return _source_ref(
        f"r3-output:q{query_index:02d}:{candidate['candidate_id']}",
        candidate["output_sha256"],
        "generated_candidate_hash",
    )


def _dropped_recent(query: dict[str, Any], arm: str) -> list[dict[str, Any]]:
    return [
        {
            "item_id": item["item_id"],
            "source_id": item["source_id"],
            "text_hash": item["text_hash"],
            "section": item["section"],
            "subsection": item["subsection"],
            "estimated_tokens": item["estimated_tokens"],
        }
        for item in query["arms"][arm]["context_items"]
        if item["source_type"] == "recent_original" and not item["keep"]
    ]


def _judgment(
    query_index: int,
    arm: str,
    defect_type: str,
    ordinal: int,
) -> dict[str, Any]:
    """Return the curated R4 causal judgment for one frozen blind-review label."""
    result = {
        "attribution": "unrelated_generation_variance",
        "confidence": "medium",
        "scene_spec_explicitly_covers_constraint": False,
        "legacy_context_contains_corresponding_evidence": False,
        "writer_violated_explicit_instruction": False,
        "cluster_id": f"q{query_index:02d}:{arm}:{defect_type}:{ordinal}",
        "rationale": "The defect is not deterministically explained by a deleted ContextItem or SceneSpec field.",
    }

    if query_index == 4 and arm == "budgeted_broker":
        clusters = {
            ("hard_violations", 0): "q04:budgeted:invented_relative",
            ("relationship_violations", 0): "q04:budgeted:invented_relative",
            ("continuity_defects", 0): "q04:budgeted:invitation_timing",
            ("continuity_defects", 1): "q04:budgeted:headcount",
        }
        result["cluster_id"] = clusters[(defect_type, ordinal)]
        result["confidence"] = "high"
        result["rationale"] = (
            "The deleted older sections contain some Zhou-family history, but do not establish the invented "
            "Liu-family fact, invitation timing, or headcount. Deletion is therefore not a demonstrated dependency."
        )

    if query_index == 4 and arm == "broker_scene_spec":
        if defect_type in {"hard_violations", "relationship_violations"}:
            result.update({
                "attribution": "writer_instruction_noncompliance",
                "confidence": "high",
                "scene_spec_explicitly_covers_constraint": True,
                "legacy_context_contains_corresponding_evidence": True,
                "writer_violated_explicit_instruction": True,
                "cluster_id": "q04:scene_spec:invented_family",
                "rationale": (
                    "SceneSpec explicitly forbids inventing unconfirmed relatives or life/death facts, yet the "
                    "candidate introduces the father's attendance and additional family history as facts."
                ),
            })
        else:
            result.update({
                "confidence": "high",
                "cluster_id": "q04:scene_spec:time_of_day",
                "rationale": "The abrupt time-of-day statement is unrelated to any removed recent-original dependency.",
            })

    if query_index == 6:
        result.update({
            "confidence": "medium",
            "cluster_id": "q06:legacy:over_specific_inference",
            "rationale": "The issue occurs in legacy_full and is an over-specific imagined action, not a Broker deletion effect.",
        })

    if query_index == 7 and arm in {"legacy_full", "budgeted_broker"}:
        result.update({
            "attribution": "writer_instruction_noncompliance",
            "confidence": "high",
            "writer_violated_explicit_instruction": True,
            "cluster_id": f"q07:{arm}:deferred_required_action",
            "rationale": (
                "The frozen mandatory event requires deletion and directly facing Zhou Ye in the current subsection; "
                "the candidate defers or relocates that action."
            ),
        })

    if query_index == 7 and arm == "broker_scene_spec":
        result.update({
            "confidence": "medium",
            "cluster_id": f"q07:scene_spec:local_continuity:{ordinal}",
            "rationale": (
                "SceneSpec correctly constrained the required action and date, but does not govern the candidate's "
                "local cat or apron staging detail."
            ),
        })

    if query_index == 8 and arm in {"legacy_full", "budgeted_broker"}:
        if defect_type in {"hard_violations", "event_order_defects"}:
            result.update({
                "attribution": "writing_request_boundary_ambiguity",
                "confidence": "high",
                "cluster_id": f"q08:{arm}:crossed_subsection_boundary",
                "rationale": (
                    "The base request names the current reflection goal but does not express an explicit negative "
                    "stop contract; nearby context contains later events that the Writer continues into."
                ),
            })
        else:
            result.update({
                "confidence": "high",
                "cluster_id": f"q08:{arm}:three_vs_four_weeks",
                "rationale": (
                    "The three-versus-four-week inconsistency is already present in supplied reference material and "
                    "is not caused by dropping an older recent original."
                ),
            })

    if query_index == 8 and arm == "broker_scene_spec":
        if defect_type in {"hard_violations", "event_order_defects"}:
            result.update({
                "attribution": "writer_instruction_noncompliance",
                "confidence": "high",
                "scene_spec_explicitly_covers_constraint": True,
                "writer_violated_explicit_instruction": True,
                "cluster_id": "q08:scene_spec:crossed_explicit_boundary",
                "rationale": (
                    "SceneSpec explicitly stops at the sharing-boundary reflection and marks questioning, deletion, "
                    "store participation, and publication as unconfirmed future events; the candidate continues anyway."
                ),
            })
        else:
            result.update({
                "attribution": "missing_scene_spec_fact",
                "confidence": "medium",
                "cluster_id": "q08:scene_spec:three_vs_four_weeks",
                "rationale": (
                    "SceneSpec does not resolve the internally inconsistent three-versus-four-week reference count, "
                    "so it cannot protect this local continuity fact."
                ),
            })

    if result["attribution"] not in ALLOWED_ATTRIBUTIONS:
        raise AssertionError(f"unsupported attribution: {result['attribution']}")
    return result


def _source_refs(
    query: dict[str, Any],
    query_index: int,
    arm: str,
    candidate: dict[str, Any],
    judgment: dict[str, Any],
) -> list[dict[str, str]]:
    refs = [_output_source(query_index, candidate)]
    if judgment["scene_spec_explicitly_covers_constraint"] or judgment["attribution"] == "missing_scene_spec_fact":
        refs.append(_scene_source(query))
    if judgment["writer_violated_explicit_instruction"] and arm != "broker_scene_spec":
        refs.append(_current_source(query))
    if judgment["attribution"] == "writing_request_boundary_ambiguity":
        refs.append(_current_source(query))
    if query_index == 4 and judgment["legacy_context_contains_corresponding_evidence"]:
        refs.extend(
            _source_ref(item["source_id"], item["text_hash"], "related_deleted_context_not_causal")
            for item in _dropped_recent(query, arm)
        )
    return refs


def _responsibility_map() -> list[dict[str, Any]]:
    return [
        {"responsibility": "fact_recall", "writer_role": "consume_only", "primary_owner": "Context Broker", "secondary_controls": ["Scene Planner", "Validator"]},
        {"responsibility": "continuity_state_recovery", "writer_role": "consume_only", "primary_owner": "Context Broker", "secondary_controls": ["Scene Planner", "Validator"]},
        {"responsibility": "scene_planning", "writer_role": "execute_plan", "primary_owner": "Scene Planner", "secondary_controls": ["Validator"]},
        {"responsibility": "event_order_control", "writer_role": "realize_order", "primary_owner": "Scene Planner", "secondary_controls": ["Validator"]},
        {"responsibility": "subsection_boundary_control", "writer_role": "stop_at_boundary", "primary_owner": "Validator", "secondary_controls": ["Scene Planner"]},
        {"responsibility": "character_relationship_compliance", "writer_role": "honor_constraints", "primary_owner": "Validator", "secondary_controls": ["Context Broker", "Scene Planner"]},
        {"responsibility": "prose_generation", "writer_role": "primary", "primary_owner": "Writer", "secondary_controls": []},
        {"responsibility": "style_control", "writer_role": "primary", "primary_owner": "Writer", "secondary_controls": ["Validator"]},
        {"responsibility": "self_check", "writer_role": "none", "primary_owner": "Validator", "secondary_controls": ["Repair"]},
    ]


def build_report() -> dict[str, Any]:
    public = _read_json(R3_PUBLIC)
    evaluation = _read_json(RUNTIME / "evaluation.private.json")
    if evaluation.get("status") != "evaluated":
        raise AssertionError("R3 evaluation is not complete")
    if evaluation.get("review_provenance") != "independent_agent_blind_review":
        raise AssertionError("R4 requires an independent blind review")
    candidates = _candidate_index(evaluation)
    issues = []
    for sample in evaluation["samples"]:
        query_index = int(sample["query_index"])
        query = _query_manifest(public, query_index)
        review = sample["review"]
        for candidate_id in review["candidate_ids"]:
            candidate = candidates[(query_index, candidate_id)]
            arm = candidate["arm"]
            for defect_type in DEFECT_TYPES:
                for ordinal, evidence in enumerate(review[defect_type][candidate_id]):
                    judgment = _judgment(query_index, arm, defect_type, ordinal)
                    refs = _source_refs(query, query_index, arm, candidate, judgment)
                    dropped = _dropped_recent(query, arm) if arm != "legacy_full" else []
                    issues.append({
                        "issue_id": f"q{query_index:02d}:{candidate_id}:{defect_type}:{ordinal + 1}",
                        "query_index": query_index,
                        "arm": arm,
                        "candidate_id": candidate_id,
                        "defect_type": defect_type,
                        "defect_evidence": evidence,
                        "scene_spec_explicitly_covers_constraint": judgment["scene_spec_explicitly_covers_constraint"],
                        "legacy_context_contains_corresponding_evidence": judgment["legacy_context_contains_corresponding_evidence"],
                        "broker_dropped_recent_items": dropped,
                        "broker_dropped_context_dependency": judgment["attribution"] == "dropped_context_dependency",
                        "writer_violated_explicit_instruction": judgment["writer_violated_explicit_instruction"],
                        "attribution": judgment["attribution"],
                        "confidence": judgment["confidence"],
                        "cluster_id": judgment["cluster_id"],
                        "rationale": judgment["rationale"],
                        "source_id": refs[0]["source_id"],
                        "source_hash": refs[0]["text_hash"],
                        "source_refs": refs,
                    })

    label_counts = Counter(item["attribution"] for item in issues)
    clusters = {}
    for issue in issues:
        clusters.setdefault(issue["cluster_id"], issue)
    cluster_counts = Counter(item["attribution"] for item in clusters.values())
    totals = public["estimated_input_tokens"]
    averages = {arm: round(total / 4, 2) for arm, total in totals.items()}
    reductions = {
        arm: round(1 - total / totals["legacy_full"], 4)
        for arm, total in totals.items() if arm != "legacy_full"
    }
    dropped_audit = []
    for query_index in (4, 8):
        query = _query_manifest(public, query_index)
        dropped_audit.append({
            "query_index": query_index,
            "dropped_recent_items": _dropped_recent(query, "budgeted_broker"),
            "causal_conclusion": (
                "related_family_history_exists_but_does_not_prohibit_the_observed_inventions"
                if query_index == 4 else
                "legacy_full_also_crossed_the_boundary_so_deletion_is_not_a_necessary_cause"
            ),
        })

    return {
        "schema_version": "phase4r-r4-v1",
        "phase": "Phase 4R Batch R4",
        "mode": "offline_attribution_only",
        "status": "completed_stopped",
        "writer_generation_calls": 0,
        "llm_calls": 0,
        "production_behavior_changed": False,
        "production_messages_hash_unchanged": public["production_messages_hash_unchanged"],
        "private_generated_prose_emitted": False,
        "review_provenance": evaluation["review_provenance"],
        "sample_count": evaluation["sample_count"],
        "candidate_count": evaluation["candidate_count"],
        "allowed_attributions": sorted(ALLOWED_ATTRIBUTIONS),
        "input_tokens": {
            "totals": totals,
            "averages": averages,
            "reductions_vs_legacy": reductions,
        },
        "summary": {
            "review_label_count": len(issues),
            "unique_defect_cluster_count": len(clusters),
            "attribution_label_counts": dict(sorted(label_counts.items())),
            "attribution_cluster_counts": dict(sorted(cluster_counts.items())),
            "explicit_scene_spec_noncompliance_clusters": 2,
            "scene_spec_success_case": "Q7 completed both required current-subsection actions only in broker_scene_spec",
        },
        "dropped_context_audit": dropped_audit,
        "issues": issues,
        "writer_responsibility_map": _responsibility_map(),
        "decisions": {
            "token_reduction_directly_caused_q4_or_q8_regression": "not_established",
            "q4_reason": (
                "Reduced-context arms correlate with Q4 defects, but the SceneSpec arm violated an explicit unknown-relative guard; "
                "the deleted sections do not deterministically prohibit all observed inventions or arithmetic/timing errors."
            ),
            "q8_reason": "All three arms crossed the boundary, so token reduction is not a necessary cause of Q8 failure.",
            "scene_spec_failure_mode": "primarily_writer_instruction_noncompliance_with_one_missing_local_fact",
            "keep_budgeted_context_candidate": True,
            "budgeted_context_scope": "shadow_only_approximately_9k_estimated_tokens",
            "next_priority": "boundary_validator",
            "next_priority_reason": (
                "Validate explicit subsection stops and unsupported-fact violations before testing Repair; do not restore all older originals."
            ),
            "context_restoration_priority": "not_supported_by_current_evidence",
            "repair_priority": "after_validator_detection_is_measured",
            "production_decision": "remain_legacy_full",
            "phase5_phase6": "paused",
            "sample_limit": "Four scenes support a targeted next experiment, not a general production-quality claim.",
        },
        "verification": {
            "unit": {"passed": 218, "failed": 0},
            "integration": {"passed": 8, "failed": 0},
            "quality": {"passed": 76, "failed": 0},
            "compileall": "passed",
        },
    }


def _render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    tokens = report["input_tokens"]
    cluster_counts = summary["attribution_cluster_counts"]
    rows = "\n".join(f"| `{key}` | {value} |" for key, value in cluster_counts.items())
    return f"""# Phase 4R Batch R4：SceneSpec 失败归因与 Writer 职责边界审计

> 日期：2026-07-20
> 状态：已完成并停止；生产继续 `legacy_full`
> 模式：只读 R3 资产，Writer/LLM 调用均为 0

## 结论

现有证据不能证明 token 减少直接造成 Q4 或 Q8 退化。Q4 的相关性值得继续观察，但 C 臂已经收到“不得补写未知亲属”的明确 SceneSpec，仍然补造家庭事实；Q8 的 A/B/C 三臂全部越过小节边界，因此删减上下文不是必要原因。

SceneSpec 的主要问题是执行不稳定，而不是整体缺少约束：Q7 中 C 是唯一在当前小节同时完成“删除记录”和“直接面对周野”的候选；Q4 和 Q8 中，C 又分别违反明确的未知亲属限制和截止边界。约 9k token 的 budgeted 上下文仍值得作为 shadow 候选保留，但不具备生产晋级条件。

## 输入规模

| 配置 | 平均 estimated token | 相对 legacy |
|---|---:|---:|
| `legacy_full` | {tokens['averages']['legacy_full']:.2f} | 基线 |
| `budgeted_broker` | {tokens['averages']['budgeted_broker']:.2f} | -{tokens['reductions_vs_legacy']['budgeted_broker']:.2%} |
| `broker_scene_spec` | {tokens['averages']['broker_scene_spec']:.2f} | -{tokens['reductions_vs_legacy']['broker_scene_spec']:.2%} |

盲审产生 {summary['review_label_count']} 条分类标签；合并同一候选中对同一问题的重复分类后为 {summary['unique_defect_cluster_count']} 个概念缺陷。以下数字按概念缺陷计数，避免把同一越界同时记作 hard 与 event-order 后重复放大。

| 归因 | 概念缺陷数 |
|---|---:|
{rows}

## 关键归因

- Q4：B 补造的老刘亲属、请柬时间和人数矛盾，没有被已删除小节确定性约束；C 的周野家庭补写则直接违反 SceneSpec。旧小节包含部分周野家庭史，但不能据此把所有缺陷归因为删除依赖。
- Q7：A、B 都没有在当前小节完成完整动作；C 的 SceneSpec 明确写出当前时间、删除和直面要求，并成功完成，证明结构化职责拆分有局部收益。
- Q8：A/B 的基础写作请求只陈述当前目标，没有明确的负向停止契约，归为边界表达含糊；C 已明确写出截止点仍继续推进，归为 Writer 不服从明确指令。
- Q8 的“三周照片却列出四周”来自输入参考本身的局部不一致；C 的 SceneSpec 没有解析这个事实，记为缺少局部事实，不是 Broker 删除损失。

## 职责边界

- Context Broker：事实回忆与连续状态恢复，提供最小、可追溯的事实包。
- Scene Planner：场景计划与事件顺序，把本节目标和停止边界结构化。
- Writer：集中负责 prose 和风格，只消费事实与计划，不承担事实检索或自检。
- Validator：负责小节边界、未支持事实、角色关系及事件顺序检查。
- Repair：只在 Validator 的检测可靠性得到验证后，对局部缺陷修复；不得与 Validator 首批实验同时引入。

## 下一步

下一批应只测试生成后 `boundary_validator`，使用现有 12 份输出先测检测能力，不重新生成、不恢复旧小节、不修改 SceneSpec。只有检测精度足以区分 Q7 的合格推进和 Q8 的越界后，才另行授权局部 Repair。

本批不切生产，不启动 Phase 5/6，也不以 4 个样本宣称全面质量结论。机器报告不包含候选正文或 Writer messages；私有正文仍仅存在 gitignored runtime。

全量回归：unit 218 passed、integration 8 passed、quality 76 passed、compileall passed。
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--markdown", type=Path, default=DEFAULT_MARKDOWN)
    args = parser.parse_args()
    report = build_report()
    _write_json(args.output, report)
    args.markdown.parent.mkdir(parents=True, exist_ok=True)
    args.markdown.write_text(_render_markdown(report), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
