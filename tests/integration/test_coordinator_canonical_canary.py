from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.config import CanonicalSettings
from app.coordinator import execute_canonical_subsection
import app.coordinator as coordinator


def test_coordinator_routes_only_exact_canary_subsection():
    rollout = CanonicalSettings.from_env(
        {
            "WRITER_TESTING": "1",
            "CANONICAL_COMMIT_MODE": "canary",
            "CANONICAL_CANARY_TASK_IDS": "task-1",
            "CANONICAL_CANARY_SUBSECTION_IDS": "subsection-1",
        }
    )
    runtime = MagicMock()
    command = MagicMock(task_id="task-1", subsection_id="subsection-1")
    runtime.execute.return_value = {"phase": "ready"}

    assert execute_canonical_subsection(runtime, command, rollout=rollout) == {
        "phase": "ready"
    }
    runtime.execute.assert_called_once_with(command)

    runtime.reset_mock()
    legacy_command = MagicMock(task_id="task-1", subsection_id="subsection-2")
    assert execute_canonical_subsection(
        runtime, legacy_command, rollout=rollout
    ) is None
    runtime.execute.assert_not_called()


def test_internal_required_fails_closed_without_runtime_or_binding():
    rollout = CanonicalSettings.from_env(
        {
            "WRITER_TESTING": "1",
            "CANONICAL_COMMIT_MODE": "internal_required",
        }
    )
    command = MagicMock(task_id="task-1", subsection_id="subsection-1")

    with pytest.raises(RuntimeError, match="fail closed"):
        execute_canonical_subsection(None, command, rollout=rollout)


def test_pre_foundation_resume_is_explicitly_legacy():
    rollout = CanonicalSettings.from_env(
        {
            "WRITER_TESTING": "1",
            "CANONICAL_COMMIT_MODE": "internal_required",
        }
    )
    runtime = MagicMock()
    command = MagicMock(task_id="old-task", subsection_id="subsection-1")

    assert execute_canonical_subsection(
        runtime,
        command,
        rollout=rollout,
        pre_foundation_resume=True,
    ) is None
    runtime.execute.assert_not_called()


def test_main_writer_path_cannot_silently_bypass_internal_required(monkeypatch):
    monkeypatch.setattr(coordinator.settings, "CANONICAL_COMMIT_MODE", "internal_required")

    with pytest.raises(RuntimeError, match="tenant/project/document binding"):
        coordinator._build_canonical_writer_bridge(
            writer=MagicMock(),
            bb=MagicMock(),
            task_id="task-1",
            state={},
            outline=[{
                "section": 1,
                "subsections": [{
                    "id": "subsection-1",
                    "subsection": 1,
                }],
            }],
            vector_store=MagicMock(),
            world_state=MagicMock(),
            event_graph=MagicMock(),
        )
