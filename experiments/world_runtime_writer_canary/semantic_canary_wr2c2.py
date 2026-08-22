"""WR2-C2 Development-v2 semantic canary builder and evaluator."""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from app.config import settings
from app.utils.llm_client import estimate_messages_tokens
from experiments.world_runtime_writer_canary import adversarial_experiment as wr1r
from experiments.world_runtime_writer_canary.delta_shadow_wr2b import validate_delta_v2
from experiments.world_runtime_writer_canary.semantic_extractor_wr2c import RawSemanticResponse
from experiments.world_runtime_writer_canary.semantic_extractor_wr2c2 import (
    build_messages,
    parse_semantic_response,
)


ROOT = Path(__file__).resolve().parents[2]
SOURCE = Path(__file__).resolve()
EXTRACTOR = ROOT / "experiments/world_runtime_writer_canary/semantic_extractor_wr2c2.py"
VALIDATOR = ROOT / "experiments/world_runtime_writer_canary/delta_shadow_wr2b.py"
FIXTURE = ROOT / "experiments/world_runtime_writer_canary/fixtures/wr2c2_semantic_development_v2.json"
EXTERNAL_RUNNER = ROOT / "experiments/world_runtime_writer_canary/external_runner_wr2c2.py"
DEFAULT_OUTPUT = ROOT / ".world_runtime_wr2c2_semantic_canary_runtime"
PROVIDER = {"temperature": 0.0, "max_tokens": 2800, "transport_retries": 0, "json_mode": True}
GATES = {
    "sample_count": 20,
    "minimum_semantic_precision": 0.90,
    "minimum_semantic_recall": 0.90,
    "minimum_invalid_transition_recall": 1.0,
    "minimum_matched_validation_accuracy": 1.0,
    "minimum_empty_delta_correctness": 1.0,
    "maximum_parser_failure_count": 0,
    "maximum_unsupported_accepted_change_count": 0,
    "minimum_evidence_traceability": 1.0,
    "maximum_mean_elapsed_ms": 60000,
}


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _digest(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _signature(change: dict[str, Any]) -> tuple[str, str, str, str, str]:
    return (
        change["change_type"],
        change["subject"],
        change["predicate"],
        json.dumps(change.get("after_value"), ensure_ascii=False, sort_keys=True),
        change["mechanism"],
    )


def build(output_dir: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    if (output_dir / "attempt-ledger.json").exists():
        raise FileExistsError("WR2-C2 attempt ledger exists; refusing rebuild")
    if not EXTERNAL_RUNNER.exists():
        raise FileNotFoundError("WR2-C2 external runner missing")
    fixture = _read(FIXTURE)
    cases = fixture["cases"]
    _, states, _ = wr1r._artifacts()
    samples = []
    for ordinal, case in enumerate(cases, 1):
        state = states[case["state_variant"]]
        messages = build_messages(text=case["text"], state_variant=case["state_variant"])
        sample_id = f"WR2C2-{ordinal:02d}"
        samples.append({
            "sample_id": sample_id,
            "source_case_id": case["case_id"],
            "scene_id": case["scene_id"],
            "state_variant": case["state_variant"],
            "base_revision": state.revision,
            "text": case["text"],
            "text_sha256": hashlib.sha256(case["text"].encode("utf-8")).hexdigest(),
            "messages": messages,
            "expected_changes": case["changes"],
            "provider": PROVIDER,
            "request_hash": _digest({"messages": messages, "provider": PROVIDER}),
        })
    expected = [change for sample in samples for change in sample["expected_changes"]]
    change_types = {change["change_type"] for change in expected}
    if len(samples) != GATES["sample_count"] or len(change_types) != 13:
        raise ValueError("WR2-C2 sample or ontology coverage mismatch")
    manifest = {
        "schema_version": "world-runtime-semantic-canary-wr2c2-manifest-v1",
        "experiment_id": "world-runtime-semantic-extractor-wr2c2-development-v2",
        "partition_role": "visible_development_v2_not_holdout",
        "fixture_sha256": _sha256(FIXTURE),
        "builder_source_sha256": _sha256(SOURCE),
        "semantic_extractor_source_sha256": _sha256(EXTRACTOR),
        "validator_source_sha256": _sha256(VALIDATOR),
        "external_runner_sha256": _sha256(EXTERNAL_RUNNER),
        "response_schema_sha256": _digest(RawSemanticResponse.model_json_schema()),
        "provider_host": urlparse(settings.LLM_BASE_URL).hostname,
        "model": settings.WRITER_LLM_MODEL,
        "gates": GATES,
        "samples": samples,
        "sample_count": len(samples),
        "expected_change_count": len(expected),
        "expected_empty_count": sum(not sample["expected_changes"] for sample in samples),
        "all_13_types_covered": len(change_types) == 13,
        "external_execution_authorized": False,
        "production_behavior_changed": False,
        "state_commit_authorized": False,
        "sealed_holdout_used": False,
        "prior_development_partition_reused": False,
    }
    ledger = {
        "schema_version": "world-runtime-semantic-canary-wr2c2-ledger-v1",
        "samples": {
            sample["sample_id"]: {
                "request_hash": sample["request_hash"],
                "status": "pending",
                "attempt_count": 0,
            }
            for sample in samples
        },
    }
    _write(output_dir / "private/locked-manifest.json", manifest)
    _write(output_dir / "attempt-ledger.json", ledger)
    return manifest


def _assert_integrity(output_dir: Path) -> dict[str, Any]:
    manifest = _read(output_dir / "private/locked-manifest.json")
    checks = {
        "fixture_sha256": _sha256(FIXTURE),
        "builder_source_sha256": _sha256(SOURCE),
        "semantic_extractor_source_sha256": _sha256(EXTRACTOR),
        "validator_source_sha256": _sha256(VALIDATOR),
        "external_runner_sha256": _sha256(EXTERNAL_RUNNER),
        "response_schema_sha256": _digest(RawSemanticResponse.model_json_schema()),
    }
    for field, expected in checks.items():
        if manifest.get(field) != expected:
            raise RuntimeError(f"wr2c2_frozen_drift:{field}")
    return manifest


def audit(output_dir: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    manifest = _assert_integrity(output_dir)
    ledger = _read(output_dir / "attempt-ledger.json")
    pending = sum(item["status"] == "pending" for item in ledger["samples"].values())
    attempts = sum(item["attempt_count"] for item in ledger["samples"].values())
    output_path = output_dir / "private/outputs"
    outputs = list(output_path.glob("*.json")) if output_path.exists() else []
    token_estimates = [estimate_messages_tokens(item["messages"]) for item in manifest["samples"]]
    issues = []
    if pending != manifest["sample_count"] or attempts != 0 or outputs:
        issues.append("ledger_or_outputs_not_pristine")
    if settings.WRITER_WORLD_RUNTIME_MODE != "off":
        issues.append("production_world_runtime_default_not_off")
    if manifest["external_execution_authorized"]:
        issues.append("manifest_must_remain_unauthorized")
    result = {
        "schema_version": "world-runtime-semantic-canary-wr2c2-preflight-v1",
        "status": "ready_zero_call_external_execution_not_authorized" if not issues else "blocked",
        "issues": issues,
        "provider_host": manifest["provider_host"],
        "model": manifest["model"],
        "sample_count": manifest["sample_count"],
        "expected_change_count": manifest["expected_change_count"],
        "expected_empty_count": manifest["expected_empty_count"],
        "all_13_types_covered": manifest["all_13_types_covered"],
        "pending": pending,
        "attempt_count_total": attempts,
        "output_files": len(outputs),
        "estimated_input_tokens_mean": round(statistics.mean(token_estimates), 2),
        "estimated_input_tokens_max": max(token_estimates),
        "transport_retries": 0,
        "provider_calls_executed": 0,
        "production_default": settings.WRITER_WORLD_RUNTIME_MODE,
        "sealed_holdout_used": False,
        "prior_development_partition_reused": False,
        "state_commit_authorized": False,
        "frozen_integrity": not issues,
    }
    _write(output_dir / "pre-generation-audit.json", result)
    return result


def evaluate(output_dir: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    manifest = _assert_integrity(output_dir)
    ledger = _read(output_dir / "attempt-ledger.json")
    keys = (
        "expected", "extracted", "matched", "validation_correct", "invalid_expected",
        "invalid_correct", "empty_expected", "empty_correct", "unsupported_accepted",
        "evidence", "evidence_ok", "parser_failures", "dropped",
    )
    totals = {key: 0 for key in keys}
    items = []
    elapsed_values = []
    for sample in manifest["samples"]:
        entry = ledger["samples"][sample["sample_id"]]
        if entry["status"] != "succeeded" or entry["attempt_count"] != 1:
            raise RuntimeError("WR2-C2 requires exactly one succeeded attempt per sample")
        output_path = output_dir / "private/outputs" / f"{sample['sample_id']}.json"
        response_text = output_path.read_text(encoding="utf-8")
        if hashlib.sha256(response_text.encode("utf-8")).hexdigest() != entry["output_sha256"]:
            raise RuntimeError("WR2-C2 response hash mismatch")
        totals["expected"] += len(sample["expected_changes"])
        totals["empty_expected"] += int(not sample["expected_changes"])
        try:
            artifact = parse_semantic_response(
                text=sample["text"],
                response_text=response_text,
                sample_id=sample["sample_id"],
                scene_id=sample["scene_id"],
                state_variant=sample["state_variant"],
                base_revision=sample["base_revision"],
            )
        except ValueError as exc:
            totals["parser_failures"] += 1
            items.append({"sample_id": sample["sample_id"], "status": "parser_failure", "error": str(exc)})
            continue
        validation = validate_delta_v2(artifact.delta)
        outcomes = {item.change_id: item.outcome for item in validation.items}
        actual_by_signature: dict[tuple[str, str, str, str, str], list[Any]] = {}
        for change in artifact.delta.changes:
            actual_by_signature.setdefault(_signature(change.model_dump(mode="json")), []).append(change)
        matched = validation_correct = invalid_correct = 0
        for expected in sample["expected_changes"]:
            candidates = actual_by_signature.get(_signature(expected), [])
            actual = candidates.pop(0) if candidates else None
            actual_outcome = outcomes.get(actual.change_id) if actual else None
            if actual:
                matched += 1
                validation_correct += int(actual_outcome == expected["expected_validation"])
                if expected["expected_validation"] == "invalid":
                    invalid_correct += int(actual_outcome == "invalid")
        unmatched = [change for values in actual_by_signature.values() for change in values]
        unsupported = sum(outcomes.get(change.change_id) == "valid" for change in unmatched)
        totals["extracted"] += len(artifact.delta.changes)
        totals["matched"] += matched
        totals["validation_correct"] += validation_correct
        totals["invalid_expected"] += sum(c["expected_validation"] == "invalid" for c in sample["expected_changes"])
        totals["invalid_correct"] += invalid_correct
        totals["unsupported_accepted"] += unsupported
        totals["dropped"] += len(artifact.dropped_events)
        if not sample["expected_changes"]:
            totals["empty_correct"] += int(not artifact.delta.changes)
        totals["evidence"] += len(artifact.delta.evidence)
        totals["evidence_ok"] += sum(
            sample["text"][e.start:e.end] == e.excerpt for e in artifact.delta.evidence
        )
        elapsed_values.append(float(entry.get("completion_metadata", {}).get("latency_seconds", 0.0)) * 1000)
        items.append({
            "sample_id": sample["sample_id"], "status": "parsed",
            "raw_event_count": artifact.raw_event_count,
            "projected_event_count": artifact.projected_event_count,
            "dropped_event_count": len(artifact.dropped_events),
            "expected_change_count": len(sample["expected_changes"]),
            "matched_change_count": matched,
            "validation_correct_count": validation_correct,
            "unsupported_accepted_count": unsupported,
            "would_commit": validation.would_commit,
            "state_mutated": validation.state_mutated,
        })
    precision = totals["matched"] / totals["extracted"] if totals["extracted"] else 1.0
    recall = totals["matched"] / totals["expected"] if totals["expected"] else 1.0
    validation_accuracy = totals["validation_correct"] / totals["matched"] if totals["matched"] else 1.0
    invalid_recall = totals["invalid_correct"] / totals["invalid_expected"] if totals["invalid_expected"] else 1.0
    empty_correctness = totals["empty_correct"] / totals["empty_expected"] if totals["empty_expected"] else 1.0
    evidence_rate = totals["evidence_ok"] / totals["evidence"] if totals["evidence"] else 1.0
    mean_elapsed = statistics.mean(elapsed_values) if elapsed_values else 0.0
    gates = {
        "semantic_precision": precision >= GATES["minimum_semantic_precision"],
        "semantic_recall": recall >= GATES["minimum_semantic_recall"],
        "invalid_transition_recall": invalid_recall >= GATES["minimum_invalid_transition_recall"],
        "matched_validation_accuracy": validation_accuracy >= GATES["minimum_matched_validation_accuracy"],
        "empty_delta_correctness": empty_correctness >= GATES["minimum_empty_delta_correctness"],
        "parser_failures": totals["parser_failures"] <= GATES["maximum_parser_failure_count"],
        "unsupported_accepted": totals["unsupported_accepted"] <= GATES["maximum_unsupported_accepted_change_count"],
        "evidence_traceability": evidence_rate >= GATES["minimum_evidence_traceability"],
        "mean_elapsed_ms": mean_elapsed <= GATES["maximum_mean_elapsed_ms"],
        "commit_forbidden": all(not i.get("would_commit", False) and not i.get("state_mutated", False) for i in items),
    }
    passed = all(gates.values())
    result = {
        "schema_version": "world-runtime-semantic-canary-wr2c2-evaluation-v1",
        "partition_role": "visible_development_v2_not_holdout",
        "sample_count": manifest["sample_count"],
        "expected_change_count": totals["expected"],
        "extracted_change_count": totals["extracted"],
        "matched_change_count": totals["matched"],
        "semantic_precision": precision,
        "semantic_recall": recall,
        "matched_validation_accuracy": validation_accuracy,
        "invalid_transition_recall": invalid_recall,
        "empty_delta_correctness": empty_correctness,
        "parser_failure_count": totals["parser_failures"],
        "unsupported_accepted_change_count": totals["unsupported_accepted"],
        "evidence_traceability": evidence_rate,
        "dropped_event_count": totals["dropped"],
        "mean_elapsed_ms": mean_elapsed,
        "gates": gates,
        "development_gate_passed": passed,
        "production_promotion_eligible": False,
        "new_unseen_holdout_authorized": passed,
        "state_mutations": 0,
        "commits": 0,
        "decision": "development_v2_passed_new_unseen_holdout_required" if passed else "hold_semantic_development_v2_failed",
        "items": items,
    }
    _write(output_dir / "evaluation.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("build", "audit", "evaluate"))
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = {"build": build, "audit": audit, "evaluate": evaluate}[args.command](args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
