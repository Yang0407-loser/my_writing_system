"""Fail-closed verifier for P3A projection recovery evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


REQUIRED_PROJECTORS = {
    "legacy_world_event",
    "handover_context",
    "chroma_story_chunks",
    "redis_stream",
    "task_preview",
    "markdown_export",
    "analytics",
}
REQUIRED_SCENARIOS = {
    "lease_contention",
    "strict_partition_order",
    "expired_lease_reclaim",
    "stale_token_rejected",
    "duplicate_wakeup_50x",
    "celery_redis_outage_scanner_recovery",
    "critical_dead_letter_barrier",
    "nonblocking_dead_letter_degraded",
    "audited_operator_requeue",
    "seven_projection_delete_rebuild",
    "rebuild_crash_resume",
    "canon_commits_during_rebuild",
    "reconciliation_missing_extra_corrupt",
    "new_projector_no_history_backfill",
    "activation_gap_race",
    "final_lag_zero",
}


def verify_evidence(evidence: dict) -> list[str]:
    errors: list[str] = []
    if evidence.get("schema_version") != "p3a-projection-gate-v1":
        errors.append("unsupported evidence schema")
    if evidence.get("backend") != "postgresql" or not evidence.get("gate_eligible"):
        errors.append("real PostgreSQL evidence is required")
    if int(evidence.get("postgres_tests", {}).get("skipped", -1)) != 0:
        errors.append("PostgreSQL gate must have zero skipped tests")

    scenarios = evidence.get("scenarios", {})
    missing = REQUIRED_SCENARIOS - set(scenarios)
    if missing:
        errors.append(f"missing scenarios: {sorted(missing)}")
    failed = sorted(
        name
        for name in REQUIRED_SCENARIOS & set(scenarios)
        if scenarios[name].get("status") != "passed"
        or int(scenarios[name].get("exit_code", 1)) != 0
        or int(scenarios[name].get("skipped", 0)) != 0
    )
    if failed:
        errors.append(f"scenarios did not pass: {failed}")

    projections = evidence.get("projectors", {})
    if set(projections) != REQUIRED_PROJECTORS:
        errors.append("seven-projector evidence is incomplete")
    else:
        invalid = sorted(
            projector_id
            for projector_id, row in projections.items()
            if not row.get("deleted")
            or not row.get("rebuilt")
            or not row.get("reconciled")
            or row.get("expected_manifest_digest")
            != row.get("actual_manifest_digest")
        )
        if invalid:
            errors.append(f"projector evidence failed: {invalid}")

    if int(evidence.get("final_health", {}).get("lag_events", -1)) != 0:
        errors.append("final projection lag is not zero")
    if evidence.get("secret_scan", {}).get("contains_secret"):
        errors.append("evidence contains a secret finding")
    if evidence.get("secret_scan", {}).get("findings"):
        errors.append("secret scan findings must be empty")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args(argv)
    evidence = json.loads(args.evidence.read_text(encoding="utf-8"))
    errors = verify_evidence(evidence)
    print(json.dumps({"passed": not errors, "errors": errors}, sort_keys=True))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
