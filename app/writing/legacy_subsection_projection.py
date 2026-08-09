"""Post-commit adapters for the fixed P2 legacy projection manifest."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..canonical.contracts import FrozenArtifact
from ..canonical.hashing import sha256_text
from ..canonical.models import CanonicalCommit, DocumentRevision, OutboxEvent
from ..canonical.projection_ports import ProjectionMessage, ProjectionPort
from ..config import settings
from ..utils.text_chunker import chunk_text


class LegacyProjectionError(RuntimeError):
    """A classified projection failure; canonical rows remain untouched."""

    def __init__(self, projection_name: str, cause: Exception | str):
        self.projection_name = projection_name
        self.cause = cause
        super().__init__(f"{projection_name}: {cause}")


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


class LegacySubsectionProjection:
    """Project one independently retryable outbox row into one legacy sink."""

    def __init__(
        self,
        session: Session,
        tenant_id: str,
        project_id: str,
        *,
        world_event_sink: Callable[[LegacyProjectionEnvelope], None] | None = None,
        handover_sink: Callable[[LegacyProjectionEnvelope], None] | None = None,
        handover_recorder: Any = None,
        vector_store: Any = None,
        non_blocking_sinks: Mapping[
            str, Callable[[LegacyProjectionEnvelope], None]
        ] | None = None,
    ) -> None:
        self.session = session
        self.tenant_id = tenant_id
        self.project_id = project_id
        self.world_event_sink = world_event_sink
        self.handover_sink = handover_sink
        self.handover_recorder = handover_recorder
        self.vector_store = vector_store
        self.non_blocking_sinks = dict(non_blocking_sinks or {})

    def as_projectors(self) -> dict[str, ProjectionPort]:
        return {
            name: self.project
            for name in (
                "legacy_world_event",
                "handover_context",
                "chroma_story_chunks",
                "redis_stream",
                "task_preview",
                "markdown_export",
                "analytics",
            )
        }

    def project(self, message: ProjectionMessage) -> None:
        if not isinstance(message, ProjectionMessage):
            raise TypeError("projection input must be a committed ProjectionMessage")
        try:
            envelope = self._load_committed(message)
            if message.projection_name == "legacy_world_event":
                self._require(self.world_event_sink, message.projection_name)(envelope)
            elif message.projection_name == "handover_context":
                if self.handover_sink is not None:
                    self.handover_sink(envelope)
                elif self.handover_recorder is not None:
                    self.handover_recorder.capture_canonical_projection(envelope)
                else:
                    raise LookupError("handover sink is unavailable")
            elif message.projection_name == "chroma_story_chunks":
                self._project_chroma(envelope)
            else:
                self._require(
                    self.non_blocking_sinks.get(message.projection_name),
                    message.projection_name,
                )(envelope)
        except LegacyProjectionError:
            raise
        except Exception as exc:
            raise LegacyProjectionError(message.projection_name, exc) from exc

    @staticmethod
    def _require(value: Any, projection_name: str) -> Any:
        if value is None:
            raise LookupError(f"{projection_name} sink is unavailable")
        return value

    def _load_committed(self, message: ProjectionMessage) -> LegacyProjectionEnvelope:
        outbox = self.session.scalar(
            select(OutboxEvent)
            .join(CanonicalCommit, CanonicalCommit.id == OutboxEvent.commit_id)
            .where(
                OutboxEvent.id == message.event_id,
                OutboxEvent.commit_id == message.commit_id,
                OutboxEvent.projection_name == message.projection_name,
                OutboxEvent.tenant_id == self.tenant_id,
                OutboxEvent.project_id == self.project_id,
                CanonicalCommit.tenant_id == self.tenant_id,
                CanonicalCommit.project_id == self.project_id,
                CanonicalCommit.status == "committed",
            )
        )
        if outbox is None:
            raise LegacyProjectionError(
                message.projection_name, "committed outbox row not found in scope"
            )
        revision_id = str(outbox.payload_json.get("revision_id") or "")
        revision = self.session.scalar(
            select(DocumentRevision).where(
                DocumentRevision.id == revision_id,
                DocumentRevision.commit_id == message.commit_id,
                DocumentRevision.tenant_id == self.tenant_id,
                DocumentRevision.project_id == self.project_id,
                DocumentRevision.status == "accepted",
            )
        )
        if revision is None or sha256_text(revision.content) != revision.content_hash:
            raise LegacyProjectionError(
                message.projection_name, "accepted revision cannot be recomputed"
            )
        metadata = dict(revision.metadata_json or {})
        return LegacyProjectionEnvelope(
            event_id=message.event_id,
            tenant_id=self.tenant_id,
            project_id=self.project_id,
            commit_id=message.commit_id,
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

    def _project_chroma(self, envelope: LegacyProjectionEnvelope) -> None:
        vector_store = self._require(self.vector_store, "chroma_story_chunks")
        for ordinal, text in enumerate(
            chunk_text(envelope.draft, settings.CHUNK_SIZE, settings.CHUNK_OVERLAP),
            start=1,
        ):
            chunk_id = "canonical-chunk-" + sha256_text(
                f"{envelope.commit_id}:{ordinal}:{envelope.content_hash}"
            )
            vector_store.add_text(
                text,
                {
                    "task_id": envelope.task_id,
                    "section": envelope.section,
                    "subsection": envelope.subsection,
                    "title": envelope.title,
                    "topic": envelope.topic,
                    "commit_id": envelope.commit_id,
                    "revision_id": envelope.revision_id,
                    "content_hash": envelope.content_hash,
                    "chunk_ordinal": ordinal,
                },
                document_id=chunk_id,
            )
        vector_store.enforce_task_limit(envelope.task_id)

