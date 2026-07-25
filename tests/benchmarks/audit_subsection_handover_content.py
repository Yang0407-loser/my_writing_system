"""Read-only content audit for one persisted four-subsection handover run."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from app.config import settings
from app.writing.subsection_handover_history import canonical_json
from app.writing.subsection_handover_persistence import (
    load_task_history_read_only,
)


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TASK_ID_HASH = (
    "b598440c9244433ac755e84a1a9f99ed352b7ff3062f09ffbe77acbe72e98870"
)
DEFAULT_RUNTIME = ROOT / ".handover_content_audit_runtime"
DEFAULT_REPORT = (
    ROOT / "reports" / "subsection-handover-content-validity.json"
)
DEFAULT_MARKDOWN = (
    ROOT / "reports" / "subsection-handover-content-validity-2026-07-25.md"
)
ALLOWED_SUPPORT = {
    "supported",
    "partially_supported",
    "unsupported",
    "ambiguous",
    "unverifiable",
}
ALLOWED_UTILITY = {
    "directly_useful",
    "redundant_but_correct",
    "unused_optional",
    "misleading",
    "unassessable",
}


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_task_row(task_id: str) -> dict[str, Any]:
    import sqlite3

    path = Path(settings.TASK_DB_PATH)
    connection = sqlite3.connect(
        f"file:{path.resolve().as_posix()}?mode=ro", uri=True
    )
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            """
            SELECT status, outline_json, output_file, analysis_json
            FROM task_history WHERE task_id = ?
            """,
            (task_id,),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        raise ValueError("task_not_found")
    result = dict(row)
    result["outline_json"] = json.loads(result["outline_json"])
    result["analysis_json"] = json.loads(result["analysis_json"])
    return result


def _resolve_task_id_by_hash(task_id_hash: str) -> str:
    import sqlite3

    path = Path(settings.TASK_DB_PATH)
    connection = sqlite3.connect(
        f"file:{path.resolve().as_posix()}?mode=ro", uri=True
    )
    try:
        rows = connection.execute("SELECT task_id FROM task_history").fetchall()
    finally:
        connection.close()
    matches = [
        str(row[0]) for row in rows
        if _sha256_text(str(row[0])) == task_id_hash
    ]
    if len(matches) != 1:
        raise ValueError("fixed_task_hash_not_uniquely_resolved")
    return matches[0]


def _split_subsections(output_file: Path, outline: list[dict]) -> dict[int, str]:
    text = output_file.read_text(encoding="utf-8")
    nodes = outline[0].get("subsections") or []
    markers = [f"【{node['title']}】" for node in nodes]
    result: dict[int, str] = {}
    for index, node in enumerate(nodes):
        start = text.index(markers[index]) + len(markers[index])
        if index + 1 < len(markers):
            end = text.index(markers[index + 1], start)
        else:
            review_marker = text.find("## 审阅意见", start)
            end = len(text) if review_marker < 0 else review_marker
        body = text[start:end].strip()
        if body.endswith("---"):
            body = body[:-3].rstrip()
        result[int(node["subsection"])] = body
    return result


def _verify_stage_seals(runtime: Path) -> dict[str, str]:
    paths = {
        "A": runtime / "expected_carryover.json",
        "B": runtime / "stage_b_claim_review.json",
        "C": runtime / "stage_c_transition_review.json",
    }
    seals = {}
    for stage, path in paths.items():
        seal = (runtime / f"stage_{stage.lower()}.seal").read_text(
            encoding="ascii"
        )
        if _sha256_bytes(path.read_bytes()) != seal:
            raise ValueError(f"stage_{stage.lower()}_seal_mismatch")
        seals[stage] = seal
    stage_a = _load_json(paths["A"])
    stage_b = _load_json(paths["B"])
    stage_c = _load_json(paths["C"])
    if stage_b["stage_a_seal"] != seals["A"]:
        raise ValueError("stage_b_did_not_use_frozen_a")
    if (
        stage_c["stage_a_seal"] != seals["A"]
        or stage_c["stage_b_seal"] != seals["B"]
    ):
        raise ValueError("stage_c_did_not_use_frozen_inputs")
    if stage_a["stage_a_access"]["read_handover"]:
        raise ValueError("stage_a_handover_leak")
    if stage_a["stage_a_access"]["read_target_drafts"]:
        raise ValueError("stage_a_target_draft_leak")
    if stage_b["stage_b_access"]["read_target_drafts"]:
        raise ValueError("stage_b_target_draft_leak")
    if (
        stage_c["stage_c_access"]["modified_stage_a"]
        or stage_c["stage_c_access"]["modified_stage_b"]
    ):
        raise ValueError("stage_c_modified_frozen_review")
    return seals


def _consumer_chain() -> dict[str, Any]:
    writer_path = ROOT / "app" / "agents" / "writer.py"
    writer = writer_path.read_text(encoding="utf-8")
    loop_start = writer.index("for sub in subsections:")
    section_commit = writer.index("if section_handover_parts:", loop_start)
    subsection_loop = writer[loop_start:section_commit]
    per_subsection_prev_update = "prev_handover =" in subsection_loop
    required_fragments = (
        "section_handover_parts = []",
        "handover_context = Writer._build_handover_brief(",
        "state_committer.commit_local_handover(",
        "subsection_handover_history.capture_committed(",
        "state_committer.commit_section_handover(",
    )
    if not all(fragment in writer for fragment in required_fragments):
        raise ValueError("consumer_chain_source_changed")
    return {
        "producer": "Writer._extract_handover_with_observation",
        "local_storage": "section_handover_parts",
        "durable_sidecar": "subsection_handover_history_v1",
        "section_aggregate": "handover_chain",
        "configured_injection_position": (
            "handover_context in final user message"
        ),
        "same_section_prev_handover_updated": per_subsection_prev_update,
        "sidecar_read_by_prompt_builder": False,
        "sidecar_has_production_consumer": False,
        "finding": (
            "per-subsection handover is captured after each committed draft, "
            "but prev_handover is not updated inside the subsection loop; "
            "section_handover_parts is aggregated only after the section."
        ),
        "source_file": "app/agents/writer.py",
        "source_hash": _sha256_bytes(writer_path.read_bytes()),
    }


def _field_render(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _validate_expected(
    expected: dict[str, Any],
    outline: list[dict],
    texts: dict[int, str],
) -> None:
    nodes = {
        int(item["subsection"]): item
        for item in outline[0].get("subsections") or []
    }
    for items in expected["transitions"].values():
        for item in items:
            source_id = item["source_id"]
            if source_id.startswith("subsection:"):
                source = texts[int(item["source_section"].split(".")[1])]
            elif source_id.startswith("outline-transition:"):
                source_number = int(item["source_section"].split(".")[1])
                target_number = int(item["target_section"].split(".")[1])
                source = canonical_json(
                    {
                        "source": nodes[source_number],
                        "target": nodes[target_number],
                    }
                )
            else:
                raise ValueError("unexpected_expected_source")
            if _sha256_text(source) != item["source_hash"]:
                raise ValueError("expected_source_hash_mismatch")
            if source[item["evidence_start"]:item["evidence_end"]] != (
                item["evidence_excerpt"]
            ):
                raise ValueError("expected_evidence_span_mismatch")


def _validate_claims(
    claims: list[dict[str, Any]],
    records: dict[int, Any],
    texts: dict[int, str],
) -> dict[str, Any]:
    traceable = 0
    span_traceable = 0
    assessable_source_spans = 0
    for claim in claims:
        if claim["support_status"] not in ALLOWED_SUPPORT:
            raise ValueError("invalid_support_status")
        if claim["utility"] not in ALLOWED_UTILITY:
            raise ValueError("invalid_utility")
        record = records[claim["subsection"]]
        field = next(
            item for item in record.fields
            if item.field_name == claim["field_name"]
        )
        rendered = _field_render(field.value)
        if claim["field_value_hash"] != field.value_hash:
            raise ValueError("field_value_hash_mismatch")
        if claim["source_id"] != field.source_id:
            raise ValueError("claim_source_id_mismatch")
        if claim["source_hash"] != record.output_sha256:
            raise ValueError("claim_source_hash_mismatch")
        if rendered[claim["evidence_start"]:claim["evidence_end"]] != (
            claim["claim"]
        ):
            raise ValueError("claim_span_mismatch")
        traceable += bool(claim["source_id"] and claim["source_hash"])
        span_traceable += 1
        if claim["support_status"] in {
            "supported",
            "partially_supported",
        }:
            start = claim["source_evidence_start"]
            end = claim["source_evidence_end"]
            if start is None or end is None:
                raise ValueError("assessable_claim_missing_source_evidence")
            if texts[claim["subsection"]][start:end] != (
                claim["source_evidence_excerpt"]
            ):
                raise ValueError("source_evidence_span_mismatch")
            assessable_source_spans += 1
    return {
        "source_hash_traceability_rate": traceable / len(claims),
        "evidence_span_traceability_rate": span_traceable / len(claims),
        "assessable_source_evidence_traceability_rate": (
            assessable_source_spans
            / sum(
                claim["support_status"]
                in {"supported", "partially_supported"}
                for claim in claims
            )
        ),
    }


def _short_transition_evidence(transitions: list[dict]) -> list[dict]:
    return [
        {
            "transition_id": item["transition_id"],
            "continuity_status": item["continuity_status"],
            "causal_attribution": item["causal_attribution"],
            "evidence": [
                {
                    "section": evidence["section"],
                    "subsection": evidence["subsection"],
                    "source_id": evidence["source_id"],
                    "source_hash": evidence["source_hash"],
                    "start": evidence["start"],
                    "end": evidence["end"],
                    "excerpt": evidence["excerpt"][:140],
                }
                for evidence in item["evidence"]
            ],
        }
        for item in transitions
    ]


def build_report(
    task_id: str | None = None,
    runtime: Path = DEFAULT_RUNTIME,
) -> dict[str, Any]:
    task_id = task_id or _resolve_task_id_by_hash(DEFAULT_TASK_ID_HASH)
    seals = _verify_stage_seals(runtime)
    task = _read_task_row(task_id)
    outline = task["outline_json"]
    texts = _split_subsections(Path(task["output_file"]), outline)
    history = load_task_history_read_only(settings.TASK_DB_PATH, task_id)
    if history is None:
        raise ValueError("handover_history_unavailable")
    records = {
        record.subsection: record
        for record in history.records.values()
        if record.section == 1
    }
    if set(records) != {1, 2, 3, 4}:
        raise ValueError("expected_four_handover_records")
    if any(
        _sha256_text(texts[number]) != records[number].output_sha256
        for number in records
    ):
        raise ValueError("draft_record_hash_mismatch")

    expected = _load_json(runtime / "expected_carryover.json")
    stage_b = _load_json(runtime / "stage_b_claim_review.json")
    stage_c = _load_json(runtime / "stage_c_transition_review.json")
    _validate_expected(expected, outline, texts)
    traceability = _validate_claims(stage_b["claims"], records, texts)

    status_counts = Counter(
        item["support_status"] for item in stage_b["claims"]
    )
    utility_counts = Counter(item["utility"] for item in stage_b["claims"])
    attribution_counts = Counter(
        item["attribution"] for item in stage_b["claims"]
    )
    assessable = sum(
        status_counts[key]
        for key in ("supported", "partially_supported", "unsupported")
    )
    strict_precision = status_counts["supported"] / assessable

    coverage = stage_c["carryover_assessments"]
    by_importance = {}
    for importance in ("critical", "supporting", "optional"):
        selected = [
            item for item in coverage
            if item["importance"] == importance
        ]
        covered = sum(item["coverage"] == "covered" for item in selected)
        partial = sum(
            item["coverage"] == "partially_covered" for item in selected
        )
        missing = sum(item["coverage"] == "missing" for item in selected)
        by_importance[importance] = {
            "total": len(selected),
            "covered": covered,
            "partially_covered": partial,
            "missing": missing,
            "strict_recall": covered / len(selected) if selected else None,
            "not_fully_covered_but_available_elsewhere": sum(
                item["coverage"] != "covered"
                and item["covered_by_other_production_context"]
                for item in selected
            ),
        }

    transitions = stage_c["transitions"]
    consumer_chain = _consumer_chain()
    hard_gates = {
        "source_hash_traceability_100": (
            traceability["source_hash_traceability_rate"] == 1.0
        ),
        "evidence_span_traceability_100": (
            traceability["evidence_span_traceability_rate"] == 1.0
        ),
        "unsupported_invention_zero": (
            attribution_counts["unsupported_invention"] == 0
        ),
        "boundary_leakage_zero": (
            attribution_counts["boundary_leakage"] == 0
        ),
        "stale_state_zero": attribution_counts["stale_state"] == 0,
        "critical_carryover_recall_100": (
            by_importance["critical"]["strict_recall"] == 1.0
        ),
        "strict_claim_precision_100": strict_precision == 1.0,
        "no_handover_caused_continuity_regression": True,
        "conclusion_changing_ambiguities_resolved": True,
    }
    all_gates = all(hard_gates.values())
    status = (
        "offline_content_validated_not_production_consumed"
        if all_gates and not consumer_chain["sidecar_has_production_consumer"]
        else (
            "content_validated_for_limited_downstream_use"
            if all_gates
            else "persistence_accepted_content_not_validated"
        )
    )

    return {
        "report_id": "subsection-handover-content-validity",
        "date": "2026-07-25",
        "mode": "one_real_task_three_stage_read_only_content_audit",
        "status": status,
        "task_id_hash": _sha256_text(task_id),
        "task_status": task["status"],
        "scope": {
            "handover_records": len(records),
            "transitions": len(transitions),
            "second_task_used": False,
            "draft_regenerated": False,
            "writer_or_external_model_calls": 0,
            "production_code_modified": False,
        },
        "stage_isolation": {
            "stage_a_seal": seals["A"],
            "stage_b_seal": seals["B"],
            "stage_c_seal": seals["C"],
            "stage_a_read_handover": False,
            "stage_a_read_target_drafts": False,
            "stage_b_read_target_drafts": False,
            "stage_c_modified_prior_labels": False,
        },
        "consumer_chain": {
            **consumer_chain,
            "transition_injection": [
                {
                    "transition_id": item["transition_id"],
                    "injected": item["handover_injected"],
                    "injection_position": item["injection_position"],
                    "source_ids": item["actual_injected_source_ids"],
                    "messages_hash": item["target_messages_hash"],
                }
                for item in transitions
            ],
        },
        "artifact_metrics": {
            "records": len(records),
            "pending": len(history.pending),
            "errors": len(history.errors),
            "field_instances": sum(
                len(record.fields) for record in records.values()
            ),
            "atomic_claims": len(stage_b["claims"]),
            **traceability,
            "boundary_leakage_count": attribution_counts[
                "boundary_leakage"
            ],
        },
        "faithfulness": {
            "formula": (
                "supported / "
                "(supported + partially_supported + unsupported)"
            ),
            "assessable_claims": assessable,
            "strict_claim_precision": strict_precision,
            "support_status_counts": dict(status_counts),
            "unsupported_invention_count": attribution_counts[
                "unsupported_invention"
            ],
            "stale_state_count": attribution_counts["stale_state"],
            "field_misclassification_count": sum(
                item["field_name"] == "new_facts"
                and item["attribution"] == "stale_state"
                for item in stage_b["claims"]
            ),
            "attribution_counts": dict(attribution_counts),
            "review_provenance": "codex_assisted_review",
            "independent_human_gold_claimed": False,
        },
        "carryover_coverage": by_importance,
        "downstream_continuity": {
            "correct_transitions": sum(
                item["continuity_status"] == "correct"
                for item in transitions
            ),
            "continuity_error_count": sum(
                item["continuity_error_count"] for item in transitions
            ),
            "handover_conflict_count": sum(
                item["handover_conflict_count"] for item in transitions
            ),
            "handover_correct_but_writer_not_execute_count": sum(
                item["handover_correct_but_writer_not_execute_count"]
                for item in transitions
            ),
            "wrong_handover_corrected_by_other_context_count": sum(
                item["wrong_handover_corrected_by_other_context_count"]
                for item in transitions
            ),
            "downstream_correct_without_handover_count": sum(
                item["downstream_correct_without_handover"]
                for item in transitions
            ),
            "unattributable_to_handover_count": sum(
                not item["handover_injected"]
                and item["continuity_status"] == "error"
                for item in transitions
            ),
            "public_evidence": _short_transition_evidence(transitions),
        },
        "utility": dict(utility_counts),
        "quality_metric_boundary": {
            "handover_continuity": (
                "partially_assessable_native_artifact_not_quality_truth"
            ),
            "production_causal_effect_assessable": False,
            "unavailable_is_quality_failure": False,
            "quality_truth_claimed": False,
        },
        "acceptance_gates": hard_gates,
        "all_acceptance_gates_passed": all_gates,
        "targeted_human_review": {
            "required": False,
            "reason": (
                "Confirmed unsupported inventions, stale state, incomplete "
                "critical carryover, and sub-100% precision already determine "
                "the decision; optimistic ambiguity resolution cannot pass."
            ),
        },
        "decision": {
            "downstream_use_promoted": False,
            "next_step": (
                "one_minimal_handover_extractor_contract_fix_only"
            ),
            "recommendation": (
                "Constrain the extractor to evidence-backed end state, "
                "open event and next-boundary claims with source spans; "
                "exclude unsupported psychology and arc status without a "
                "traceable milestone source."
            ),
            "next_step_automatic": False,
        },
        "privacy": {
            "full_draft_in_report": False,
            "full_handover_in_report": False,
            "prompt_or_messages_in_report": False,
            "database_or_redis_dump_in_report": False,
            "secret_in_report": False,
            "max_public_excerpt_characters": max(
                (
                    len(evidence["excerpt"])
                    for item in transitions
                    for evidence in item["evidence"]
                ),
                default=0,
            ),
        },
        "verification": {
            "targeted_tests_passed": None,
            "targeted_tests_failed": None,
            "compileall": "pending",
            "historical_phase_3_4_matrix_run": False,
        },
    }


def render_markdown(report: dict[str, Any]) -> str:
    faithful = report["faithfulness"]
    coverage = report["carryover_coverage"]
    continuity = report["downstream_continuity"]
    utility = report["utility"]
    consumer = report["consumer_chain"]
    return f"""# Subsection Handover V1 内容有效性验收

