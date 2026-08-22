from __future__ import annotations

from unittest.mock import MagicMock

from app.coordinator import execute_canonical_subsection
from app.config import CanonicalSettings


def test_coordinator_retry_delegates_to_runtime_preflight_once():
    rollout = CanonicalSettings.from_env(
        {
            "WRITER_TESTING": "1",
            "CANONICAL_COMMIT_MODE": "canary",
            "CANONICAL_CANARY_TASK_IDS": "task-1",
            "CANONICAL_CANARY_SUBSECTION_IDS": "subsection-1",
        }
    )
    runtime = MagicMock()
    runtime.execute.return_value = MagicMock(
        phase="ready", generated=False, critical_projection_status="ready"
    )
    command = MagicMock(task_id="task-1", subsection_id="subsection-1")

    result = execute_canonical_subsection(runtime, command, rollout=rollout)

    assert result.generated is False
    assert result.critical_projection_status == "ready"
    runtime.execute.assert_called_once_with(command)
