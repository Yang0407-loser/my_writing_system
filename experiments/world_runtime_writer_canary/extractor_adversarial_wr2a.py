"""One-shot WR2-A adversarial diagnostic for the frozen automatic extractor.

This runner locks the challenge fixture, extractor, and validator by SHA-256.
It executes extraction exactly once, writes audit-only artifacts, and cannot
promote production or commit canonical state.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from experiments.world_runtime_writer_canary import adversarial_experiment as wr1r
from experiments.world_runtime_writer_canary import delta_shadow_wr2a as wr2a
from experiments.world_runtime_writer_canary import extractor_shadow_wr2a as extractor


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "experiments/world_runtime_writer_canary/fixtures/wr2a_extractor_adversarial_v1.json"
LOCK = ROOT / "experiments/world_runtime_writer_canary/fixtures/wr2a_extractor_adversarial_lock_v1.json"
RUNTIME = ROOT / ".world_runtime_wr2a_extractor_adversarial_runtime"
LEDGER = RUNTIME / "attempt-ledger.json"
PREFLIGHT = RUNTIME / "preflight-audit.json"
RESULT = ROOT / "reports/world-runtime-wr2a-extractor-adversarial-result-2026-08-04.json"
SCHEMA_VERSION = "world-runtime-extractor-adversarial-audit-wr2a-v1"


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_lock() -> dict[str, Any]:
    lock = _read(LOCK)
    actual = {
        "fixture_sha256": _hash(FIXTURE),
        "extractor_sha256": _hash(ROOT / lock["extractor_path"]),
        "validator_sha256": _hash(ROOT / lock["validator_path"]),
    }
    matched = all(actual[key] == lock[key] for key in actual)
    return {
        "ready": matched,
        "locked": {key: lock[key] for key in actual},
        "actual": actual,
        "hashes_matched": matched,
        "run_policy": lock["run_policy"],
    }


def _signature(change: wr2a.ProposedChange | dict[str, Any]) -> tuple[str, str, str, str, str]:
    payload = change.model_dump(mode="json") if isinstance(change, wr2a.ProposedChange) else change
    return (
        payload["change_type"],
        payload["subject"],
        payload["predicate"],
        json.dumps(payload.get("after_value"), ensure_ascii=False, sort_keys=True),
        payload["mechanism"],
    )


def evaluate_frozen_partition() -> dict[str, Any]:
    fixture = _read(FIXTURE)
    _, states, _ = wr1r._artifacts()
    case_results = []
    expected_total = extracted_total = matched_total = outcome_correct = 0
    invalid_total = invalid_correct = empty_total = empty_correct = 0
    control_total = control_matched = 0
    class_counts: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    expected_types: set[str] = set()
    extracted_types: set[str] = set()
    unsupported_expected_types: set[str] = set()

    for case in fixture["cases"]:
        state = states[case["state_variant"]]
        delta = extractor.extract_typed_delta(
            text=case["text"],
            sample_id=case["case_id"],
            scene_id=case["scene_id"],
            state_variant=case["state_variant"],
            base_revision=state.revision,
        )
        validation = wr2a.validate_delta(delta)
        outcomes = {item.change_id: item.outcome for item in validation.items}
        actual_by_signature: dict[tuple[str, str, str, str, str], list[wr2a.ProposedChange]] = defaultdict(list)
        for change in delta.changes:
            actual_by_signature[_signature(change)].append(change)
            extracted_types.add(change.change_type)

        matched = 0
        correct_outcomes = 0
        invalid_case_total = 0
        invalid_case_correct = 0
        expected_details = []
        for expected in case["changes"]:
            signature = _signature(expected)
            expected_types.add(expected["change_type"])
            class_counts[case["class"]][0] += 1
            if expected["change_type"] not in wr2a.ChangeType.__args__:
                unsupported_expected_types.add(expected["change_type"])
            candidates = actual_by_signature.get(signature, [])
            actual = candidates.pop(0) if candidates else None
            actual_outcome = outcomes.get(actual.change_id) if actual else None
            is_match = actual is not None
            outcome_match = is_match and actual_outcome == expected["expected_validation"]
            matched += int(is_match)
            correct_outcomes += int(outcome_match)
            class_counts[case["class"]][1] += int(is_match)
            if expected["expected_validation"] == "invalid":
                invalid_case_total += 1
                invalid_case_correct += int(outcome_match)
            expected_details.append(
                {
                    "signature": list(signature),
                    "expected_validation": expected["expected_validation"],
                    "extracted": is_match,
                    "actual_validation": actual_outcome,
                    "validation_correct": outcome_match,
                }
            )

        unmatched_actual = [
            change.change_id
            for changes in actual_by_signature.values()
            for change in changes
        ]
        expected_count = len(case["changes"])
        expected_total += expected_count
        extracted_total += len(delta.changes)
        matched_total += matched
        outcome_correct += correct_outcomes
        invalid_total += invalid_case_total
        invalid_correct += invalid_case_correct
        if not expected_count:
            empty_total += 1
            empty_correct += int(not delta.changes)
        if case["class"] == "control":
            control_total += expected_count
            control_matched += matched

        text_hash = hashlib.sha256(case["text"].encode("utf-8")).hexdigest()
        traceable = delta.output_hash == text_hash and all(
            case["text"][item.start:item.end] == item.excerpt for item in delta.evidence
        )
        case_results.append(
            {
                "case_id": case["case_id"],
                "class": case["class"],
                "expected_change_count": expected_count,
                "extracted_change_count": len(delta.changes),
                "matched_change_count": matched,
                "unmatched_actual_change_ids": unmatched_actual,
                "expected": expected_details,
                "traceability_complete": traceable,
                "would_commit": validation.would_commit,
                "state_mutated": validation.state_mutated,
            }
        )

    precision = matched_total / extracted_total if extracted_total else 1.0
    recall = matched_total / expected_total if expected_total else 1.0
    validation_accuracy = outcome_correct / matched_total if matched_total else 1.0
    invalid_recall = invalid_correct / invalid_total if invalid_total else 1.0
    class_metrics = {
        name: {
            "expected_change_count": counts[0],
            "matched_change_count": counts[1],
            "recall": counts[1] / counts[0] if counts[0] else None,
        }
        for name, counts in sorted(class_counts.items())
    }
    gates = {
        "control_recall_complete": control_matched == control_total,
        "semantic_precision_at_least_0_90": precision >= 0.90,
        "semantic_recall_at_least_0_90": recall >= 0.90,
        "invalid_transition_recall_complete": invalid_recall == 1.0,
        "matched_validation_accuracy_complete": validation_accuracy == 1.0,
        "empty_delta_cases_preserved": empty_correct == empty_total,
        "evidence_traceability_complete": all(item["traceability_complete"] for item in case_results),
        "commit_forbidden": all(not item["would_commit"] and not item["state_mutated"] for item in case_results),
    }
    passed = all(gates.values())
    return {
        "case_count": len(fixture["cases"]),
        "expected_change_count": expected_total,
        "extracted_change_count": extracted_total,
        "matched_change_count": matched_total,
        "semantic_precision": precision,
        "semantic_recall": recall,
        "matched_validation_accuracy": validation_accuracy,
        "invalid_transition_expected": invalid_total,
        "invalid_transition_correct": invalid_correct,
        "invalid_transition_recall": invalid_recall,
        "empty_delta_cases": empty_total,
        "empty_delta_correct": empty_correct,
        "control_expected_changes": control_total,
        "control_matched_changes": control_matched,
        "expected_change_types": sorted(expected_types),
        "extracted_change_types": sorted(extracted_types),
        "unsupported_expected_change_types": sorted(unsupported_expected_types),
        "class_metrics": class_metrics,
        "gates": gates,
        "diagnostic_gate_passed": passed,
        "cases": case_results,
    }


def run_once() -> dict[str, Any]:
    preflight = verify_lock()
    _write(PREFLIGHT, preflight)
    if not preflight["ready"]:
        raise ValueError("WR2-A adversarial preflight hash mismatch")
    if LEDGER.exists():
        prior = _read(LEDGER)
        if prior.get("attempt_count_total", 0) >= 1:
            raise RuntimeError("WR2-A adversarial extractor run already consumed")
    ledger = {
        "schema_version": SCHEMA_VERSION,
        "status": "started",
        "attempt_count_total": 1,
        "transport_retries": 0,
        "model_calls": 0,
    }
    _write(LEDGER, ledger)
    evaluation = evaluate_frozen_partition()
    passed = evaluation["diagnostic_gate_passed"]
    result = {
        "schema_version": SCHEMA_VERSION,
        "status": "adversarial_extractor_diagnostic_passed" if passed else "adversarial_extractor_diagnostic_failed",
        "decision": "hold_scope_generalization_failed" if not passed else "shadow_gate_passed_production_still_forbidden",
        "preflight": preflight,
        "evaluation": evaluation,
        "execution": {
            "command_executed_exactly_once": True,
            "attempt_count_total": 1,
            "transport_retries": 0,
            "model_calls": 0,
            "same_partition_tuning": False,
        },
        "state_mutations": 0,
        "commits": 0,
        "production_writer_changed": False,
        "production_promotion_eligible": False,
        "next_gate": "freeze_training_partition_then_repair_without_reusing_adversarial_v1_as_holdout" if not passed else "separately_authorized_state_commit_canary",
    }
    _write(RESULT, result)
    ledger["status"] = "completed"
    ledger["result_sha256"] = _hash(RESULT)
    _write(LEDGER, ledger)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit", action="store_true")
    parser.add_argument("--run-once", action="store_true")
    args = parser.parse_args()
    if args.audit == args.run_once:
        parser.error("choose exactly one of --audit or --run-once")
    result = verify_lock() if args.audit else run_once()
    if args.run_once:
        evaluation = result["evaluation"]
        result = {
            "status": result["status"],
            "decision": result["decision"],
            "case_count": evaluation["case_count"],
            "expected_change_count": evaluation["expected_change_count"],
            "extracted_change_count": evaluation["extracted_change_count"],
            "matched_change_count": evaluation["matched_change_count"],
            "semantic_precision": evaluation["semantic_precision"],
            "semantic_recall": evaluation["semantic_recall"],
            "invalid_transition_recall": evaluation["invalid_transition_recall"],
            "empty_delta_correct": f"{evaluation['empty_delta_correct']}/{evaluation['empty_delta_cases']}",
            "unsupported_expected_change_types": evaluation["unsupported_expected_change_types"],
            "gates": evaluation["gates"],
            "state_mutations": result["state_mutations"],
            "commits": result["commits"],
            "model_calls": result["execution"]["model_calls"],
            "next_gate": result["next_gate"],
        }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
