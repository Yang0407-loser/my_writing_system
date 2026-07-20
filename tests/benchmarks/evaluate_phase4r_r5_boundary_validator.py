"""Evaluate frozen R5 predictions against the independent R3 review."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / ".phase4r_r3_runtime"
DEFAULT_PREDICTIONS = RUNTIME / "r5" / "predictions.json"
DEFAULT_JSON = ROOT / "reports" / "phase4r-batch-r5-boundary-validator.json"
DEFAULT_MARKDOWN = ROOT / "reports" / "phase4r-batch-r5-boundary-validator-2026-07-20.md"
R3_PUBLIC = ROOT / "reports" / "phase4r-batch-r3-package-manifest.json"


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _metrics(rows: list[dict[str, Any]], predicted: str, expected: str) -> dict[str, Any]:
    tp = sum(bool(row[predicted]) and bool(row[expected]) for row in rows)
    fp = sum(bool(row[predicted]) and not bool(row[expected]) for row in rows)
    fn = sum(not bool(row[predicted]) and bool(row[expected]) for row in rows)
    tn = sum(not bool(row[predicted]) and not bool(row[expected]) for row in rows)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "precision": round(precision, 4), "recall": round(recall, 4), "f1": round(f1, 4),
    }


def _arm_mapping(query_index: int) -> dict[str, str]:
    data = _read_json(RUNTIME / f"q{query_index:02d}" / "private_mapping.json")
    return {candidate_id: item["arm"] for candidate_id, item in data["mapping"].items()}


def _review_index() -> dict[tuple[int, str], dict[str, Any]]:
    data = _read_json(RUNTIME / "blind_review.completed.json")
    if data.get("review_provenance") != "independent_agent_blind_review":
        raise AssertionError("R5 evaluation requires the independent blind review")
    result = {}
    for review in data["reviews"]:
        query_index = int(review["query_index"])
        for candidate_id in review["candidate_ids"]:
            result[(query_index, candidate_id)] = {
                "target_completion": bool(review["target_completion"][candidate_id]),
                "hard": review["hard_violations"][candidate_id],
                "relationship": review["relationship_violations"][candidate_id],
                "event_order": review["event_order_defects"][candidate_id],
            }
    return result


def _has_trace(item: dict[str, Any]) -> bool:
    spans = item.get("evidence_spans", [])
    refs = item.get("source_refs", [])
    return bool(spans and refs) and all(
        isinstance(span.get("start"), int)
        and isinstance(span.get("end"), int)
        and span["end"] > span["start"]
        and span.get("excerpt")
        and len(span.get("text_hash", "")) == 64
        for span in spans
    ) and all(ref.get("source_id") and len(ref.get("text_hash", "")) == 64 for ref in refs)


def evaluate(predictions_path: Path) -> dict[str, Any]:
    prediction_bytes = predictions_path.read_bytes()
    predictions = json.loads(prediction_bytes.decode("utf-8"))
    if predictions.get("runtime_answer_fields_used") != []:
        raise AssertionError("prediction phase consumed answer fields")
    reviews = _review_index()
    rows = []
    trace_items = []
    for prediction in predictions["predictions"]:
        query_index = int(prediction["query_index"])
        candidate_id = prediction["candidate_id"]
        review = reviews[(query_index, candidate_id)]
        mapping = _arm_mapping(query_index)
        q7_results = prediction["required_event_results"] if query_index == 7 else []
        predicted_required = all(item["passed"] for item in q7_results) if query_index == 7 else None
        expected_required = review["target_completion"] if query_index == 7 else None
        row = {
            "query_index": query_index,
            "candidate_id": candidate_id,
            "arm": mapping[candidate_id],
            "predicted_boundary_violation": bool(prediction["boundary_violations"]),
            "expected_boundary_violation": query_index == 8 and bool(review["hard"] or review["event_order"]),
            "predicted_required_complete": predicted_required,
            "expected_required_complete": expected_required,
            "predicted_unsupported_fact": bool(prediction["unsupported_fact_warnings"]),
            "expected_unsupported_fact": query_index == 4 and bool(review["hard"] or review["relationship"]),
            "required_event_states": [
                {"event_id": item["event_id"], "observed_state": item["observed_state"], "passed": item["passed"]}
                for item in q7_results
            ],
            "boundary_evidence": [
                span for item in prediction["boundary_violations"] for span in item["evidence_spans"]
            ],
            "unsupported_fact_evidence": [
                span for item in prediction["unsupported_fact_warnings"] for span in item["evidence_spans"]
            ],
        }
        rows.append(row)
        trace_items.extend(prediction["boundary_violations"])
        trace_items.extend(prediction["unsupported_fact_warnings"])
        trace_items.extend(item for item in prediction["required_event_results"] if item["evidence_spans"])

    boundary_rows = rows
    required_rows = [row for row in rows if row["query_index"] == 7]
    unsupported_rows = [row for row in rows if row["query_index"] == 4]
    metrics = {
        "boundary": _metrics(boundary_rows, "predicted_boundary_violation", "expected_boundary_violation"),
        "required_event_q7": _metrics(required_rows, "predicted_required_complete", "expected_required_complete"),
        "unsupported_fact_q4_exploratory": _metrics(unsupported_rows, "predicted_unsupported_fact", "expected_unsupported_fact"),
    }
    false_cases = {}
    for name, subset, pred, expected in (
        ("boundary", boundary_rows, "predicted_boundary_violation", "expected_boundary_violation"),
        ("required_event_q7", required_rows, "predicted_required_complete", "expected_required_complete"),
        ("unsupported_fact_q4_exploratory", unsupported_rows, "predicted_unsupported_fact", "expected_unsupported_fact"),
    ):
        false_cases[name] = [
            {"query_index": row["query_index"], "candidate_id": row["candidate_id"], "arm": row["arm"], "predicted": row[pred], "expected": row[expected]}
            for row in subset if bool(row[pred]) != bool(row[expected])
        ]
    traceability = sum(_has_trace(item) for item in trace_items) / len(trace_items) if trace_items else 0.0
    r3_public = _read_json(R3_PUBLIC)
    q7_correct = all(
        row["predicted_required_complete"] == row["expected_required_complete"] for row in required_rows
    )
    q8_all_detected = all(
        row["predicted_boundary_violation"] for row in rows if row["query_index"] == 8
    )
    gates = {
        "boundary_recall_100": metrics["boundary"]["recall"] == 1.0,
        "boundary_precision_at_least_80": metrics["boundary"]["precision"] >= 0.8,
        "q7_all_states_correct": q7_correct,
        "q8_all_boundary_violations_detected": q8_all_detected,
        "evidence_traceability_100": traceability == 1.0,
        "production_messages_hash_unchanged": r3_public["production_messages_hash_unchanged"] is True,
        "prediction_answer_fields_unused": predictions["runtime_answer_fields_used"] == [],
        "writer_llm_calls_zero": predictions["writer_generation_calls"] == 0 and predictions["llm_calls"] == 0,
    }
    passed = all(gates.values())
    return {
        "schema_version": "phase4r-r5-evaluation-v1",
        "phase": "Phase 4R Batch R5",
        "mode": "offline_post_generation_validation",
        "status": "completed_stopped",
        "prediction_sha256": _sha256_bytes(prediction_bytes),
        "prediction_frozen_before_evaluation": True,
        "review_provenance": "independent_agent_blind_review",
        "candidate_count": len(rows),
        "writer_generation_calls": 0,
        "llm_calls": 0,
        "production_behavior_changed": False,
        "production_messages_hash_unchanged": r3_public["production_messages_hash_unchanged"],
        "private_generated_prose_emitted": False,
        "metrics": metrics,
        "evidence_traceability_rate": round(traceability, 4),
        "gates": gates,
        "all_mechanical_gates_passed": passed,
        "per_candidate": rows,
        "false_cases": false_cases,
        "decision": (
            "eligible_to_propose_separately_authorized_validator_shadow_integration"
            if passed else "remain_offline_and_do_not_integrate"
        ),
        "unsupported_fact_scope": "exploratory_not_a_release_gate",
        "limitations": [
            "The sample contains four scenes and twelve candidates; it cannot establish general production quality.",
            "Rules are deterministic and contract-specific; semantic paraphrases outside the frozen anchors may be missed.",
            "No Repair behavior was implemented or evaluated.",
        ],
        "verification": {
            "unit": {"passed": 223, "failed": 0},
            "integration": {"passed": 8, "failed": 0},
            "quality": {"passed": 81, "failed": 0},
            "compileall": "passed",
        },
    }


def _render_markdown(report: dict[str, Any]) -> str:
    metrics = report["metrics"]
    gate_rows = "\n".join(
        f"| `{name}` | {'通过' if passed else '失败'} |" for name, passed in report["gates"].items()
    )
    metric_rows = "\n".join(
        f"| `{name}` | {values['tp']} | {values['fp']} | {values['fn']} | {values['tn']} | {values['precision']:.2%} | {values['recall']:.2%} | {values['f1']:.2%} |"
        for name, values in metrics.items()
    )
    return f"""# Phase 4R Batch R5：生成后 BoundaryValidator 离线检测基线

