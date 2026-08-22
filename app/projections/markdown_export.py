"""Atomic, scoped Markdown materialization from Canon."""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from pathlib import Path

from ..canonical.hashing import sha256_text
from ..canonical.models import CanonicalSubsection
from ..canonical.projection_ports import ProjectionMessage, ProjectionRecord, ProjectionScope
from ..canonical.projection_registry import DEFAULT_PROJECTOR_REGISTRY
from ..canonical.projection_replay import CanonicalProjectionReplay
from ..config import settings
from .base import ProjectionAdapterBase, normalized_records


class MarkdownExportProjectionAdapter(ProjectionAdapterBase):
    spec = DEFAULT_PROJECTOR_REGISTRY.get("markdown_export")

    def __init__(self, session_factory, scope: ProjectionScope, task_id: str, *, root: str | None = None) -> None:
        super().__init__(scope, task_id)
        self.session_factory = session_factory
        self.root = Path(root or settings.PROJECTION_MARKDOWN_ROOT).resolve()

    def _paths(self):
        import hashlib
        tenant = hashlib.sha256(self.scope.tenant_id.encode()).hexdigest()
        project = hashlib.sha256(self.scope.project_id.encode()).hexdigest()
        directory = (self.root / tenant).resolve()
        path = (directory / f"{project}.md").resolve()
        meta = (directory / f"{project}.json").resolve()
        for candidate in (directory, path, meta):
            if candidate != self.root and self.root not in candidate.parents:
                raise ValueError("projection path escapes configured root")
        return directory, path, meta

    def _record(self, message: ProjectionMessage) -> ProjectionRecord:
        revision = self._validate_message(message)
        with self.session_factory() as session:
            subsection = session.get(CanonicalSubsection, revision["subsection_id"])
            if (
                subsection is None
                or subsection.tenant_id != self.scope.tenant_id
                or subsection.project_id != self.scope.project_id
            ):
                raise ValueError("projection subsection is outside adapter scope")
            content = CanonicalProjectionReplay(session).materialize_document_at(
                self.scope,
                message.stream_position,
                document_id=subsection.document_id,
            )
        payload = {
            "content": content,
            "content_hash": sha256_text(content),
            "projection_event_id": message.projection_event_id,
            "document_id": subsection.document_id,
            "stream_position": message.stream_position,
        }
        return ProjectionRecord(
            record_id=f"markdown:{self.blackboard_hash()}",
            stream_position=message.stream_position,
            commit_id=message.commit_id,
            revision_id=message.revision_id,
            payload=payload,
        )

    def blackboard_hash(self):
        import hashlib
        return hashlib.sha256(f"{self.scope.tenant_id}\0{self.scope.project_id}".encode()).hexdigest()

    def apply(self, message: ProjectionMessage):
        record = self._record(message)
        directory, path, meta = self._paths()
        directory.mkdir(parents=True, exist_ok=True)
        if meta.exists():
            current = ProjectionRecord.model_validate(
                json.loads(meta.read_text(encoding="utf-8"))["record"]
            )
            if record.stream_position < current.stream_position:
                raise ValueError("markdown export is stale")
            if record.stream_position == current.stream_position:
                if record != current:
                    raise ValueError("markdown export conflict at stream_position")
                if path.exists() and path.read_text(encoding="utf-8") == record.payload["content"]:
                    return self._receipt(message, (record,))
        temp = path.with_suffix(path.suffix + ".tmp")
        meta_temp = meta.with_suffix(meta.suffix + ".tmp")
        temp.write_text(record.payload["content"], encoding="utf-8")
        meta_temp.write_text(
            json.dumps(
                {"record": record.model_dump(mode="json")},
                ensure_ascii=False,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        os.replace(temp, path)
        os.replace(meta_temp, meta)
        return self._receipt(message, (record,))

    def expected_records(self, messages: Iterable[ProjectionMessage]):
        records = [self._record(message) for message in messages]
        if not records:
            return ()
        latest = max(records, key=lambda record: record.stream_position)
        return normalized_records((latest,))

    def actual_records(self, scope: ProjectionScope):
        self._validate_actual_scope(scope)
        _, path, meta = self._paths()
        if not path.exists() or not meta.exists():
            return ()
        raw = json.loads(meta.read_text(encoding="utf-8"))["record"]
        record = ProjectionRecord.model_validate(raw)
        content = path.read_text(encoding="utf-8")
        record = record.model_copy(
            update={
                "payload": {
                    **record.payload,
                    "content": content,
                    "content_hash": sha256_text(content),
                }
            }
        )
        return normalized_records((record,))

    def clear(self, scope: ProjectionScope) -> None:
        self._validate_actual_scope(scope)
        _, path, meta = self._paths()
        for candidate in (path, meta):
            if candidate.exists():
                candidate.unlink()
