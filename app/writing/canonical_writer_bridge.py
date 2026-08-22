"""Coordinator-owned bridge from the legacy Writer loop to Canonical runtime."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Callable

from ..canonical.models import DocumentRevision
from ..config import CanonicalSettings
from .canonical_subsection_runtime import CanonicalSubsectionCommand
from .contracts import CommitArtifact, GenerationArtifact, SubsectionInput


class CanonicalProjectionPending(RuntimeError):
    """A commit exists but its critical compatibility projections are not ready."""


@dataclass(frozen=True)
class CanonicalWriterOutcome:
    draft: str
    handover_note: dict[str, Any]
    handover_observation: dict[str, Any]
    generation_artifact: GenerationArtifact
    commit_artifact: CommitArtifact
    runtime_result: Any


class CanonicalWriterBridge:
    """Route one prepared Writer subsection without exposing SQL to Writer."""

    def __init__(
        self,
        *,
        writer: Any,
        runtime: Any,
        rollout: CanonicalSettings,
        document_id: str,
        runtime_executor: Callable[..., Any],
        pre_foundation_resume: bool = False,
        close_callback: Callable[[], None] | None = None,
    ) -> None:
        self.writer = writer
        self.runtime = runtime
        self.rollout = rollout
        self.document_id = document_id
        self.runtime_executor = runtime_executor
        self.pre_foundation_resume = pre_foundation_resume
        self.close_callback = close_callback

    def selects(self, *, task_id: str, subsection_id: str) -> bool:
        path = self.rollout.resolve_path(
            task_id,
            subsection_id,
            pre_foundation_resume=self.pre_foundation_resume,
        )
        if path == "canonical" and not subsection_id:
            raise RuntimeError("canonical subsection binding is missing; fail closed")
        return path == "canonical"

    def execute(
        self,
        *,
        prepared: SubsectionInput,
        subsection_id: str,
        ordinal: int,
        title: str,
        topic: str,
        mandatory_events_text: str,
        token_by_source: dict[str, int] | None,
        characters: list[dict[str, Any]] | None,
        previous_texts: list[str] | None,
        prev_sub_text: str,
        target_goal: str,
        character_context: str,
        event_graph: Any,
        current_subsection: dict[str, Any] | None,
        next_subsection: dict[str, Any] | None,
        state_frame: dict[str, Any] | None,
        post_validator: Callable[[str], dict[str, Any]],
    ) -> CanonicalWriterOutcome:
        if not self.selects(
            task_id=prepared.task_id, subsection_id=subsection_id
        ):
            raise RuntimeError("legacy subsection was sent to CanonicalWriterBridge")

        repo = self.runtime.repo
        state = repo.get_current_state()
        subsection = repo.get_subsection(subsection_id)
        if state is None or subsection is None:
            raise RuntimeError("canonical scope or subsection binding is missing")
        if subsection.document_id != self.document_id:
            raise RuntimeError("canonical subsection/document binding mismatch")
        revision = repo.get_current_revision(subsection_id)
        expected_revision_id = "GENESIS" if revision is None else revision.id
        attempt_seed = (
            f"{prepared.task_id}\0{self.document_id}\0{subsection_id}"
        )
        command = CanonicalSubsectionCommand(
            task_id=prepared.task_id,
            document_id=self.document_id,
            subsection_id=subsection_id,
            generation_attempt_id=(
                "writer-" + hashlib.sha256(attempt_seed.encode()).hexdigest()
            ),
            expected_revision_id=expected_revision_id,
            expected_state_version_id=state.id,
        )
        generated = {}

        def candidate_generator(*, snapshot, base_revision_number, command):
            candidate = self.writer.generate_subsection_candidate(
                prepared=prepared,
                canonical_state_snapshot=snapshot,
                tenant_id=self.runtime.tenant_id,
                project_id=self.runtime.project_id,
                document_id=command.document_id,
                subsection_id=command.subsection_id,
                ordinal=ordinal,
                title=title,
                topic=topic,
                base_revision_number=base_revision_number,
                mandatory_events_text=mandatory_events_text,
                token_by_source=token_by_source,
                characters=characters,
                previous_texts=previous_texts,
                prev_sub_text=prev_sub_text,
                target_goal=target_goal,
                character_context=character_context,
                event_graph=event_graph,
                current_subsection=current_subsection,
                next_subsection=next_subsection,
                state_frame=state_frame,
                post_validator=post_validator,
            )
            generated["candidate"] = candidate
            return candidate

        self.runtime.candidate_generator = candidate_generator
        result = self.runtime_executor(
            self.runtime,
            command,
            rollout=self.rollout,
            pre_foundation_resume=self.pre_foundation_resume,
        )
        if result is None:
            raise RuntimeError("canonical route unexpectedly resolved to legacy")
        if result.phase != "ready":
            raise CanonicalProjectionPending(
                f"critical projection barrier is {result.critical_projection_status}"
            )

        accepted = self.runtime.session.get(
            DocumentRevision, result.commit.revision_id
        )
        if accepted is None or accepted.status != "accepted":
            raise RuntimeError("accepted canonical revision cannot be loaded")
        metadata = dict(accepted.metadata_json or {})
        generation_metadata = dict(metadata.get("generation_metadata") or {})
        candidate = generated.get("candidate")
        handover_note = dict(metadata.get("handover_candidate") or {})
        observation = dict(
            generation_metadata.get("handover_observation") or {}
        )
        attempts = list(generation_metadata.get("generation_attempts") or [])
        generation_artifact = GenerationArtifact(
            raw_output=accepted.content,
            draft=accepted.content,
            generation_attempts=attempts,
            finish_reason=str(
                generation_metadata.get("finish_reason") or "canonical_replay"
            ),
            latency_ms=0.0,
            output_hash=accepted.content_hash,
        )
        if candidate is not None:
            handover_note = dict(candidate.handover_candidate or {})
            observation = dict(
                candidate.generation_metadata.get("handover_observation") or {}
            )
        commit_artifact = CommitArtifact(
            idempotency_key=result.commit.idempotency_key,
            committed_fields=[
                "canonical.revision",
                "canonical.state",
                "canonical.ledger",
                "canonical.outbox",
                "canonical.critical_projection_barrier",
            ],
            source_hash=str(metadata.get("prompt_hash") or "canonical"),
            output_hash=result.commit.content_hash,
            checkpoint_version="canonical-foundation-v0",
            warnings=(
                ["non_blocking_projection_lagging"]
                if result.non_blocking_projection_status == "lagging"
                else []
            ),
            rollback_information={
                "automatic_rollback": False,
                "commit_id": result.commit.commit_id,
            },
            skipped_as_duplicate=result.commit.skipped_as_duplicate,
        )
        return CanonicalWriterOutcome(
            draft=accepted.content,
            handover_note=handover_note,
            handover_observation=observation,
            generation_artifact=generation_artifact,
            commit_artifact=commit_artifact,
            runtime_result=result,
        )

    def close(self) -> None:
        if self.close_callback is not None:
            self.close_callback()
