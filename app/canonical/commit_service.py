"""Dual-Head OCC and atomic Canonical Commit Service."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable
from uuid import uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .contracts import CanonicalCommitResult, PreparedCanonicalCommit
from .errors import IdempotencyConflict, RevisionConflict, StateVersionConflict
from .hashing import sha256_json, sha256_text
from .models import (
    CanonicalCommit,
    CanonicalStateVersion,
    DocumentRevision,
    EventLedger,
    IdempotencyRecord,
    OutboxEvent,
)
from .repositories import CanonicalRepository


PROJECTION_MANIFEST = (
    ("legacy_world_event", "critical"),
    ("handover_context", "critical"),
    ("chroma_story_chunks", "critical"),
    ("redis_stream", "non_blocking"),
    ("task_preview", "non_blocking"),
    ("markdown_export", "non_blocking"),
    ("analytics", "non_blocking"),
)


class CanonicalCommitService:
    """Persist one already-prepared candidate/state transition atomically.

    The supplied Session is a dedicated SQL unit of work. This method commits it
    on success and rolls it back on every exception. It never calls projections.
    """

    def __init__(
        self,
        session: Session,
        tenant_id: str,
        project_id: str,
        *,
        failure_hook: Callable[[str], None] | None = None,
    ):
        self.session = session
        self.repo = CanonicalRepository(session, tenant_id, project_id)
        self.tenant_id = tenant_id
        self.project_id = project_id
        self.failure_hook = failure_hook

    def _stage(self, name: str) -> None:
        if self.failure_hook is not None:
            self.failure_hook(name)

    def _validate_prepared(self, prepared: PreparedCanonicalCommit) -> None:
        candidate = prepared.candidate
        transition = prepared.state_transition
        if candidate.tenant_id != self.tenant_id or candidate.project_id != self.project_id:
            raise ValueError("candidate scope does not match Commit Service scope")
        if not candidate.validation.complete:
            raise ValueError("validation.complete must be true")
        if candidate.draft_hash != sha256_text(candidate.draft):
            raise ValueError("draft_hash cannot be recomputed")
        if candidate.candidate_hash != sha256_json(candidate.hash_payload()):
            raise ValueError("candidate_hash cannot be recomputed")
        if transition.candidate_hash != candidate.candidate_hash:
            raise ValueError("transition candidate_hash mismatch")
        if transition.base_state_version_id != candidate.base_state_version_id:
            raise ValueError("transition base_state_version_id mismatch")
        if transition.state_hash != sha256_json(transition.next_state_json):
            raise ValueError("state_hash cannot be recomputed")

    def _begin_unit_of_work(self) -> None:
        # The service owns a dedicated Session. Discard any read-only autobegin
        # transaction created while preparing the immutable input, then ensure
        # the reservation savepoint is nested inside a real outer transaction.
        if self.session.in_transaction():
            self.session.rollback()
        self.session.begin()
        connection = self.session.connection()
        if connection.dialect.name == "sqlite":
            connection.exec_driver_sql("BEGIN IMMEDIATE")

    def _reserve(
        self, idempotency_key: str, candidate_hash: str
    ) -> tuple[IdempotencyRecord | None, CanonicalCommitResult | None]:
        record = IdempotencyRecord(
            id=str(uuid4()),
            tenant_id=self.tenant_id,
            project_id=self.project_id,
            idempotency_key=idempotency_key,
            candidate_hash=candidate_hash,
            status="reserved",
            commit_id=None,
            result_json=None,
        )
        try:
            with self.session.begin_nested():
                self.session.add(record)
                self.session.flush()
            return record, None
        except IntegrityError:
            existing = self.repo.get_idempotency_record(
                idempotency_key, for_update=True
            )
            if existing is None:
                raise
            if existing.candidate_hash != candidate_hash:
                raise IdempotencyConflict(
                    "idempotency key already belongs to a different candidate"
                )
            if existing.status != "completed" or existing.result_json is None:
                raise RuntimeError("idempotency reservation is not completed")
            result = CanonicalCommitResult.model_validate(existing.result_json)
            return None, result.model_copy(update={"skipped_as_duplicate": True})

    def commit(
        self,
        prepared: PreparedCanonicalCommit,
        idempotency_key: str,
    ) -> CanonicalCommitResult:
        self._validate_prepared(prepared)
        candidate = prepared.candidate
        transition = prepared.state_transition
        try:
            self._begin_unit_of_work()
            reservation, duplicate = self._reserve(
                idempotency_key, candidate.candidate_hash
            )
            if duplicate is not None:
                self.session.commit()
                return duplicate
            assert reservation is not None
            self._stage("after_reservation")

            project = self.repo.get_project_for_update()
            if project is None or not project.current_state_version_id:
                raise StateVersionConflict("project is missing an explicit State Head")
            base_state = self.session.get(
                CanonicalStateVersion, transition.base_state_version_id
            )
            if (
                base_state is None
                or base_state.tenant_id != self.tenant_id
                or base_state.project_id != self.project_id
                or base_state.state_hash != sha256_json(base_state.state_json)
            ):
                raise ValueError("base state cannot be recomputed in scope")
            subsection = self.repo.get_subsection_for_update(candidate.subsection_id)
            if subsection is None:
                raise RevisionConflict("subsection is missing in scope")
            if (
                subsection.document_id != candidate.document_id
                or subsection.ordinal != candidate.ordinal
                or subsection.legacy_section != candidate.section
                or subsection.legacy_subsection != candidate.subsection
            ):
                raise RevisionConflict(
                    "Candidate binding does not match the locked canonical subsection"
                )
            current_revision = self.repo.get_current_revision(candidate.subsection_id)
            current_revision_number = (
                0 if current_revision is None else current_revision.revision_number
            )
            if current_revision_number != candidate.base_revision_number:
                raise RevisionConflict("Subsection Revision Head is stale")
            if project.current_state_version_id != transition.base_state_version_id:
                raise StateVersionConflict("Project State Head is stale")

            commit_id = str(uuid4())
            revision_id = str(uuid4())
            state_version_id = str(uuid4())
            commit = CanonicalCommit(
                id=commit_id,
                tenant_id=self.tenant_id,
                project_id=self.project_id,
                candidate_hash=candidate.candidate_hash,
                base_revision_number=candidate.base_revision_number,
                base_state_version_id=transition.base_state_version_id,
                status="committed",
            )
            self.session.add(commit)
            self.session.flush()

            revision = DocumentRevision(
                id=revision_id,
                tenant_id=self.tenant_id,
                project_id=self.project_id,
                commit_id=commit_id,
                subsection_id=candidate.subsection_id,
                revision_number=current_revision_number + 1,
                parent_revision_id=(
                    None if current_revision is None else current_revision.id
                ),
                content=candidate.draft,
                content_hash=candidate.draft_hash,
                status="accepted",
                creator="canonical-commit-service-v0",
                metadata_json={
                    "task_id": candidate.task_id,
                    "candidate_hash": candidate.candidate_hash,
                    "section": candidate.section,
                    "subsection": candidate.subsection,
                    "ordinal": candidate.ordinal,
                    "title": candidate.title,
                    "topic": candidate.topic,
                    "prompt_hash": candidate.prompt_hash,
                    "handover_candidate": candidate.handover_candidate,
                    "state_frame": candidate.state_frame,
                    "generation_metadata": candidate.generation_metadata,
                },
            )
            self.session.add(revision)
            self.session.flush()
            self._stage("after_revision")

            state_version = CanonicalStateVersion(
                id=state_version_id,
                tenant_id=self.tenant_id,
                project_id=self.project_id,
                commit_id=commit_id,
                origin="commit",
                parent_state_version_id=transition.base_state_version_id,
                transition_version=transition.transition_version,
                schema_version="canonical-state-v0",
                state_json=transition.next_state_json,
                state_hash=transition.state_hash,
            )
            self.session.add(state_version)
            self.session.flush()
            self._stage("after_state")

            for ordinal, event in enumerate(transition.ledger_events, start=1):
                self.session.add(
                    EventLedger(
                        id=str(uuid4()),
                        tenant_id=self.tenant_id,
                        project_id=self.project_id,
                        commit_id=commit_id,
                        event_type=event.event_type,
                        payload_json=event.payload,
                        evidence_refs_json=[event.provenance],
                        ordinal=ordinal,
                    )
                )
            self.session.flush()
            self._stage("after_ledger")

            outbox_ids = []
            now = datetime.now(timezone.utc)
            for projection_name, barrier_kind in PROJECTION_MANIFEST:
                outbox_id = str(uuid4())
                outbox_ids.append(outbox_id)
                self.session.add(
                    OutboxEvent(
                        id=outbox_id,
                        tenant_id=self.tenant_id,
                        project_id=self.project_id,
                        commit_id=commit_id,
                        projection_name=projection_name,
                        barrier_kind=barrier_kind,
                        event_type="canonical.subsection.committed",
                        payload_json={
                            "commit_id": commit_id,
                            "revision_id": revision_id,
                            "state_version_id": state_version_id,
                            "candidate_hash": candidate.candidate_hash,
                        },
                        status="pending",
                        attempts=0,
                        available_at=now,
                        published_at=None,
                        last_error=None,
                    )
                )
            self.session.flush()
            self._stage("after_outbox")

            subsection.current_revision_id = revision_id
            project.current_state_version_id = state_version_id
            result = CanonicalCommitResult(
                commit_id=commit_id,
                revision_id=revision_id,
                revision_number=revision.revision_number,
                state_version_id=state_version_id,
                content_hash=revision.content_hash,
                outbox_event_ids=tuple(outbox_ids),
                idempotency_key=idempotency_key,
                candidate_hash=candidate.candidate_hash,
                skipped_as_duplicate=False,
            )
            reservation.status = "completed"
            reservation.commit_id = commit_id
            reservation.result_json = result.model_dump(mode="json")
            self.session.flush()
            self._stage("before_commit")
            self.session.commit()
            self._stage("after_commit")
            return result
        except Exception:
            self.session.rollback()
            raise