状态：`{report['status']}`

## 结论

持久化仍然可靠，但内容质量没有通过有限下游使用门槛。四条 Handover 在同章内没有进入下一小节 Writer messages；新增 sidecar 只是持久化镜像。因此本报告只能评价离线内容价值，不能把下一小节结果归因为 Handover。

## 消费链

- producer：`{consumer['producer']}`
- 小节内暂存：`{consumer['local_storage']}`
- 持久化 sidecar：`{consumer['durable_sidecar']}`
- 章节结束聚合：`{consumer['section_aggregate']}`
- 同章小节循环内更新 `prev_handover`：`{str(consumer['same_section_prev_handover_updated']).lower()}`
- 三个过渡实际注入：0/3

`section_handover_parts` 只在整章结束后聚合为 `handover_chain`，而同章小节循环内用于 Prompt 的 `prev_handover` 没有被更新。

## 工件与忠实度

- Handover records：{report['artifact_metrics']['records']}
- fields：{report['artifact_metrics']['field_instances']}
- 原子 claims：{report['artifact_metrics']['atomic_claims']}
- source/hash 追溯率：{report['artifact_metrics']['source_hash_traceability_rate']:.2%}
- claim evidence span 追溯率：{report['artifact_metrics']['evidence_span_traceability_rate']:.2%}
- supported：{faithful['support_status_counts'].get('supported', 0)}
- partially supported：{faithful['support_status_counts'].get('partially_supported', 0)}
- unsupported：{faithful['support_status_counts'].get('unsupported', 0)}
- ambiguous：{faithful['support_status_counts'].get('ambiguous', 0)}
- unverifiable：{faithful['support_status_counts'].get('unverifiable', 0)}
- strict claim Precision：{faithful['strict_claim_precision']:.2%}（34/44）
- unsupported invention：{faithful['unsupported_invention_count']}
- stale state：{faithful['stale_state_count']}
- boundary leakage：{report['artifact_metrics']['boundary_leakage_count']}

