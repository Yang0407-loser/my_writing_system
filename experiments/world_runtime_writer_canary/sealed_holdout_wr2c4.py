"""WR2-C4 sealed unseen holdout runner.

The holdout fixture is authored by an independent author who did not write the
extractor/projector.  This module seals it (hash lock), builds the frozen
manifest, and permits exactly one external run-once with zero retries.
It never modifies the holdout, never calls a provider twice, and never commits
state.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from app.config import settings
from app.utils.llm_client import estimate_messages_tokens, get_llm_client
from experiments.world_runtime_writer_canary import adversarial_experiment as wr1r
from experiments.world_runtime_writer_canary.delta_shadow_wr2b import validate_delta_v2
from experiments.world_runtime_writer_canary.semantic_canary_wr2c4 import GATES
from experiments.world_runtime_writer_canary.semantic_extractor_wr2c4 import (
    RawJudgmentResponse,
    build_messages,
    parse_semantic_response,
)
from experiments.world_runtime_writer_canary.semantic_projector_wr2c4 import project


ROOT = Path(__file__).resolve().parents[2]
SOURCE = Path(__file__).resolve()
EXTRACTOR = ROOT / "experiments/world_runtime_writer_canary/semantic_extractor_wr2c4.py"
PROJECTOR = ROOT / "experiments/world_runtime_writer_canary/semantic_projector_wr2c4.py"
VALIDATOR = ROOT / "experiments/world_runtime_writer_canary/delta_shadow_wr2b.py"
RUNTIME = ROOT / ".world_runtime_wr2c4_sealed_holdout_runtime"
HOLDOUT_PATH = RUNTIME / "private" / "sealed-holdout-v1.json"
LOCK_PATH = RUNTIME / "holdout-lock.json"
DEFAULT_AUTHORIZATION = RUNTIME / "private" / "external-execution-authorization.json"
DEVELOPMENT_FIXTURES = (
    ROOT / "experiments/world_runtime_writer_canary/fixtures/wr2c2_semantic_development_v2.json",
    ROOT / "experiments/world_runtime_writer_canary/fixtures/wr2c3_semantic_development_v3.json",
    ROOT / "experiments/world_runtime_writer_canary/fixtures/wr2c4_semantic_development_v4.json",
)
WR2B_SEALED_HOLDOUT = ROOT / ".world_runtime_wr2b_sealed_holdout_runtime/private/sealed-holdout-v1.json"


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _sha256_file(path: Path) -> str:
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


def _development_text_hashes() -> set[str]:
    hashes: set[str] = set()
    for fixture_path in DEVELOPMENT_FIXTURES:
        if not fixture_path.exists():
            continue
        fixture = _read_json(fixture_path)
        for case in fixture["cases"]:
            hashes.add(hashlib.sha256(case["text"].encode("utf-8")).hexdigest())
    if WR2B_SEALED_HOLDOUT.exists():
        try:
            holdout = _read_json(WR2B_SEALED_HOLDOUT)
            for case in holdout.get("cases", []):
                hashes.add(hashlib.sha256(case["text"].encode("utf-8")).hexdigest())
        except Exception:
            pass
    return hashes


def _projector_roundtrip(case: dict[str, Any]) -> None:
    _, states, _ = wr1r._artifacts()
    state = states[case["state_variant"]]
    events, _ = project(text=case["text"], state=state, judgments=case["judgments"])
    actual = [
        (
            event.change_type,
            event.subject,
            event.predicate,
            json.dumps(event.after_value, ensure_ascii=False, sort_keys=True),
            event.mechanism,
        )
        for event in events
    ]
    expected = [_signature(change) for change in case["changes"]]
    if actual != expected:
        raise ValueError(
            f"holdout projector roundtrip mismatch in {case['case_id']}: "
            f"actual={actual} expected={expected}"
        )


def validate_holdout(holdout: dict[str, Any]) -> dict[str, Any]:
    cases = holdout.get("cases", [])
    if len(cases) < 20:
        raise ValueError("holdout requires at least 20 cases")
    expected = [change for case in cases for change in case.get("changes", [])]
    change_types = {change["change_type"] for change in expected}
    invalid_count = sum(c["expected_validation"] == "invalid" for c in expected)
    empty_count = sum(not case.get("changes") for case in cases)
    chain_count = sum(
        {"resignation_acknowledgement", "employment_state"} <= {c["change_type"] for c in case.get("changes", [])}
        for case in cases
    )
    has_unsourced = any(c["change_type"] == "unsourced_project_fact" for c in expected)
    has_clock = any(c["change_type"] == "clock_state" for c in expected)
    has_location = any(c["change_type"] == "location_state" for c in expected)
    if len(change_types) != 13:
        raise ValueError(f"holdout must cover all 13 types, got {len(change_types)}")
    if len(expected) < 16:
        raise ValueError(f"holdout requires at least 16 expected changes, got {len(expected)}")
    if empty_count < 5:
        raise ValueError(f"holdout requires at least 5 expected-empty cases, got {empty_count}")
    if invalid_count < 5:
        raise ValueError(f"holdout requires at least 5 invalid transitions, got {invalid_count}")
    if chain_count < 1:
        raise ValueError("holdout requires at least one ack+employment chain case")
    if not (has_unsourced and has_clock and has_location):
        raise ValueError("holdout must include unsourced_project_fact, clock_state and location_state")
    known = _development_text_hashes()
    for case in cases:
        digest = hashlib.sha256(case["text"].encode("utf-8")).hexdigest()
        if digest in known:
            raise ValueError(f"holdout reuses a development/holdout text: {case['case_id']}")
        _projector_roundtrip(case)
    return {
        "sample_count": len(cases),
        "expected_change_count": len(expected),
        "expected_empty_count": empty_count,
        "invalid_change_count": invalid_count,
        "chain_case_count": chain_count,
        "all_13_types_covered": len(change_types) == 13,
    }


def _source_hashes() -> dict[str, str]:
    return {
        "extractor_sha256": _sha256_file(EXTRACTOR),
        "projector_sha256": _sha256_file(PROJECTOR),
        "validator_sha256": _sha256_file(VALIDATOR),
        "runner_sha256": _sha256_file(SOURCE),
    }


def seal(output_dir: Path = RUNTIME) -> dict[str, Any]:
    if not HOLDOUT_PATH.exists():
        raise FileNotFoundError("sealed holdout file missing; independent author must place it first")
    if LOCK_PATH.exists():
        raise FileExistsError("holdout lock exists; refusing re-seal")
    holdout = _read_json(HOLDOUT_PATH)
    metadata = validate_holdout(holdout)
    lock = {
        "schema_version": "world-runtime-sealed-holdout-wr2c4-lock-v1",
        "experiment_id": "world-runtime-semantic-judgment-wr2c4-sealed-unseen-holdout",
        "partition_role": "sealed_unseen_holdout",
        "holdout_sha256": _sha256_file(HOLDOUT_PATH),
        "sealed_before_run": True,
        "source_hashes": _source_hashes(),
        "coverage": metadata,
    }
    _write_json(LOCK_PATH, lock)
    return lock


def build(output_dir: Path = RUNTIME) -> dict[str, Any]:
    if (output_dir / "attempt-ledger.json").exists():
        raise FileExistsError("holdout ledger exists; refusing rebuild")
    if not LOCK_PATH.exists():
        raise RuntimeError("holdout not sealed; run seal first")
    if not HOLDOUT_PATH.exists():
        raise FileNotFoundError("sealed holdout file missing")
    if _sha256_file(HOLDOUT_PATH) != _read_json(LOCK_PATH)["holdout_sha256"]:
        raise RuntimeError("holdout hash mismatch after seal")
    holdout = _read_json(HOLDOUT_PATH)
    validate_holdout(holdout)
    cases = holdout["cases"]
    samples = []
    for ordinal, case in enumerate(cases, 1):
        messages = build_messages(text=case["text"], state_variant=case["state_variant"])
        sample_id = f"WR2C4H-{ordinal:02d}"
        samples.append({
            "sample_id": sample_id,
            "source_case_id": case["case_id"],
            "scene_id": case["scene_id"],
            "state_variant": case["state_variant"],
            "base_revision": wr1r._artifacts()[1][case["state_variant"]].revision,
            "text": case["text"],
            "text_sha256": hashlib.sha256(case["text"].encode("utf-8")).hexdigest(),
            "messages": messages,
            "expected_changes": case["changes"],
            "provider": {"temperature": 0.0, "max_tokens": 4000, "transport_retries": 0, "json_mode": True},
            "request_hash": _digest({"messages": messages, "provider": {"temperature": 0.0, "max_tokens": 4000, "transport_retries": 0, "json_mode": True}}),
        })
    expected = [change for sample in samples for change in sample["expected_changes"]]
    manifest = {
        "schema_version": "world-runtime-sealed-holdout-wr2c4-manifest-v1",
        "experiment_id": "world-runtime-semantic-judgment-wr2c4-sealed-unseen-holdout",
        "partition_role": "sealed_unseen_holdout",
        "holdout_sha256": _sha256_file(HOLDOUT_PATH),
        "locked_manifest_sha256": None,
        "extractor_source_sha256": _sha256_file(EXTRACTOR),
        "projector_source_sha256": _sha256_file(PROJECTOR),
        "validator_source_sha256": _sha256_file(VALIDATOR),
        "runner_source_sha256": _sha256_file(SOURCE),
        "response_schema_sha256": _digest(RawJudgmentResponse.model_json_schema()),
        "provider_host": urlparse(settings.LLM_BASE_URL).hostname,
        "model": settings.WRITER_LLM_MODEL,
        "gates": GATES,
        "samples": samples,
        "sample_count": len(samples),
        "expected_change_count": len(expected),
        "expected_empty_count": sum(not sample["expected_changes"] for sample in samples),
        "all_13_types_covered": len({c["change_type"] for c in expected}) == 13,
        "external_execution_authorized": False,
        "production_behavior_changed": False,
        "state_commit_authorized": False,
        "sealed_holdout_used": True,
        "prior_development_partition_reused": False,
    }
    manifest_path = output_dir / "private/locked-manifest.json"
    _write_json(manifest_path, manifest)
    manifest["locked_manifest_sha256"] = _sha256_file(manifest_path)
    _write_json(manifest_path, manifest)
    ledger = {
        "schema_version": "world-runtime-sealed-holdout-wr2c4-ledger-v1",
        "samples": {
            sample["sample_id"]: {
                "request_hash": sample["request_hash"],
                "status": "pending",
                "attempt_count": 0,
            }
            for sample in samples
        },
    }
    _write_json(output_dir / "attempt-ledger.json", ledger)
    return manifest


def audit(output_dir: Path = RUNTIME) -> dict[str, Any]:
    issues = []
    if not LOCK_PATH.exists():
        issues.append("holdout_lock_missing")
    else:
        lock = _read_json(LOCK_PATH)
        if _sha256_file(HOLDOUT_PATH) != lock["holdout_sha256"]:
            issues.append("holdout_hash_mismatch")
        source_paths = {
            "extractor_sha256": EXTRACTOR,
            "projector_sha256": PROJECTOR,
            "validator_sha256": VALIDATOR,
            "runner_sha256": SOURCE,
        }
        for field, expected in lock["source_hashes"].items():
            if _sha256_file(source_paths[field]) != expected:
                issues.append(f"source_hash_mismatch:{field}")
    if not HOLDOUT_PATH.exists():
        issues.append("holdout_file_missing")
    manifest_path = output_dir / "private/locked-manifest.json"
    if not manifest_path.exists():
        issues.append("manifest_missing")
    else:
        manifest = _read_json(manifest_path)
        if manifest["holdout_sha256"] != _sha256_file(HOLDOUT_PATH):
            issues.append("manifest_holdout_hash_mismatch")
    ledger_path = output_dir / "attempt-ledger.json"
    ledger = _read_json(ledger_path) if ledger_path.exists() else {}
    pending = sum(item["status"] == "pending" for item in ledger.get("samples", {}).values())
    attempts = sum(item["attempt_count"] for item in ledger.get("samples", {}).values())
    output_path = output_dir / "private/outputs"
    outputs = list(output_path.glob("*.json")) if output_path.exists() else []
    if ledger and (pending != manifest["sample_count"] or attempts != 0 or outputs):
        issues.append("ledger_or_outputs_not_pristine")
    if settings.WRITER_WORLD_RUNTIME_MODE != "off":
        issues.append("production_default_not_off")
    return {
        "schema_version": "world-runtime-sealed-holdout-wr2c4-preflight-v1",
        "ready": not issues,
        "issues": issues,
        "holdout_sha256": _sha256_file(HOLDOUT_PATH) if HOLDOUT_PATH.exists() else None,
        "pending": pending,
        "attempt_count_total": attempts,
        "output_files": len(outputs),
        "production_default": settings.WRITER_WORLD_RUNTIME_MODE,
        "sealed_holdout_used": True,
        "prior_development_partition_reused": False,
        "state_commit_authorized": False,
    }


def preflight(
    output_dir: Path = RUNTIME,
    authorization_path: Path = DEFAULT_AUTHORIZATION,
) -> dict[str, Any]:
    check = audit(output_dir)
    manifest = _read_json(output_dir / "private/locked-manifest.json")
    issues = list(check["issues"])
    if not authorization_path.exists():
        issues.append("authorization_missing")
        authorization = {}
    else:
        authorization = _read_json(authorization_path)
    expected = {
        "schema_version": "world-runtime-semantic-canary-wr2c4-sealed-holdout-external-authorization-v1",
        "authorized": True,
        "experiment_id": manifest["experiment_id"],
        "holdout_sha256": manifest["holdout_sha256"],
        "locked_manifest_sha256": _sha256_file(output_dir / "private/locked-manifest.json"),
        "runner_source_sha256": _sha256_file(SOURCE),
        "extractor_source_sha256": _sha256_file(EXTRACTOR),
        "projector_source_sha256": _sha256_file(PROJECTOR),
        "maximum_requests": manifest["sample_count"],
        "transport_retries": 0,
        "execute_command_exactly_once": True,
        "prior_development_partition_reuse_authorized": False,
        "sealed_holdout_use_authorized": True,
        "production_writer_change_authorized": False,
        "state_commit_authorized": False,
    }
    for field, value in expected.items():
        if authorization.get(field) != value:
            issues.append(f"authorization.{field}:expected:{value!r}")
    return {
        "schema_version": "world-runtime-sealed-holdout-wr2c4-external-preflight-v1",
        "ready": not issues,
        "issues": issues,
        "holdout_sha256": check["holdout_sha256"],
        "sample_count": manifest["sample_count"],
        "pending": check["pending"],
        "attempt_count_total": check["attempt_count_total"],
        "transport_retries": 0,
        "production_default": settings.WRITER_WORLD_RUNTIME_MODE,
        "authorization_sha256": _sha256_file(authorization_path) if authorization_path.exists() else None,
        "prior_development_partition_reused": False,
        "sealed_holdout_used": True,
        "state_commit_authorized": False,
    }


def evaluate(output_dir: Path = RUNTIME) -> dict[str, Any]:
    manifest = _read_json(output_dir / "private/locked-manifest.json")
    ledger = _read_json(output_dir / "attempt-ledger.json")
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
            raise RuntimeError("sealed holdout requires exactly one succeeded attempt per sample")
        output_path = output_dir / "private/outputs" / f"{sample['sample_id']}.json"
        response_text = output_path.read_text(encoding="utf-8")
        if hashlib.sha256(response_text.encode("utf-8")).hexdigest() != entry["output_sha256"]:
            raise RuntimeError("sealed holdout response hash mismatch")
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
        "schema_version": "world-runtime-sealed-holdout-wr2c4-evaluation-v1",
        "partition_role": "sealed_unseen_holdout",
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
        "sealed_holdout_gate_passed": passed,
        "production_promotion_eligible": False,
        "state_commit_canary_authorized": passed,
        "state_mutations": 0,
        "commits": 0,
        "decision": "sealed_holdout_passed_state_commit_canary_eligible" if passed else "hold_sealed_holdout_failed_no_rerun",
        "items": items,
    }
    _write_json(output_dir / "evaluation.json", result)
    return result


def run_once(
    output_dir: Path = RUNTIME,
    authorization_path: Path = DEFAULT_AUTHORIZATION,
) -> dict[str, Any]:
    check = preflight(output_dir, authorization_path)
    if not check["ready"]:
        raise RuntimeError("sealed_holdout_preflight_failed:" + "|".join(check["issues"]))
    manifest = _read_json(output_dir / "private/locked-manifest.json")
    ledger_path = output_dir / "attempt-ledger.json"
    ledger = _read_json(ledger_path)
    client = get_llm_client(manifest["model"])
    completed = 0
    for sample in manifest["samples"]:
        entry = ledger["samples"][sample["sample_id"]]
        if entry["status"] != "pending" or entry["attempt_count"] != 0:
            raise RuntimeError(f"sealed_holdout_refusing_nonpristine_sample:{sample['sample_id']}")
        entry.update(status="started", attempt_count=1)
        _write_json(ledger_path, ledger)
        metadata: dict[str, Any] = {}
        try:
            response = client.chat_completion(
                sample["messages"],
                temperature=sample["provider"]["temperature"],
                max_tokens=sample["provider"]["max_tokens"],
                max_retries=0,
                json_mode=sample["provider"]["json_mode"],
                prompt_name="world_runtime_semantic_judgment_wr2c4_holdout_v1",
                completion_metadata_sink=metadata.update,
            )
            output_path = output_dir / "private/outputs" / f"{sample['sample_id']}.json"
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(response, encoding="utf-8", newline="\n")
            entry.update(
                status="succeeded",
                output_sha256=hashlib.sha256(response.encode("utf-8")).hexdigest(),
                completion_metadata=metadata,
            )
            completed += 1
        except Exception as exc:
            entry.update(status="failed", error_type=type(exc).__name__, error=str(exc)[:500])
            _write_json(ledger_path, ledger)
            raise
        _write_json(ledger_path, ledger)
    evaluation = evaluate(output_dir)
    return {
        "schema_version": "world-runtime-sealed-holdout-wr2c4-external-result-v1",
        "command_executed_exactly_once": True,
        "succeeded": completed,
        "failed": 0,
        "attempt_count_total": sum(item["attempt_count"] for item in ledger["samples"].values()),
        "transport_retries": 0,
        "sealed_holdout_gate_passed": evaluation["sealed_holdout_gate_passed"],
        "decision": evaluation["decision"],
        "production_promotion_eligible": False,
        "state_commit_canary_authorized": evaluation["state_commit_canary_authorized"],
        "state_commit_authorized": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="WR2-C4 sealed unseen holdout runner")
    parser.add_argument("command", choices=("seal", "build", "preflight", "run-once"))
    parser.add_argument("--output", type=Path, default=RUNTIME)
    parser.add_argument("--authorization", type=Path, default=DEFAULT_AUTHORIZATION)
    args = parser.parse_args()
    if args.command == "seal":
        result = seal(args.output)
    elif args.command == "build":
        result = build(args.output)
    elif args.command == "preflight":
        result = preflight(args.output, args.authorization)
    else:
        result = run_once(args.output, args.authorization)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
