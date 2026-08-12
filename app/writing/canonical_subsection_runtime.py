"""Coordinator-owned P2 state machine for one canonical subsection."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import Field
from sqlalchemy.orm import Session

from ..canonical.commit_service import CanonicalCommitService
from ..canonical.contracts import (
    CanonicalCommitResult,
    CanonicalStateSnapshot,
    FrozenArtifact,
    PreparedCanonicalCommit,
    SubsectionCandidate,
)
from ..canonical.errors import RevisionConflict, StateVersionConflict
from ..canonical.projection_delivery import ScanFilter
from ..canonical.projection_barrier import ProjectionBarrier
from ..canonical.projection_ports import ProjectionPort, ProjectionReceipt
from ..canonical.projection_registry import DEFAULT_PROJECTOR_REGISTRY, ProjectorSpec
from ..canonical.projection_worker import ProjectionWorker
from ..canonical.repositories import CanonicalRepository
from ..canonical.state_transition import LegacyStateTransitionAdapter


class CanonicalSubsectionCommand(FrozenArtifact):
    task_id: str
    document_id: str
    subsection_id: str
    generation_attempt_id: str
    expected_revision_id: str
    expected_state_version_id: str


class CanonicalSubsectionRuntimeResult(FrozenArtifact):
    phase: str
    commit: CanonicalCommitResult
    critical_projection_status: str
    non_blocking_projection_status: str
    critical_summary: dict[str, int] = Field(default_factory=dict)
    non_blocking_summary: dict[str, int] = Field(default_factory=dict)
    generated: bool


@dataclass(frozen=True)
class _RuntimeProjectionExecutor:
    """Adapt legacy ports while preserving ProjectionWorker as the only caller."""

    spec: ProjectorSpec
    projector: ProjectionPort

    def apply(self, message) -> ProjectionReceipt:
        receipt = self.projector(message)
        if not isinstance(receipt, ProjectionReceipt):
            raise TypeError("runtime projector must return ProjectionReceipt")
        return receipt


def canonical_idempotency_key(command: CanonicalSubsectionCommand) -> str:
    payload = (
        f"{command.task_id}\0{command.document_id}\0{command.subsection_id}\0"
        f"{command.generation_attempt_id}"
    )
    return "canonical-subsection:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


class CanonicalSubsectionRuntime:
    """Generate, commit and project exactly one subsection in fixed order."""

    def __init__(
        self,
        *,
        session: Session,
        tenant_id: str,
        project_id: str,
        candidate_generator: Callable[..., SubsectionCandidate],
        projectors: Mapping[str, ProjectionPort],
        checkpoint_writer: Callable[[dict[str, Any]], None],
        state_transition_compiler: Any | None = None,
        projection_wakeup_sender: Callable[..., bool] | None = None,
    ) -> None:
        if not tenant_id or not project_id:
            raise ValueError("canonical scope is required")
        self.session = session
        self.tenant_id = tenant_id
        self.project_id = project_id
        self.repo = CanonicalRepository(session, tenant_id, project_id)
        self.candidate_generator = candidate_generator
        self.projectors = dict(projectors)
        self.checkpoint_writer = checkpoint_writer
        self.state_transition_compiler = (
            state_transition_compiler or LegacyStateTransitionAdapter()
        )
        self.projection_wakeup_sender = projection_wakeup_sender

    def execute(self, command: CanonicalSubsectionCommand) -> CanonicalSubsectionRuntimeResult:
        self._validate_command(command)
        idempotency_key = canonical_idempotency_key(command)

        # Retry preflight happens before reading current Heads or invoking the
        # LLM. A post-commit crash necessarily leaves the caller's base stale.
        existing = self.repo.get_idempotency_record(idempotency_key)
        if existing is not None and existing.status == "completed" and existing.result_json:
            commit = CanonicalCommitResult.model_validate(existing.result_json).model_copy(
                update={"skipped_as_duplicate": True}
            )
            return self._resume_projection(command, commit, generated=False)

        document = self.repo.get_document(command.document_id)
        subsection = self.repo.get_subsection(command.subsection_id)
        if document is None or subsection is None or subsection.document_id != document.id:
            raise ValueError("canonical document binding is missing or outside scope")
        current_revision = self.repo.get_current_revision(command.subsection_id)
        actual_revision_id = "GENESIS" if current_revision is None else current_revision.id
        if command.expected_revision_id != actual_revision_id:
            raise RevisionConflict("expected Revision Head does not match loaded Head")
        state = self.repo.get_current_state()
        if state is None:
            raise StateVersionConflict("Project State Head is missing")
        if command.expected_state_version_id != state.id:
            raise StateVersionConflict("expected State Head does not match loaded Head")
        snapshot = CanonicalStateSnapshot.create(
            version_id=state.id,
            project_id=state.project_id,
            schema_version=state.schema_version,
            state_json=state.state_json,
        )
        base_revision_number = 0 if current_revision is None else current_revision.revision_number
        candidate = self.candidate_generator(
            snapshot=snapshot,
            base_revision_number=base_revision_number,
            command=command,
        )
        if (
            candidate.task_id != command.task_id
            or candidate.document_id != command.document_id
            or candidate.subsection_id != command.subsection_id
            or candidate.base_state_version_id != snapshot.version_id
            or candidate.base_revision_number != base_revision_number
        ):
            raise ValueError("generated Candidate does not match loaded canonical bindings")
        transition = self.state_transition_compiler.compile(
            base_state=snapshot, candidate=candidate
        )
        commit = CanonicalCommitService(
            self.session, self.tenant_id, self.project_id
        ).commit(
            PreparedCanonicalCommit(
                candidate=candidate, state_transition=transition
            ),
            idempotency_key,
        )
        self._wake_projection_scanner()
        self._checkpoint(command, commit, "pending")
        return self._resume_projection(command, commit, generated=True)

    def execute_sequence(
        self,
        commands: Sequence[
            tuple[CanonicalSubsectionCommand, Callable[..., SubsectionCandidate]]
        ],
    ) -> list[CanonicalSubsectionRuntimeResult]:
        results: list[CanonicalSubsectionRuntimeResult] = []
        original_generator = self.candidate_generator
        try:
            for command, generator in commands:
                self.candidate_generator = generator
                result = self.execute(command)
                results.append(result)
                if result.critical_projection_status != "ready":
                    break
        finally:
            self.candidate_generator = original_generator
        return results

    @staticmethod
    def _validate_command(command: CanonicalSubsectionCommand) -> None:
        required = {
            "task_id": command.task_id,
            "document_id": command.document_id,
            "subsection_id": command.subsection_id,
            "generation_attempt_id": command.generation_attempt_id,
            "expected_revision_id": command.expected_revision_id,
            "expected_state_version_id": command.expected_state_version_id,
        }
        missing = next((name for name, value in required.items() if not value), None)
        if missing:
            raise ValueError(f"{missing} is required for canonical execution")

    def _resume_projection(
        self,
        command: CanonicalSubsectionCommand,
        commit: CanonicalCommitResult,
        *,
        generated: bool,
    ) -> CanonicalSubsectionRuntimeResult:
        critical_summary = self._scan_critical(commit.commit_id)
        barrier_status = ProjectionBarrier(
            self.session, self.tenant_id, self.project_id
        ).ensure_ready(commit.commit_id)
        if barrier_status != "ready":
            self._checkpoint(command, commit, barrier_status)
            return CanonicalSubsectionRuntimeResult(
                phase="awaiting_critical_projection",
                commit=commit,
                critical_projection_status=barrier_status,
                non_blocking_projection_status="pending",
                critical_summary=critical_summary,
                non_blocking_summary={"published": 0, "failed": 0},
                generated=generated,
            )

        non_blocking_summary = self._scan_non_blocking(commit.commit_id)
        non_blocking_status = (
            "lagging" if non_blocking_summary["failed"] else "ready"
        )
        self._checkpoint(command, commit, "ready")
        return CanonicalSubsectionRuntimeResult(
            phase="ready",
            commit=commit,
            critical_projection_status="ready",
            non_blocking_projection_status=non_blocking_status,
            critical_summary=critical_summary,
            non_blocking_summary=non_blocking_summary,
            generated=generated,
        )

    def _scan_critical(self, commit_id: str) -> dict[str, int]:
        """Bounded synchronous scan retaining the fenced Delivery lease path."""
        summary = self._projection_worker().scan_once(
            ScanFilter(
                tenant_id=self.tenant_id,
                project_id=self.project_id,
                commit_id=commit_id,
                barrier_kind="critical",
                limit=100,
            )
        )
        return {
            "published": summary.published,
            "failed": summary.retried + summary.dead_lettered,
        }

    def _scan_non_blocking(self, commit_id: str) -> dict[str, int]:
        summary = self._projection_worker().scan_once(
            ScanFilter(
                tenant_id=self.tenant_id,
                project_id=self.project_id,
                commit_id=commit_id,
                barrier_kind="non_blocking",
                limit=100,
            )
        )
        return {
            "published": summary.published,
            "failed": summary.retried + summary.dead_lettered,
        }

    def _projection_worker(self) -> ProjectionWorker:
        executors = {
            projector_id: _RuntimeProjectionExecutor(
                DEFAULT_PROJECTOR_REGISTRY.get(projector_id), projector
            )
            for projector_id, projector in self.projectors.items()
            if projector_id
            in {spec.projector_id for spec in DEFAULT_PROJECTOR_REGISTRY.all()}
        }
        return ProjectionWorker(
            lambda: self.session,
            executors,
            worker_id=f"canonical-runtime:{self.tenant_id}:{self.project_id}",
        )

    def _wake_projection_scanner(self) -> None:
        sender = self.projection_wakeup_sender
        if sender is None:
            if os.getenv("WRITER_TESTING") == "1":
                return
            from ..projection_tasks import try_wake_projection_scanner

            sender = try_wake_projection_scanner

        sender(
            tenant_id=self.tenant_id,
            project_id=self.project_id,
        )

    def _checkpoint(
        self,
        command: CanonicalSubsectionCommand,
        commit: CanonicalCommitResult,
        critical_status: str,
    ) -> None:
        self.checkpoint_writer(
            {
                "generation_attempt_id": command.generation_attempt_id,
                "idempotency_key": commit.idempotency_key,
                "document_id": command.document_id,
                "current_revision_id": commit.revision_id,
                "current_state_version_id": commit.state_version_id,
                "last_commit_id": commit.commit_id,
                "critical_projection_status": critical_status,
            }
        )

