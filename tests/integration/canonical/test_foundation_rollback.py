from __future__ import annotations

from unittest.mock import MagicMock

from app.canonical.commit_service import PROJECTION_MANIFEST
from app.canonical.repositories import CanonicalRepository
from app.config import CanonicalSettings
from app.coordinator import execute_canonical_subsection
from app.writing.canonical_subsection_runtime import (
    CanonicalSubsectionCommand,
    CanonicalSubsectionRuntime,
)
from tests.unit.canonical.test_commit_service import _prepared, canonical_session


def test_mode_rollback_preserves_committed_canon_and_does_not_cross_barrier(
    canonical_session,
):
    projectors = {name: MagicMock() for name, _ in PROJECTION_MANIFEST}
    projectors["handover_context"] = MagicMock(
        side_effect=RuntimeError("handover unavailable")
    )
    llm = MagicMock()

    def generate(*, snapshot, base_revision_number, command):
        llm()
        return _prepared(
            canonical_session,
            base_revision_number=base_revision_number,
            base_state_version_id=snapshot.version_id,
        ).candidate

    runtime = CanonicalSubsectionRuntime(
        session=canonical_session,
        tenant_id="tenant-1",
        project_id="project-1",
        candidate_generator=generate,
        projectors=projectors,
        checkpoint_writer=lambda _payload: None,
    )
    command = CanonicalSubsectionCommand(
        task_id="task-1",
        document_id="document-1",
        subsection_id="subsection-1",
        generation_attempt_id="rollback-attempt",
        expected_revision_id="GENESIS",
        expected_state_version_id="state-genesis",
    )
    canary = CanonicalSettings(
        database_url="sqlite+pysqlite:///:memory:",
        commit_mode="canary",
        canary_task_ids=frozenset({"task-1"}),
        canary_subsection_ids=frozenset({"subsection-1"}),
    )

    result = execute_canonical_subsection(runtime, command, rollout=canary)
    assert result.phase == "awaiting_critical_projection"
    repo = CanonicalRepository(canonical_session, "tenant-1", "project-1")
    committed_head = repo.get_current_state().id
    committed_text = repo.materialize_document("document-1")

    legacy = canary.__class__(
        database_url=canary.database_url,
        commit_mode="legacy",
        canary_task_ids=frozenset(),
        canary_subsection_ids=frozenset(),
    )
    next_llm = MagicMock(side_effect=AssertionError("barrier must still pause"))
    runtime.candidate_generator = next_llm
    assert execute_canonical_subsection(runtime, command, rollout=legacy) is None

    # A stale legacy checkpoint may exist, but it cannot move either SQL Head.
    stale_checkpoint = {"draft": {"1-1": "stale"}, "current_state_version_id": "old"}
    assert stale_checkpoint["current_state_version_id"] != committed_head
    assert repo.get_current_state().id == committed_head
    assert repo.materialize_document("document-1") == committed_text
    next_llm.assert_not_called()
