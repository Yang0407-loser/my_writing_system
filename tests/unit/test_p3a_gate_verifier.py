from __future__ import annotations

from copy import deepcopy

import pytest

from scripts.p3a.verify_projection_gate import (
    REQUIRED_PROJECTORS,
    REQUIRED_SCENARIOS,
    verify_evidence,
)


def _valid_evidence():
    digest = "a" * 64
    return {
        "schema_version": "p3a-projection-gate-v1",
        "backend": "postgresql",
        "gate_eligible": True,
        "postgres_tests": {"passed": 16, "failed": 0, "skipped": 0},
        "scenarios": {
            name: {"status": "passed", "exit_code": 0, "skipped": 0}
            for name in REQUIRED_SCENARIOS
        },
        "projectors": {
            name: {
                "deleted": True,
                "rebuilt": True,
                "reconciled": True,
                "expected_manifest_digest": digest,
                "actual_manifest_digest": digest,
            }
            for name in REQUIRED_PROJECTORS
        },
        "final_health": {"lag_events": 0},
        "secret_scan": {"contains_secret": False, "findings": []},
    }


def test_valid_evidence_passes():
    assert verify_evidence(_valid_evidence()) == []


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (lambda row: row.update(backend="sqlite"), "real PostgreSQL"),
        (
            lambda row: row["postgres_tests"].update(skipped=1),
            "zero skipped",
        ),
        (
            lambda row: row["scenarios"].pop(
                "celery_redis_outage_scanner_recovery"
            ),
            "missing scenarios",
        ),
        (
            lambda row: row["scenarios"]["stale_token_rejected"].update(
                status="failed", exit_code=1
            ),
            "did not pass",
        ),
        (
            lambda row: row["scenarios"]["strict_partition_order"].update(
                skipped=1
            ),
            "did not pass",
        ),
        (
            lambda row: row["projectors"].pop("analytics"),
            "seven-projector",
        ),
        (
            lambda row: row["projectors"]["analytics"].update(
                actual_manifest_digest="b" * 64
            ),
            "projector evidence failed",
        ),
        (
            lambda row: row["final_health"].update(lag_events=1),
            "lag is not zero",
        ),
        (
            lambda row: row["secret_scan"].update(
                contains_secret=True, findings=["$.token"]
            ),
            "secret",
        ),
    ],
)
def test_verifier_rejects_incomplete_or_untrusted_evidence(mutate, expected):
    evidence = deepcopy(_valid_evidence())
    mutate(evidence)
    assert any(expected in error for error in verify_evidence(evidence))
