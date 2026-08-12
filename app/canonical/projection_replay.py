"""Reconstruct deterministic projection messages directly from Canon."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from .contracts import FrozenArtifact
from .hashing import sha256_json, sha256_text
from .models import (
    CanonicalCommit,
    CanonicalDocument,
    CanonicalStateVersion,
    CanonicalSubsection,
    DocumentRevision,
    EventLedger,
    OutboxEvent,
    ProjectionDelivery,
)
from .projection_ports import ProjectionMessage, ProjectionScope
from .projection_registry import (
    DEFAULT_PROJECTOR_REGISTRY,
    ProjectorRegistry,
    projection_event_id,
)


class LegacyProjectionEnvelope(FrozenArtifact):
    event_id: str
    tenant_id: str
    project_id: str
    commit_id: str
    revision_id: str
    content_hash: str
    task_id: str
    section: int = Field(ge=1)
    subsection: int = Field(ge=1)
    ordinal: int = Field(ge=1)
    title: str
    topic: str
    draft: str
    prompt_hash: str
    handover_candidate: dict[str, Any] | None = None
    generation_metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def provenance(self) -> dict[str, str]:
        return {
            "commit_id": self.commit_id,
            "revision_id": self.revision_id,
            "content_hash": self.content_hash,
        }


class CanonicalProjectionReplay:
    """Read validated projection inputs from tenant/project-scoped Canon rows."""

    def __init__(
        self,
        session: Session,
        *,
        registry: ProjectorRegistry = DEFAULT_PROJECTOR_REGISTRY,
    ) -> None:
        self.session = session
        self.registry = registry

    def iter_messages(
        self,
        scope: ProjectionScope,
        projector_id: str,
        after_position: int,
        through_position: int,
    ) -> Iterator[ProjectionMessage]:
        if after_position < 0 or through_position < after_position:
            raise ValueError("invalid replay position range")
        spec = self.registry.get(projector_id)
        rows = self.session.execute(
            self._canon_statement(scope).where(
                CanonicalCommit.stream_position > after_position,
                CanonicalCommit.stream_position <= through_position,
            )
        ).all()
        for commit, revision, state in rows:
            yield self._message(
                scope,
                spec.projector_id,
                spec.barrier_kind,
                commit,
                revision,
                state,
            )

    def message_for_delivery(self, delivery_id: str) -> ProjectionMessage:
        row = self.session.execute(
            select(
                ProjectionDelivery,
                OutboxEvent,
                CanonicalCommit,
                DocumentRevision,
                CanonicalStateVersion,
            )
            .join(
                OutboxEvent,
                OutboxEvent.id == ProjectionDelivery.outbox_event_id,
            )
            .join(
                CanonicalCommit,
                CanonicalCommit.id == OutboxEvent.commit_id,
            )
            .join(
                DocumentRevision,
                DocumentRevision.commit_id == CanonicalCommit.id,
            )
            .join(
                CanonicalStateVersion,
                CanonicalStateVersion.commit_id == CanonicalCommit.id,
            )
            .where(
                ProjectionDelivery.id == delivery_id,
                ProjectionDelivery.tenant_id == OutboxEvent.tenant_id,
                ProjectionDelivery.project_id == OutboxEvent.project_id,
                ProjectionDelivery.projector_id == OutboxEvent.projection_name,
                ProjectionDelivery.stream_position == OutboxEvent.stream_position,
                OutboxEvent.tenant_id == CanonicalCommit.tenant_id,
                OutboxEvent.project_id == CanonicalCommit.project_id,
                OutboxEvent.stream_position == CanonicalCommit.stream_position,
                DocumentRevision.tenant_id == CanonicalCommit.tenant_id,
                DocumentRevision.project_id == CanonicalCommit.project_id,
                DocumentRevision.status == "accepted",
                CanonicalStateVersion.tenant_id == CanonicalCommit.tenant_id,
                CanonicalStateVersion.project_id == CanonicalCommit.project_id,
                CanonicalCommit.status == "committed",
            )
        ).one_or_none()
        if row is None:
            raise ValueError("delivery is not joined to committed Canon in scope")
        delivery, outbox, commit, revision, state = row
        if (
            outbox.payload_json.get("revision_id") != revision.id
            or outbox.payload_json.get("state_version_id") != state.id
        ):
            raise ValueError("delivery Envelope does not identify matching Canon")
        message = self._message(
            ProjectionScope(commit.tenant_id, commit.project_id),
            delivery.projector_id,
            delivery.barrier_kind,
            commit,
            revision,
            state,
        )
        return message.model_copy(
            update={
                "outbox_event_id": outbox.id,
                "delivery_id": delivery.id,
            }
        )

    def materialize_document_at(
        self,
        scope: ProjectionScope,
        stream_position: int,
        *,
        document_id: str | None = None,
    ) -> str:
        if stream_position < 0:
            raise ValueError("stream_position must be non-negative")
        document_ids = tuple(
            self.session.scalars(
                select(CanonicalDocument.id)
                .where(
                    CanonicalDocument.tenant_id == scope.tenant_id,
                    CanonicalDocument.project_id == scope.project_id,
                )
                .order_by(CanonicalDocument.id)
            ).all()
        )
        if document_id is None:
            if len(document_ids) > 1:
                raise ValueError(
                    "project has multiple documents; document_id is required"
                )
            if not document_ids:
                return ""
            document_id = document_ids[0]
        elif document_id not in document_ids:
            raise ValueError(f"document is outside scope or missing: {document_id}")
        newer_commit = aliased(CanonicalCommit)
        newer_revision = aliased(DocumentRevision)
        rows = self.session.execute(
            select(CanonicalSubsection, DocumentRevision)
            .join(
                DocumentRevision,
                DocumentRevision.subsection_id == CanonicalSubsection.id,
            )
            .join(
                CanonicalCommit,
                CanonicalCommit.id == DocumentRevision.commit_id,
            )
            .where(
                CanonicalSubsection.tenant_id == scope.tenant_id,
                CanonicalSubsection.project_id == scope.project_id,
                CanonicalSubsection.document_id == document_id,
                DocumentRevision.tenant_id == scope.tenant_id,
                DocumentRevision.project_id == scope.project_id,
                DocumentRevision.status == "accepted",
                CanonicalCommit.tenant_id == scope.tenant_id,
                CanonicalCommit.project_id == scope.project_id,
                CanonicalCommit.status == "committed",
                CanonicalCommit.stream_position <= stream_position,
                ~select(newer_revision.id)
                .join(newer_commit, newer_commit.id == newer_revision.commit_id)
                .where(
                    newer_revision.subsection_id == DocumentRevision.subsection_id,
                    newer_revision.tenant_id == scope.tenant_id,
                    newer_revision.project_id == scope.project_id,
                    newer_revision.status == "accepted",
                    newer_commit.tenant_id == scope.tenant_id,
                    newer_commit.project_id == scope.project_id,
                    newer_commit.status == "committed",
                    newer_commit.stream_position <= stream_position,
                    newer_commit.stream_position > CanonicalCommit.stream_position,
                )
                .exists(),
            )
            .order_by(CanonicalSubsection.ordinal, CanonicalSubsection.id)
        ).all()
        contents = []
        for _, revision in rows:
            self._validate_revision(revision)
            contents.append(revision.content)
        return "\n\n".join(contents)

    def legacy_envelope(self, message: ProjectionMessage) -> LegacyProjectionEnvelope:
        if not isinstance(message, ProjectionMessage):
            raise TypeError("projection input must be a committed ProjectionMessage")
        scope = ProjectionScope(message.tenant_id, message.project_id)
        row = self.session.execute(
            self._canon_statement(scope).where(
                CanonicalCommit.id == message.commit_id,
                CanonicalCommit.stream_position == message.stream_position,
                DocumentRevision.id == message.revision_id,
                CanonicalStateVersion.id == message.state_version_id,
            )
        ).one_or_none()
        if row is None:
            raise ValueError("message is not backed by committed Canon in scope")
        commit, revision, state = row
        expected = self._message(
            scope,
            message.projector_id,
            message.barrier_kind,
            commit,
            revision,
            state,
        )
        referenced_ids = {
            "commit_id": commit.id,
            "revision_id": revision.id,
            "state_version_id": state.id,
            "candidate_hash": commit.candidate_hash,
        }
        if (
            message.projection_event_id != expected.projection_event_id
            or message.event_type != expected.event_type
            or any(
                key in message.payload and message.payload[key] != value
                for key, value in referenced_ids.items()
            )
        ):
            raise ValueError("message semantic content does not match Canon")
        metadata = dict(revision.metadata_json or {})
        return LegacyProjectionEnvelope(
            event_id=message.projection_event_id,
            tenant_id=scope.tenant_id,
            project_id=scope.project_id,
            commit_id=commit.id,
            revision_id=revision.id,
            content_hash=revision.content_hash,
            task_id=str(metadata.get("task_id") or ""),
            section=int(metadata.get("section") or 1),
            subsection=int(metadata.get("subsection") or 1),
            ordinal=int(metadata.get("ordinal") or 1),
            title=str(metadata.get("title") or ""),
            topic=str(metadata.get("topic") or ""),
            draft=revision.content,
            prompt_hash=str(metadata.get("prompt_hash") or "unknown"),
            handover_candidate=metadata.get("handover_candidate"),
            generation_metadata=dict(metadata.get("generation_metadata") or {}),
        )

    @staticmethod
    def _canon_statement(scope: ProjectionScope):
        return (
            select(CanonicalCommit, DocumentRevision, CanonicalStateVersion)
            .join(
                DocumentRevision,
                DocumentRevision.commit_id == CanonicalCommit.id,
            )
            .join(
                CanonicalStateVersion,
                CanonicalStateVersion.commit_id == CanonicalCommit.id,
            )
            .where(
                CanonicalCommit.tenant_id == scope.tenant_id,
                CanonicalCommit.project_id == scope.project_id,
                CanonicalCommit.status == "committed",
                DocumentRevision.tenant_id == scope.tenant_id,
                DocumentRevision.project_id == scope.project_id,
                DocumentRevision.status == "accepted",
                CanonicalStateVersion.tenant_id == scope.tenant_id,
                CanonicalStateVersion.project_id == scope.project_id,
            )
            .order_by(CanonicalCommit.stream_position, CanonicalCommit.id)
        )

    def _message(
        self,
        scope: ProjectionScope,
        projector_id: str,
        barrier_kind: str,
        commit: CanonicalCommit,
        revision: DocumentRevision,
        state: CanonicalStateVersion,
    ) -> ProjectionMessage:
        self._validate_revision(revision)
        if sha256_json(state.state_json) != state.state_hash:
            raise ValueError("state cannot be recomputed")
        ledger = self.session.scalars(
            select(EventLedger)
            .where(
                EventLedger.tenant_id == scope.tenant_id,
                EventLedger.project_id == scope.project_id,
                EventLedger.commit_id == commit.id,
            )
            .order_by(EventLedger.ordinal, EventLedger.id)
        ).all()
        payload = {
            "commit_id": commit.id,
            "revision_id": revision.id,
            "state_version_id": state.id,
            "candidate_hash": commit.candidate_hash,
            "revision": {
                "subsection_id": revision.subsection_id,
                "revision_number": revision.revision_number,
                "parent_revision_id": revision.parent_revision_id,
                "content": revision.content,
                "content_hash": revision.content_hash,
                "metadata": revision.metadata_json,
            },
            "state": {
                "parent_state_version_id": state.parent_state_version_id,
                "transition_version": state.transition_version,
                "schema_version": state.schema_version,
                "state_json": state.state_json,
                "state_hash": state.state_hash,
            },
            "ledger_events": [
                {
                    "event_id": event.id,
                    "event_type": event.event_type,
                    "payload": event.payload_json,
                    "evidence_refs": event.evidence_refs_json,
                    "ordinal": event.ordinal,
                }
                for event in ledger
            ],
        }
        return ProjectionMessage(
            projection_event_id=projection_event_id(projector_id, commit.id),
            tenant_id=scope.tenant_id,
            project_id=scope.project_id,
            commit_id=commit.id,
            revision_id=revision.id,
            state_version_id=state.id,
            projector_id=projector_id,
            barrier_kind=barrier_kind,
            event_type="canonical.subsection.committed",
            stream_position=commit.stream_position,
            payload=payload,
        )

    @staticmethod
    def _validate_revision(revision: DocumentRevision) -> None:
        if sha256_text(revision.content) != revision.content_hash:
            raise ValueError("revision cannot be recomputed")