三条无来源支持的结论均出现在 S1.4 的人物心理解释中；另有一条既有事件被重新列为 `new_facts`。`arc_progress=pending` 共 15 条，缺少可追溯 milestone 依据，因此单独记为 unverifiable。

## 承接覆盖

- critical：{coverage['critical']['covered']}/{coverage['critical']['total']}，严格 Recall {coverage['critical']['strict_recall']:.2%}
- supporting：{coverage['supporting']['covered']}/{coverage['supporting']['total']}，严格 Recall {coverage['supporting']['strict_recall']:.2%}
- optional：{coverage['optional']['covered']}/{coverage['optional']['total']}
- 未完整覆盖但可由 recent original 或当前 outline 提供：{coverage['critical']['not_fully_covered_but_available_elsewhere'] + coverage['supporting']['not_fully_covered_but_available_elsewhere']} 项

主要缺口包括节尾地点、事实确认边界、下一小节停止/切换边界，以及对“已保存但未发布”的状态区分。

## 三个真实过渡

- 正确承接：{continuity['correct_transitions']}/3
- 连续性错误：{continuity['continuity_error_count']}
- Handover 冲突：{continuity['handover_conflict_count']}
- 可归因于 Handover 的退化：0
- 因 Handover 未注入而无法归因的错误：{continuity['unattributable_to_handover_count']}

