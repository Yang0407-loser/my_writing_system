"""Post-commit adapters for the fixed P2 legacy projection manifest."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from sqlalchemy.orm import Session

from ..canonical.hashing import sha256_json, sha256_text
from ..canonical.projection_ports import (
    ProjectionMessage,
    ProjectionPort,
    ProjectionReceipt,
    ProjectionScope,
)
from ..canonical.projection_registry import DEFAULT_PROJECTOR_REGISTRY
from ..canonical.projection_replay import (
    CanonicalProjectionReplay,
    LegacyProjectionEnvelope,
)
from ..config import settings
from ..utils.text_chunker import chunk_text


class LegacyProjectionError(RuntimeError):
    """A classified projection failure; canonical rows remain untouched."""

    def __init__(self, projection_name: str, cause: Exception | str):
        self.projection_name = projection_name
        self.cause = cause
        super().__init__(f"{projection_name}: {cause}")


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
        self.scope = ProjectionScope(tenant_id, project_id)
        self.replay = CanonicalProjectionReplay(session)

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

    def project(self, message: ProjectionMessage) -> ProjectionReceipt:
        if not isinstance(message, ProjectionMessage):
            raise TypeError("projection input must be a committed ProjectionMessage")
        try:
            if message.tenant_id != self.tenant_id or message.project_id != self.project_id:
                raise ValueError("projection message is outside projector scope")
            envelope = self.replay.legacy_envelope(message)
            record_count = 1
            if message.projector_id == "legacy_world_event":
                self._require(self.world_event_sink, message.projector_id)(envelope)
            elif message.projector_id == "handover_context":
                if self.handover_sink is not None:
                    self.handover_sink(envelope)
                elif self.handover_recorder is not None:
                    self.handover_recorder.capture_canonical_projection(envelope)
                else:
                    raise LookupError("handover sink is unavailable")
            elif message.projector_id == "chroma_story_chunks":
                record_count = self._project_chroma(envelope)
            else:
                self._require(
                    self.non_blocking_sinks.get(message.projector_id),
                    message.projector_id,
                )(envelope)
        except LegacyProjectionError:
            raise
        except Exception as exc:
            raise LegacyProjectionError(message.projector_id, exc) from exc
        spec = DEFAULT_PROJECTOR_REGISTRY.get(message.projector_id)
        return ProjectionReceipt(
            projection_event_id=message.projection_event_id,
            projector_id=message.projector_id,
            projector_version=spec.version,
            stream_position=message.stream_position,
            record_count=record_count,
            content_digest=sha256_json(
                {
                    "projection_event_id": message.projection_event_id,
                    "projector_id": message.projector_id,
                    "stream_position": message.stream_position,
                    "record_count": record_count,
                    "envelope": envelope,
                }
            ),
        )

    @staticmethod
    def _require(value: Any, projection_name: str) -> Any:
        if value is None:
            raise LookupError(f"{projection_name} sink is unavailable")
        return value

    def _project_chroma(self, envelope: LegacyProjectionEnvelope) -> int:
        vector_store = self._require(self.vector_store, "chroma_story_chunks")
        record_count = 0
        for ordinal, text in enumerate(
            chunk_text(envelope.draft, settings.CHUNK_SIZE, settings.CHUNK_OVERLAP),
            start=1,
        ):
            record_count = ordinal
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
        return record_count