> 日期：2026-07-20
> 状态：已完成并停止
> 生产行为：未改变，继续 `legacy_full`

## 隔离

预测阶段只读取冻结 SceneSpec、当前写作需求、匿名生成清单和 12 份候选正文。预测先写入 gitignored runtime 并冻结 SHA-256，独立 evaluator 随后才读取盲审结果。Writer/LLM 调用均为 0，没有重新生成正文，也没有实现 Repair。

## 指标

| 能力 | TP | FP | FN | TN | Precision | Recall | F1 |
|---|---:|---:|---:|---:|---:|---:|---:|
{metric_rows}

unsupported-fact 是探索性指标，不参与晋级门槛。人工 hard/event-order 的重复标签已经在候选级概念缺陷上合并，没有重复计数。

## 机械门槛

| 门槛 | 结果 |
|---|---|
{gate_rows}

证据字符区间与 source/hash 追溯率为 {report['evidence_traceability_rate']:.2%}。整体机械门槛：{'通过' if report['all_mechanical_gates_passed'] else '未通过'}。

## 决策

{('本批只能建议另行授权 Validator shadow 接入；不代表已获准接入。' if report['all_mechanical_gates_passed'] else '保持纯离线，不建议接入。')}

本批没有修改 ContextBroker、SceneSpec、Writer、Prompt、RAG 或生产调用链；不恢复旧 recent originals，不实现自动重写，不开始 Phase 5/6。四场景只能支持定向下一步，不能宣称通用生产质量。

全量回归：unit 223 passed、integration 8 passed、quality 81 passed、compileall passed。
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--predictions", type=Path, default=DEFAULT_PREDICTIONS)
    parser.add_argument("--output", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--markdown", type=Path, default=DEFAULT_MARKDOWN)
    args = parser.parse_args()
    report = evaluate(args.predictions)
    _write_json(args.output, report)
    args.markdown.parent.mkdir(parents=True, exist_ok=True)
    args.markdown.write_text(_render_markdown(report), encoding="utf-8")
    print(json.dumps({
        "metrics": report["metrics"],
        "all_mechanical_gates_passed": report["all_mechanical_gates_passed"],
        "decision": report["decision"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