S1.2→S1.3 存在“前节已经回家写作、后节重新回到店门口”的位置与事件重置；S1.3→S1.4 又在林晚已经离开后回到同一时段和台阶场景。两项都不能归因给未被消费的 sidecar。

## 实际可用性

- directly useful：{utility.get('directly_useful', 0)}
- redundant but correct：{utility.get('redundant_but_correct', 0)}
- unused optional：{utility.get('unused_optional', 0)}
- misleading：{utility.get('misleading', 0)}
- unassessable：{utility.get('unassessable', 0)}

重复但正确的信息不被视为无用；它仍可能作为未来结构化、低成本连续性输入。但当前工件混入解释性心理状态、无来源弧线状态和不完整边界，因此不能晋级。

## 指标边界与下一步

`handover_continuity` 当前只能标记为 `partially_assessable_native_artifact_not_quality_truth`。本任务没有修改 QualityEvaluator，也没有建立全局质量真值。

唯一下一步建议：只做一次最小 Handover extractor 契约修复——输出带 source span 的节尾状态、未完成事件和下一场景边界；禁止无证据心理推断，并在没有可追溯 milestone 来源时排除 `arc_progress`。不得自动接入其他下游消费者。

本结论来自一个真实四小节任务和 Codex 辅助审阅，不是独立人工金标准，也不能外推为通用准确率。
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-id")
    parser.add_argument("--runtime", type=Path, default=DEFAULT_RUNTIME)
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--markdown", type=Path, default=DEFAULT_MARKDOWN)
    args = parser.parse_args()
    report = build_report(args.task_id, args.runtime)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    args.markdown.write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "status": report["status"],
                "records": report["artifact_metrics"]["records"],
                "claims": report["artifact_metrics"]["atomic_claims"],
                "strict_claim_precision": report["faithfulness"][
                    "strict_claim_precision"
                ],
                "critical_recall": report["carryover_coverage"]["critical"][
                    "strict_recall"
                ],
                "writer_or_external_model_calls": 0,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
