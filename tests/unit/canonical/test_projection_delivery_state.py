from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.canonical.errors import (
    PermanentProjectionError,
    RetryableProjectionError,
)
from app.canonical.projection_delivery import failure_transition
from app.canonical.projection_registry import RetryPolicy


NOW = datetime(2026, 8, 12, 8, 0, tzinfo=timezone.utc)
POLICY = RetryPolicy(
    max_attempts=3,
    base_delay_seconds=2,
    max_delay_seconds=30,
    lease_seconds=120,
    heartbeat_seconds=30,
)


@pytest.mark.parametrize(
    ("error", "attempt_count", "expected_status", "expected_available_at"),
    [
        (
            RetryableProjectionError("temporary"),
            1,
            "pending",
            NOW + timedelta(seconds=2),
        ),
        (PermanentProjectionError("invalid"), 1, "dead_letter", NOW),
        (RuntimeError("unknown"), 3, "dead_letter", NOW),
    ],
)
def test_failure_transition_classifies_retry_and_dead_letter(
    error, attempt_count, expected_status, expected_available_at
):
    transition = failure_transition(error, attempt_count, POLICY, NOW)

    assert transition.status == expected_status
    assert transition.available_at == expected_available_at
    assert transition.error_class == type(error).__name__
    assert transition.error_message == str(error)


def test_connection_timeout_and_rate_limit_errors_are_retryable():
    for error in (
        ConnectionError("sink unavailable"),
        TimeoutError("sink timeout"),
        RetryableProjectionError("rate limited"),
    ):
        assert failure_transition(error, 1, POLICY, NOW).status == "pending"


def test_retry_backoff_is_exponential_and_capped_by_registry_policy():
    assert failure_transition(RuntimeError("x"), 2, POLICY, NOW).available_at == (
        NOW + timedelta(seconds=4)
    )
    assert failure_transition(RuntimeError("x"), 99, POLICY, NOW).status == (
        "dead_letter"
    )
