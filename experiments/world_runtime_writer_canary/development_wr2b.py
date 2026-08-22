"""Evaluate WR2-B on the visible WR2-A adversarial development partition."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from experiments.world_runtime_writer_canary import adversarial_experiment as wr1r
from experiments.world_runtime_writer_canary.delta_shadow_wr2b import ProposedChangeV2, validate_delta_v2
from experiments.world_runtime_writer_canary.layered_extractor_wr2b import extract_typed_delta_v2


ROOT = Path(__file__).resolve().parents[2]
DEVELOPMENT_FIXTURE = ROOT / "experiments/world_runtime_writer_canary/fixtures/wr2a_extractor_adversarial_v1.json"
DEFAULT_REPORT = ROOT / "reports/world-runtime-wr2b-development-result-2026-08-04.json"


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def _signature(change: ProposedChangeV2 | dict[str, Any]) -> tuple[str, str, str, str, str]:
    payload = change.model_dump(mode="json") if isinstance(change, ProposedChangeV2) else change
    return (
        payload["change_type"], payload["subject"], payload["predicate"],
        json.dumps(payload.get("after_value"), ensure_ascii=False, sort_keys=True), payload["mechanism"],
    )


def evaluate_development() -> dict[str, Any]:
    fixture = _read(DEVELOPMENT_FIXTURE)
    _, states, _ = wr1r._artifacts()
    totals = defaultdict(int)
    class_totals: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    cases = []
    for case in fixture["cases"]:
        state = states[case["state_variant"]]
        delta, clauses, candidates = extract_typed_delta_v2(
            text=case["text"], sample_id=case["case_id"], scene_id=case["scene_id"],
            state_variant=case["state_variant"], base_revision=state.revision,
        )
        validation = validate_delta_v2(delta)
        outcomes = {item.change_id: item.outcome for item in validation.items}
        actual_by_signature: dict[tuple[str, str, str, str, str], list[ProposedChangeV2]] = defaultdict(list)
        for change in delta.changes:
            actual_by_signature[_signature(change)].append(change)
        matched = outcome_correct = invalid_total = invalid_correct = 0
        expected_details = []
        for expected in case["changes"]:
            signature = _signature(expected)
            candidate_changes = actual_by_signature.get(signature, [])
            actual = candidate_changes.pop(0) if candidate_changes else None
            actual_outcome = outcomes.get(actual.change_id) if actual else None
            signature_match = actual is not None
            validation_match = signature_match and actual_outcome == expected["expected_validation"]
            matched += int(signature_match)
            outcome_correct += int(validation_match)
            class_totals[case["class"]][0] += 1
            class_totals[case["class"]][1] += int(signature_match)
            if expected["expected_validation"] == "invalid":
                invalid_total += 1
                invalid_correct += int(validation_match)
            expected_details.append({
                "signature": list(signature), "expected_validation": expected["expected_validation"],
                "extracted": signature_match, "actual_validation": actual_outcome,
                "validation_correct": validation_match,
            })
        unmatched = [change.change_id for values in actual_by_signature.values() for change in values]
        expected_count = len(case["changes"])
        totals["expected"] += expected_count
        totals["extracted"] += len(delta.changes)
        totals["matched"] += matched
        totals["outcome_correct"] += outcome_correct
        totals["invalid"] += invalid_total
        totals["invalid_correct"] += invalid_correct
        totals["candidate_count"] += len(candidates)
        if expected_count == 0:
            totals["empty"] += 1
            totals["empty_correct"] += int(not delta.changes)
        traceable = delta.output_hash == hashlib.sha256(case["text"].encode("utf-8")).hexdigest() and all(
            case["text"][item.start:item.end] == item.excerpt for item in delta.evidence
        )
        cases.append({
            "case_id": case["case_id"], "class": case["class"], "clause_count": len(clauses),
            "event_candidate_count": len(candidates), "expected_change_count": expected_count,
            "extracted_change_count": len(delta.changes), "matched_change_count": matched,
            "unmatched_actual_change_ids": unmatched, "expected": expected_details,
            "traceability_complete": traceable, "would_commit": validation.would_commit,
            "state_mutated": validation.state_mutated,
        })
    precision = totals["matched"] / totals["extracted"] if totals["extracted"] else 1.0
    recall = totals["matched"] / totals["expected"] if totals["expected"] else 1.0
    outcome_accuracy = totals["outcome_correct"] / totals["matched"] if totals["matched"] else 1.0
    invalid_recall = totals["invalid_correct"] / totals["invalid"] if totals["invalid"] else 1.0
    gates = {
        "development_semantic_precision_complete": precision == 1.0,
        "development_semantic_recall_complete": recall == 1.0,
        "development_validation_accuracy_complete": outcome_accuracy == 1.0,
        "development_invalid_recall_complete": invalid_recall == 1.0,
        "development_empty_deltas_preserved": totals["empty_correct"] == totals["empty"],
        "evidence_traceability_complete": all(item["traceability_complete"] for item in cases),
        "commit_forbidden": all(not item["would_commit"] and not item["state_mutated"] for item in cases),
    }
    passed = all(gates.values())
    return {
        "schema_version": "world-runtime-wr2b-development-audit-v1",
        "status": "development_fit_complete" if passed else "development_fit_incomplete",
        "partition_role": "visible_development_not_holdout",
        "fixture_sha256": hashlib.sha256(DEVELOPMENT_FIXTURE.read_bytes()).hexdigest(),
        "case_count": len(fixture["cases"]), "event_candidate_count": totals["candidate_count"],
        "expected_change_count": totals["expected"], "extracted_change_count": totals["extracted"],
        "matched_change_count": totals["matched"], "semantic_precision": precision,
        "semantic_recall": recall, "matched_validation_accuracy": outcome_accuracy,
        "invalid_transition_recall": invalid_recall, "empty_delta_cases": totals["empty"],
        "empty_delta_correct": totals["empty_correct"],
        "class_metrics": {
            name: {"expected": value[0], "matched": value[1], "recall": value[1] / value[0] if value[0] else None}
            for name, value in sorted(class_totals.items())
        },
        "gates": gates, "development_gate_passed": passed,
        "state_mutations": 0, "commits": 0, "model_calls": 0,
        "production_promotion_eligible": False,
        "next_gate": "sealed_unseen_holdout_not_created",
        "cases": cases,
    }


def run(output_path: Path = DEFAULT_REPORT) -> dict[str, Any]:
    result = evaluate_development()
    _write(output_path, result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    result = run(args.output)
    print(json.dumps({key: result[key] for key in (
        "status", "partition_role", "case_count", "event_candidate_count", "expected_change_count",
        "extracted_change_count", "matched_change_count", "semantic_precision", "semantic_recall",
        "matched_validation_accuracy", "invalid_transition_recall", "empty_delta_correct", "gates",
        "state_mutations", "commits", "model_calls", "next_gate",
    )}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
