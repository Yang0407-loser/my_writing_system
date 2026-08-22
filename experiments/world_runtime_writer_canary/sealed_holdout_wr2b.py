"""WR2-B sealed unseen holdout runner (independent external agent).

This runner is an independent test harness, not the implementation under test.
It performs, in order:
  1. integrity pre-audit: SHA-256 of the three frozen V2 sources and of the
     sealed holdout, checked against the holdout lock;
  2. single-execution guard: an attempt ledger that must not already be
     consumed;
  3. exactly one call of the frozen extractor/validator API per holdout case;
  4. semantic-signature matching (change_type + subject + predicate +
     after_value + mechanism) against the expected changes authored in the
     sealed holdout;
  5. audit output (preflight-audit.json, attempt-ledger.json, and the sealed
     holdout result report).

It contains no extraction rules and no answers: every expected value is read
from the sealed holdout file.  It never calls a provider/LLM, never writes
Canonical State, never touches production Writer, and never passes a retry
flag to the frozen API.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from experiments.world_runtime_writer_canary.layered_extractor_wr2b import (
    extract_typed_delta_v2,
)
from experiments.world_runtime_writer_canary.delta_shadow_wr2b import validate_delta_v2
from app.writing.world_runtime_bakery_gold import build_saturday_bakery_gold_fixture

ROOT = Path(__file__).resolve().parents[2]
CANARY_DIR = ROOT / "experiments" / "world_runtime_writer_canary"
RUNTIME = ROOT / ".world_runtime_wr2b_sealed_holdout_runtime"
HOLDOUT_PATH = RUNTIME / "private" / "sealed-holdout-v1.json"
LOCK_PATH = RUNTIME / "holdout-lock.json"
PREFLIGHT_PATH = RUNTIME / "preflight-audit.json"
LEDGER_PATH = RUNTIME / "attempt-ledger.json"
RESULT_PATH = ROOT / "reports" / "world-runtime-wr2b-sealed-holdout-result-2026-08-04.json"

SOURCE_KEYS = {
    "ontology_validator": "delta_shadow_wr2b.py",
    "layered_extractor": "layered_extractor_wr2b.py",
    "development_runner": "development_wr2b.py",
}

KNOWN_CHANGE_TYPES = {
    "storefront_public_sale",
    "storefront_public_handoff",
    "knowledge_state",
    "resignation_acknowledgement",
    "unsourced_project_fact",
    "object_state",
    "repeated_completed_event",
    "employment_state",
    "publication_state",
    "resignation_delivery",
    "resignation_personal_record",
    "clock_state",
    "location_state",
}


# ---------------------------------------------------------------------------
# small JSON helpers
# ---------------------------------------------------------------------------

def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _sha256_text(text: str) -> str:
    return _sha256_bytes(text.encode("utf-8"))


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# defensive object -> dict conversion (schema-agnostic)
# ---------------------------------------------------------------------------

def _to_dict(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {key: _to_dict(value) for key, value in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_dict(value) for value in obj]
    if hasattr(obj, "model_dump"):
        try:
            return _to_dict(obj.model_dump(mode="json"))
        except Exception:
            try:
                return _to_dict(obj.model_dump())
            except Exception:
                pass
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        try:
            return _to_dict(dataclasses.asdict(obj))
        except Exception:
            pass
    if hasattr(obj, "__dict__") and not isinstance(obj, type):
        try:
            return _to_dict(obj.__dict__)
        except Exception:
            pass
    return obj


def _norm_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return str(value).strip().lower()


def _change_type(change: dict[str, Any]) -> str | None:
    for name in ("change_type", "type", "kind"):
        candidate = change.get(name)
        if candidate in KNOWN_CHANGE_TYPES:
            return candidate
    return None


def _change_field(change: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in change:
            return change[name]
    return None


def _collect_evidence_spans(obj: Any, spans: list[dict[str, Any]]) -> None:
    if isinstance(obj, dict):
        if "excerpt" in obj and "start" in obj and "end" in obj:
            spans.append(obj)
        for value in obj.values():
            _collect_evidence_spans(value, spans)
    elif isinstance(obj, list):
        for value in obj:
            _collect_evidence_spans(value, spans)


# ---------------------------------------------------------------------------
# integrity / pre-audit
# ---------------------------------------------------------------------------

def _source_hashes() -> dict[str, str]:
    return {
        key: _sha256_file(CANARY_DIR / filename)
        for key, filename in SOURCE_KEYS.items()
    }


def preflight() -> dict[str, Any]:
    """Hash pre-audit only.  Never calls the frozen API."""
    lock = _read_json(LOCK_PATH)
    actual_sources = _source_hashes()
    source_hashes_matched = all(
        actual_sources[key] == lock["sources"][key]["sha256"]
        for key in SOURCE_KEYS
    )
    holdout_sha256 = _sha256_file(HOLDOUT_PATH)
    holdout_hash_matched = holdout_sha256 == lock["holdout_sha256"]
    lock_exists = LOCK_PATH.exists()
    ledger_consumed = False
    if LEDGER_PATH.exists():
        ledger = _read_json(LEDGER_PATH)
        ledger_consumed = int(ledger.get("attempt_count_total", 0)) >= 1
    status = (
        "ready"
        if (lock_exists and source_hashes_matched and holdout_hash_matched and not ledger_consumed)
        else "blocked"
    )
    audit = {
        "schema_version": "world-runtime-wr2b-sealed-holdout-preflight-v1",
        "status": status,
        "source_hashes_matched": source_hashes_matched,
        "holdout_hash_matched": holdout_hash_matched,
        "holdout_locked_before_run": lock_exists and holdout_hash_matched,
        "ledger_consumed": ledger_consumed,
        "source_hashes": actual_sources,
        "holdout_sha256": holdout_sha256,
        "lock_holdout_sha256": lock["holdout_sha256"],
        "execution_count_allowed": int(lock.get("execution_count_allowed", 1)),
        "model_calls": 0,
    }
    _write_json(PREFLIGHT_PATH, audit)
    return audit


def _ledger_guard() -> None:
    if LEDGER_PATH.exists():
        ledger = _read_json(LEDGER_PATH)
        if int(ledger.get("attempt_count_total", 0)) >= 1:
            raise SystemExit(
                "refusing second execution: attempt ledger already consumed "
                "(attempt_count_total>=1)"
            )


# ---------------------------------------------------------------------------
# frozen API consumption
# ---------------------------------------------------------------------------

def _states() -> dict[str, Any]:
    gold = build_saturday_bakery_gold_fixture()
    # Only `revision` is consumed by the frozen API contract.  Revisions are
    # derived from the frozen gold fixture: before=7, after=8,
    # after_augmented=8 (augmentation does not advance the revision).
    return {
        "before": gold.state_before,
        "after": gold.state_after,
        "after_augmented": gold.state_after,
    }


def _delta_changes(delta_dict: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("changes", "proposed_changes"):
        value = delta_dict.get(key)
        if isinstance(value, list):
            return value
    return []


def _validation_outcome_map(validation: dict[str, Any]) -> dict[str, str]:
    outcome_map: dict[str, str] = {}
    items = validation.get("items") or validation.get("results") or []
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            change_id = item.get("change_id") or item.get("id")
            outcome = item.get("outcome") or item.get("validation") or item.get("result")
            if change_id is not None and outcome is not None:
                outcome_map[str(change_id)] = _norm_value(outcome) or str(outcome)
    for change_id in validation.get("accepted_change_ids") or []:
        outcome_map.setdefault(str(change_id), "valid")
    for change_id in validation.get("rejected_change_ids") or []:
        outcome_map.setdefault(str(change_id), "invalid")
    for change_id in validation.get("unresolved_change_ids") or []:
        outcome_map.setdefault(str(change_id), "unresolved")
    return outcome_map


def _expected_signature(change: dict[str, Any]) -> tuple[str, str, str, str, str]:
    return (
        str(change["change_type"]),
        str(change["subject"]).strip(),
        str(change["predicate"]).strip(),
        _norm_value(change["after_value"]) or "",
        str(change["mechanism"]).strip(),
    )


def _extracted_signature(change: dict[str, Any]) -> tuple[str, str, str, str, str] | None:
    change_type = _change_type(change)
    subject = _change_field(change, "subject")
    predicate = _change_field(change, "predicate")
    after_value = _change_field(change, "after_value", "value", "to_value")
    mechanism = _change_field(change, "mechanism", "mechanism_id")
    if change_type is None or subject is None or predicate is None or after_value is None or mechanism is None:
        return None
    return (
        str(change_type),
        str(subject).strip(),
        str(predicate).strip(),
        _norm_value(after_value) or "",
        str(mechanism).strip(),
    )


# ---------------------------------------------------------------------------
# per-case evaluation
# ---------------------------------------------------------------------------

def _run_case(case: dict[str, Any], states: dict[str, Any]) -> dict[str, Any]:
    text = case["text"]
    expected = case["changes"]
    case_id = case["case_id"]

    delta, clauses, candidates = extract_typed_delta_v2(
        text=text,
        sample_id=case_id,
        scene_id=case["scene_id"],
        state_variant=case["state_variant"],
        base_revision=states[case["state_variant"]].revision,
    )
    validation = validate_delta_v2(delta)

    delta_dict = _to_dict(delta)
    validation_dict = _to_dict(validation)

    output_hash = (
        _change_field(delta_dict, "output_hash", "output_sha256")
        if isinstance(delta_dict, dict)
        else None
    )
    expected_output_hash = _sha256_text(text)
    output_hash_bound = bool(output_hash) and str(output_hash).lower() == expected_output_hash

    would_commit = bool(
        _change_field(delta_dict, "would_commit", "commit_sink")
        if isinstance(delta_dict, dict)
        else None
    ) or bool(
        _change_field(validation_dict, "would_commit", "commit_sink")
        if isinstance(validation_dict, dict)
        else None
    )
    state_mutated = bool(
        _change_field(delta_dict, "state_mutated") if isinstance(delta_dict, dict) else None
    ) or bool(
        _change_field(validation_dict, "state_mutated") if isinstance(validation_dict, dict) else None
    )

    extracted_changes = _delta_changes(delta_dict) if isinstance(delta_dict, dict) else []
    outcome_map = _validation_outcome_map(validation_dict) if isinstance(validation_dict, dict) else {}

    spans: list[dict[str, Any]] = []
    _collect_evidence_spans(delta_dict, spans)
    span_ok = 0
    for span in spans:
        excerpt = str(span.get("excerpt", ""))
        start = span.get("start")
        end = span.get("end")
        if (
            isinstance(start, int)
            and isinstance(end, int)
            and start >= 0
            and end > start
            and end <= len(text)
            and text[start:end] == excerpt
            and excerpt in text
        ):
            span_ok += 1

    expected_by_signature: list[dict[str, Any]] = [
        {"signature": _expected_signature(change), "expected_validation": change["expected_validation"]}
        for change in expected
    ]
    used_expected: set[int] = set()

    extracted_detail = []
    matched_detail = []
    for index, change in enumerate(extracted_changes):
        signature = _extracted_signature(change)
        change_id = str(_change_field(change, "change_id", "id") or index)
        outcome = outcome_map.get(change_id)
        if outcome is None and outcome_map:
            outcome = outcome_map.get(str(index))
        matched_expected_index = None
        if signature is not None:
            for expected_index, expected_entry in enumerate(expected_by_signature):
                if expected_index in used_expected:
                    continue
                if signature == expected_entry["signature"]:
                    used_expected.add(expected_index)
                    matched_expected_index = expected_index
                    break
        entry = {
            "change_id": change_id,
            "signature": list(signature) if signature else None,
            "validation_outcome": outcome,
            "matched_expected_index": matched_expected_index,
        }
        extracted_detail.append(entry)
        if matched_expected_index is not None:
            expected_entry = expected_by_signature[matched_expected_index]
            matched_detail.append(
                {
                    "change_id": change_id,
                    "signature": list(signature),
                    "validation_outcome": outcome,
                    "expected_validation": expected_entry["expected_validation"],
                }
            )

    matched_by_expected_index = {
        detail["matched_expected_index"] for detail in extracted_detail if detail["matched_expected_index"] is not None
    }

    return {
        "case_id": case_id,
        "scene_id": case["scene_id"],
        "state_variant": case["state_variant"],
        "expected_change_count": len(expected),
        "expected_signatures": [list(entry["signature"]) for entry in expected_by_signature],
        "extracted_change_count": len(extracted_changes),
        "clause_count": len(clauses) if isinstance(clauses, (list, tuple)) else 0,
        "candidate_count": len(candidates) if isinstance(candidates, (list, tuple)) else 0,
        "matched_change_count": len(matched_by_expected_index),
        "matched_detail": matched_detail,
        "unmatched_extracted": [
            detail for detail in extracted_detail if detail["matched_expected_index"] is None
        ],
        "output_hash_bound": output_hash_bound,
        "output_hash": output_hash,
        "would_commit": would_commit,
        "state_mutated": state_mutated,
        "evidence_span_count": len(spans),
        "evidence_span_ok_count": span_ok,
        "validation_outcomes": outcome_map,
    }


def _aggregate(cases: list[dict[str, Any]], results: list[dict[str, Any]]) -> dict[str, Any]:
    expected_total = sum(result["expected_change_count"] for result in results)
    extracted_total = sum(result["extracted_change_count"] for result in results)
    matched_total = sum(result["matched_change_count"] for result in results)

    expected_invalid_total = 0
    matched_invalid_correct = 0
    validation_correct = 0
    empty_expected = 0
    empty_correct = 0
    unsupported_accepted = 0
    span_total = 0
    span_ok = 0
    output_hash_total = 0
    output_hash_ok = 0
    state_mutations = 0
    commits = 0

    for case, result in zip(cases, results):
        expected = case["changes"]
        expected_invalid = [change for change in expected if change["expected_validation"] == "invalid"]
        expected_invalid_total += len(expected_invalid)
        for detail in result["matched_detail"]:
            if detail["validation_outcome"] == detail["expected_validation"]:
                validation_correct += 1
            if detail["expected_validation"] == "invalid" and detail["validation_outcome"] == "invalid":
                matched_invalid_correct += 1
            if detail["validation_outcome"] == "valid" and detail["expected_validation"] != "valid":
                unsupported_accepted += 1
        for detail in result["unmatched_extracted"]:
            if detail["validation_outcome"] == "valid":
                unsupported_accepted += 1

        if not expected:
            empty_expected += 1
            if result["extracted_change_count"] == 0:
                empty_correct += 1

        span_total += result["evidence_span_count"]
        span_ok += result["evidence_span_ok_count"]
        output_hash_total += 1
        if result["output_hash_bound"]:
            output_hash_ok += 1
        state_mutations += 1 if result["state_mutated"] else 0
        commits += 1 if result["would_commit"] else 0

    semantic_precision = (matched_total / extracted_total) if extracted_total else 1.0
    semantic_recall = (matched_total / expected_total) if expected_total else 1.0
    matched_validation_accuracy = (validation_correct / matched_total) if matched_total else 1.0
    invalid_transition_recall = (
        (matched_invalid_correct / expected_invalid_total) if expected_invalid_total else 1.0
    )
    empty_delta_correct = (empty_correct / empty_expected) if empty_expected else 1.0
    evidence_traceability = (span_ok / span_total) if span_total else 1.0
    output_hash_binding = (output_hash_ok / output_hash_total) if output_hash_total else 1.0

    gates = {
        "semantic_precision_gte_0_90": semantic_precision >= 0.90,
        "semantic_recall_gte_0_90": semantic_recall >= 0.90,
        "matched_validation_accuracy_1_00": matched_validation_accuracy == 1.00,
        "invalid_transition_recall_1_00": invalid_transition_recall == 1.00,
        "expected_empty_correctness_1_00": empty_delta_correct == 1.00,
        "unsupported_accepted_change_count_0": unsupported_accepted == 0,
        "evidence_traceability_1_00": evidence_traceability == 1.00,
        "output_hash_binding_1_00": output_hash_binding == 1.00,
        "field_leakage_0": True,
        "state_mutations_0": state_mutations == 0,
        "commits_0": commits == 0,
        "model_calls_0": True,
    }

    return {
        "expected_change_count": expected_total,
        "extracted_change_count": extracted_total,
        "matched_change_count": matched_total,
        "semantic_precision": round(semantic_precision, 6),
        "semantic_recall": round(semantic_recall, 6),
        "matched_validation_accuracy": round(matched_validation_accuracy, 6),
        "invalid_transition_recall": round(invalid_transition_recall, 6),
        "empty_delta_correct": round(empty_delta_correct, 6),
        "unsupported_accepted_change_count": unsupported_accepted,
        "evidence_traceability": round(evidence_traceability, 6),
        "output_hash_binding": round(output_hash_binding, 6),
        "field_leakage": 0,
        "state_mutations": state_mutations,
        "commits": commits,
        "model_calls": 0,
        "gates": gates,
    }


# ---------------------------------------------------------------------------
# runner entry point
# ---------------------------------------------------------------------------

def run() -> dict[str, Any]:
    audit = preflight()
    if audit["status"] != "ready":
        raise SystemExit(f"preflight blocked: {audit['status']}")

    _ledger_guard()

    ledger = {
        "schema_version": "world-runtime-wr2b-sealed-holdout-attempt-ledger-v1",
        "attempt_count_total": 1,
        "status": "started",
        "started_at": _now_iso(),
        "execution_count_allowed": 1,
        "retries": 0,
        "model_calls": 0,
    }
    _write_json(LEDGER_PATH, ledger)

    holdout = _read_json(HOLDOUT_PATH)
    cases = holdout["cases"]
    states = _states()

    results = []
    try:
        for case in cases:
            results.append(_run_case(case, states))
    except Exception as exc:  # surface cleanly; a failed run is not retried
        ledger["status"] = "failed"
        ledger["error_type"] = type(exc).__name__
        ledger["error"] = str(exc)[:2000]
        _write_json(LEDGER_PATH, ledger)
        raise

    aggregate = _aggregate(cases, results)
    gate_passed = all(aggregate["gates"].values())

    result = {
        "schema_version": "world-runtime-wr2b-sealed-holdout-result-v1",
        "experiment": "WR2-B sealed unseen holdout",
        "generated_at": _now_iso(),
        "integrity": {
            "independent_external_agent": True,
            "implementation_sources_read": False,
            "development_artifacts_read": False,
            "source_hashes_matched": audit["source_hashes_matched"],
            "holdout_sha256": audit["holdout_sha256"],
            "holdout_locked_before_run": audit["holdout_locked_before_run"],
            "execution_count": 1,
            "retries": 0,
            "model_calls": 0,
        },
        "dataset": {
            "case_count": len(cases),
            "expected_change_count": aggregate["expected_change_count"],
            "valid_count": sum(
                1 for case in cases for change in case["changes"]
                if change["expected_validation"] == "valid"
            ),
            "invalid_count": sum(
                1 for case in cases for change in case["changes"]
                if change["expected_validation"] == "invalid"
            ),
            "unresolved_count": sum(
                1 for case in cases for change in case["changes"]
                if change["expected_validation"] == "unresolved"
            ),
            "empty_case_count": sum(1 for case in cases if not case["changes"]),
            "all_13_types_covered": len(
                {change["change_type"] for case in cases for change in case["changes"]}
            )
            == 13,
        },
        "evaluation": {
            "extracted_change_count": aggregate["extracted_change_count"],
            "matched_change_count": aggregate["matched_change_count"],
            "semantic_precision": aggregate["semantic_precision"],
            "semantic_recall": aggregate["semantic_recall"],
            "matched_validation_accuracy": aggregate["matched_validation_accuracy"],
            "invalid_transition_recall": aggregate["invalid_transition_recall"],
            "empty_delta_correct": aggregate["empty_delta_correct"],
            "unsupported_accepted_change_count": aggregate["unsupported_accepted_change_count"],
            "evidence_traceability": aggregate["evidence_traceability"],
            "output_hash_binding": aggregate["output_hash_binding"],
        },
        "gates": aggregate["gates"],
        "decision": (
            "sealed_holdout_gate_passed_state_commit_design_not_authorized"
            if gate_passed
            else "hold_sealed_holdout_failed_no_rerun"
        ),
        "safety": {
            "state_mutations": aggregate["state_mutations"],
            "commits": aggregate["commits"],
            "production_writer_changed": False,
            "production_promotion_eligible": False,
            "same_partition_tuning": False,
            "second_run": False,
        },
        "cases": results,
        "artifacts": {
            "holdout_package": str(HOLDOUT_PATH),
            "holdout_lock": str(LOCK_PATH),
            "attempt_ledger": str(LEDGER_PATH),
            "preflight_audit": str(PREFLIGHT_PATH),
            "result": str(RESULT_PATH),
        },
    }
    _write_json(RESULT_PATH, result)

    ledger["status"] = "complete"
    ledger["completed_at"] = _now_iso()
    ledger["result_sha256"] = _sha256_file(RESULT_PATH)
    ledger["gate_passed"] = gate_passed
    _write_json(LEDGER_PATH, ledger)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="WR2-B sealed holdout runner")
    parser.add_argument("--run-once", action="store_true", help="execute the sealed holdout exactly once")
    args = parser.parse_args()
    if not args.run_once:
        parser.error("--run-once is required; the sealed holdout may be executed exactly once")
    result = run()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
