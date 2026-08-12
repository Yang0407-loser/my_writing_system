"""Rebuildable adapter for deterministic Chroma story chunks."""

from __future__ import annotations

from collections.abc import Iterable

from ..canonical.hashing import sha256_text
from ..canonical.projection_ports import ProjectionMessage, ProjectionRecord, ProjectionScope
from ..canonical.projection_registry import DEFAULT_PROJECTOR_REGISTRY
from ..config import settings
from ..utils.text_chunker import chunk_text
from .base import ProjectionAdapterBase, normalized_records


CANONICAL_CHUNK_METADATA = (
    "tenant_id",
    "project_id",
    "task_id",
    "section",
    "subsection",
    "title",
    "topic",
    "commit_id",
    "revision_id",
    "stream_position",
    "content_hash",
    "source_content_hash",
    "chunk_ordinal",
)


class ChromaStoryProjectionAdapter(ProjectionAdapterBase):
    spec = DEFAULT_PROJECTOR_REGISTRY.get("chroma_story_chunks")

    def __init__(
        self,
        vector_store,
        scope: ProjectionScope,
        task_id: str,
        *,
        chunk_size: int | None = None,
        overlap: int | None = None,
    ) -> None:
        super().__init__(scope, task_id)
        self.vector_store = vector_store
        self.chunk_size = settings.CHUNK_SIZE if chunk_size is None else chunk_size
        self.overlap = settings.CHUNK_OVERLAP if overlap is None else overlap

    def _records_for(self, message: ProjectionMessage) -> tuple[ProjectionRecord, ...]:
        revision = self._validate_message(message)
        metadata = revision["metadata"]
        content_hash = str(revision.get("content_hash") or sha256_text(revision["content"]))
        records = []
        for ordinal, text in enumerate(
            chunk_text(revision["content"], self.chunk_size, self.overlap), start=1
        ):
            record_id = "canonical-chunk-" + sha256_text(
                f"{message.commit_id}:{ordinal}:{content_hash}"
            )
            chunk_metadata = {
                "tenant_id": message.tenant_id,
                "project_id": message.project_id,
                "task_id": self.task_id,
                "section": int(metadata.get("section") or 1),
                "subsection": int(metadata.get("subsection") or 1),
                "title": str(metadata.get("title") or ""),
                "topic": str(metadata.get("topic") or ""),
                "commit_id": message.commit_id,
                "revision_id": message.revision_id,
                "stream_position": message.stream_position,
                "content_hash": sha256_text(text.strip()),
                "source_content_hash": content_hash,
                "chunk_ordinal": ordinal,
            }
            records.append(
                ProjectionRecord(
                    record_id=record_id,
                    stream_position=message.stream_position,
                    commit_id=message.commit_id,
                    revision_id=message.revision_id,
                    payload={"text": text.strip(), "metadata": chunk_metadata},
                )
            )
        return normalized_records(records)

    def apply(self, message: ProjectionMessage):
        records = self._records_for(message)
        for record in records:
            stored_id = self.vector_store.add_text(
                record.payload["text"],
                record.payload["metadata"],
                document_id=record.record_id,
            )
            if stored_id != record.record_id:
                raise RuntimeError("Chroma sink returned a different canonical identity")
        actual_by_id = {
            record.record_id: record for record in self.actual_records(self.scope)
        }
        converged = normalized_records(
            actual_by_id[record.record_id]
            for record in records
            if record.record_id in actual_by_id
        )
        if converged != records:
            raise RuntimeError("Chroma sink did not converge to canonical records")
        return self._receipt(message, records)

    def expected_records(
        self, messages: Iterable[ProjectionMessage]
    ) -> tuple[ProjectionRecord, ...]:
        return normalized_records(
            record for message in messages for record in self._records_for(message)
        )

    def actual_records(self, scope: ProjectionScope) -> tuple[ProjectionRecord, ...]:
        self._validate_actual_scope(scope)
        records = []
        for item in self.vector_store.list_canonical_chunks(
            tenant_id=scope.tenant_id,
            project_id=scope.project_id,
            task_id=self.task_id,
        ):
            metadata = {
                key: item["metadata"][key]
                for key in CANONICAL_CHUNK_METADATA
                if key in item["metadata"]
            }
            records.append(
                ProjectionRecord(
                    record_id=item["record_id"],
                    stream_position=int(metadata["stream_position"]),
                    commit_id=str(metadata["commit_id"]),
                    revision_id=str(metadata["revision_id"]),
                    payload={"text": item["text"], "metadata": metadata},
                )
            )
        return normalized_records(records)

    def clear(self, scope: ProjectionScope) -> None:
        self._validate_actual_scope(scope)
        self.vector_store.delete_canonical_chunks(
            tenant_id=scope.tenant_id,
            project_id=scope.project_id,
            task_id=self.task_id,
        )
