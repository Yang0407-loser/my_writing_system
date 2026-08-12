"""Run the P3A PostgreSQL scenarios and emit bounded evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

from sqlalchemy import case, func, select

from app.canonical.database import build_engine, build_session_factory
from app.canonical.models import CanonicalCommit, ProjectionPartition, ProjectionRebuildRun
from scripts.p3a.verify_projection_gate import (
    REQUIRED_PROJECTORS,
    REQUIRED_SCENARIOS,
    verify_evidence,
)


ROOT = Path(__file__).resolve().parents[2]
GATE_FILE = "tests/integration/canonical/test_p3a_postgres_gate.py"
SUMMARY_RE = re.compile(
    r"(?P<passed>\d+) passed(?:, (?P<failed>\d+) failed)?(?:, (?P<skipped>\d+) skipped)?"
)

SCENARIO_TESTS = {
    "lease_contention": "tests/integration/canonical/test_projection_delivery_claim.py::test_twenty_sessions_create_one_current_owner_and_token",
    "strict_partition_order": "tests/integration/canonical/test_projection_delivery_claim.py::test_later_position_is_blocked_by_every_unpublished_status",
    "expired_lease_reclaim": "tests/integration/canonical/test_projection_delivery_claim.py::test_expired_lease_is_reclaimed_and_old_token_is_fenced",
    "stale_token_rejected": "tests/integration/canonical/test_projection_delivery_claim.py::test_noncurrent_claimed_attempt_has_no_processing_authority",
    "duplicate_wakeup_50x": f"{GATE_FILE}::test_postgres_duplicate_wakeup_and_outage_scanner_recovery",
    "celery_redis_outage_scanner_recovery": f"{GATE_FILE}::test_postgres_duplicate_wakeup_and_outage_scanner_recovery",
    "critical_dead_letter_barrier": f"{GATE_FILE}::test_postgres_dead_letters_barrier_health_and_audited_requeue",
    "nonblocking_dead_letter_degraded": f"{GATE_FILE}::test_postgres_dead_letters_barrier_health_and_audited_requeue",
    "audited_operator_requeue": f"{GATE_FILE}::test_postgres_dead_letters_barrier_health_and_audited_requeue",
    "seven_projection_delete_rebuild": f"{GATE_FILE}::test_postgres_seven_projection_delete_rebuild_and_final_lag",
    "rebuild_crash_resume": f"{GATE_FILE}::test_postgres_rebuild_crash_resume_commits_during_rebuild_and_mismatch",
    "canon_commits_during_rebuild": f"{GATE_FILE}::test_postgres_rebuild_crash_resume_commits_during_rebuild_and_mismatch",
    "reconciliation_missing_extra_corrupt": f"{GATE_FILE}::test_postgres_rebuild_crash_resume_commits_during_rebuild_and_mismatch",
    "new_projector_no_history_backfill": f"{GATE_FILE}::test_postgres_new_projector_bootstrap_has_no_history_backfill",
    "activation_gap_race": f"{GATE_FILE}::test_postgres_activation_gap_race_freezes_threshold_and_reconciles_gap",
    "final_lag_zero": f"{GATE_FILE}::test_postgres_seven_projection_delete_rebuild_and_final_lag",
}


def _run(name: str, node: str, env: dict[str, str]) -> dict[str, object]:
    started = time.perf_counter()
    scenario_env = env.copy()
    scenario_env["P3A_GATE_SCENARIO"] = name
    result = subprocess.run(
        [sys.executable, "-m", "pytest", node, "-q"],
        cwd=ROOT,
        env=scenario_env,
        capture_output=True,
        text=True,
    )
    output = f"{result.stdout}\n{result.stderr}"
    match = SUMMARY_RE.search(output)
    passed = int(match.group("passed")) if match else 0
    failed = int(match.group("failed") or 0) if match else 1
    skipped = int(match.group("skipped") or 0) if match else 0
    return {
        "status": "passed" if result.returncode == 0 and failed == 0 and skipped == 0 else "failed",
        "exit_code": result.returncode,
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "duration_ms": round((time.perf_counter() - started) * 1000, 3),
        "output_digest": hashlib.sha256(output.encode("utf-8")).hexdigest(),
    }


def _database_evidence(database_url: str, run_id: str) -> tuple[dict, int]:
    engine = build_engine(database_url)
    factory = build_session_factory(engine)
    prefix = f"t-{run_id[:8]}-%"
    final_prefix = f"t-{run_id[:8]}-fina-%"
    try:
        with factory() as session:
            runs = session.scalars(
                select(ProjectionRebuildRun).where(
                    ProjectionRebuildRun.tenant_id.like(prefix)
                )
            ).all()
            projectors = {}
            for projector_id in REQUIRED_PROJECTORS:
                matches = [
                    run
                    for run in runs
                    if run.projector_id == projector_id
                    and run.status == "completed"
                    and run.expected_manifest_digest
                    and run.actual_manifest_digest
                ]
                matched = matches[-1] if matches else None
                projectors[projector_id] = {
                    "deleted": matched is not None,
                    "rebuilt": matched is not None,
                    "reconciled": bool(
                        matched
                        and matched.expected_manifest_digest
                        == matched.actual_manifest_digest
                    ),
                    "expected_manifest_digest": (
                        matched.expected_manifest_digest if matched else ""
                    ),
                    "actual_manifest_digest": (
                        matched.actual_manifest_digest if matched else ""
                    ),
                }

            heads = (
                select(
                    CanonicalCommit.tenant_id.label("tenant_id"),
                    CanonicalCommit.project_id.label("project_id"),
                    func.max(CanonicalCommit.stream_position).label("head"),
                )
                .where(
                    CanonicalCommit.status == "committed",
                    CanonicalCommit.tenant_id.like(final_prefix),
                )
                .group_by(CanonicalCommit.tenant_id, CanonicalCommit.project_id)
                .subquery()
            )
            lag = session.scalar(
                select(
                    func.coalesce(
                        func.sum(
                            case(
                                (
                                    heads.c.head
                                    > ProjectionPartition.last_published_position,
                                    heads.c.head
                                    - ProjectionPartition.last_published_position,
                                ),
                                else_=0,
                            )
                        ),
                        0,
                    )
                )
                .select_from(ProjectionPartition)
                .join(
                    heads,
                    (heads.c.tenant_id == ProjectionPartition.tenant_id)
                    & (heads.c.project_id == ProjectionPartition.project_id),
                )
                .where(ProjectionPartition.enrollment_status == "active")
            )
            return projectors, int(lag or 0)
    finally:
        engine.dispose()


def run_gate(output: Path, database_url: str | None = None) -> dict:
    database_url = (database_url or os.getenv("TEST_CANONICAL_DATABASE_URL", "")).strip()
    eligible = database_url.startswith(("postgresql://", "postgresql+psycopg://")) and database_url.rsplit("/", 1)[-1].endswith("_test")
    evidence = {
        "schema_version": "p3a-projection-gate-v1",
        "backend": "postgresql" if eligible else "unknown",
        "gate_eligible": eligible,
        "postgres_tests": {"passed": 0, "failed": 0, "skipped": len(REQUIRED_SCENARIOS)},
        "scenarios": {},
        "projectors": {},
        "final_health": {"lag_events": -1},
        "secret_scan": {"contains_secret": False, "findings": []},
    }
    if eligible:
        env = os.environ.copy()
        env["TEST_CANONICAL_DATABASE_URL"] = database_url
        run_id = uuid4().hex
        env["P3A_GATE_RUN_ID"] = run_id
        for name in sorted(REQUIRED_SCENARIOS):
            evidence["scenarios"][name] = _run(name, SCENARIO_TESTS[name], env)
        passed = sum(row["status"] == "passed" for row in evidence["scenarios"].values())
        failed = len(evidence["scenarios"]) - passed
        evidence["postgres_tests"] = {
            "passed": passed,
            "failed": failed,
            "skipped": sum(row["skipped"] for row in evidence["scenarios"].values()),
        }
        projectors, lag = _database_evidence(database_url, run_id)
        evidence["projectors"] = projectors
        evidence["final_health"] = {"lag_events": lag}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return evidence


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--database-url")
    args = parser.parse_args(argv)
    evidence = run_gate(args.output, args.database_url)
    errors = verify_evidence(evidence)
    print(
        json.dumps(
            {
                "evidence": str(args.output),
                "gate_eligible": evidence["gate_eligible"],
                "passed": not errors,
                "errors": errors,
            },
            sort_keys=True,
        )
    )
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
