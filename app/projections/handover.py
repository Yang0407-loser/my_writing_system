"""Rebuildable adapter for content-addressed subsection handovers."""

from __future__ import annotations

from collections.abc import Iterable

from ..canonical.hashing import sha256_text
from ..canonical.projection_ports import ProjectionMessage, ProjectionRecord, ProjectionScope
from ..canonical.projection_registry import DEFAULT_PROJECTOR_REGISTRY
from ..writing.subsection_handover_history import (
    HANDOVER_FIELD_NAMES,
    HandoverFieldArtifact,
    SubsectionHandoverRecord,
    observation_from_note,
    sha256_json,
    task_id_hash,
    value_item_count,
)
from ..writing.subsection_handover_persistence import SubsectionHandoverHistoryRecorder
from .base import ProjectionAdapterBase, normalized_records
from .legacy_scope import LegacyScopeBindingStore


class HandoverProjectionAdapter(ProjectionAdapterBase):
    spec = DEFAULT_PROJECTOR_REGISTRY.get("handover_context")

    def __init__(self, blackboard, scope: ProjectionScope, task_id: str) -> None:
        super().__init__(scope, task_id)
        self.recorder = SubsectionHandoverHistoryRecorder(blackboard, task_id)
        self.bindings = LegacyScopeBindingStore(blackboard)

    def _record_identity(self, message: ProjectionMessage):
        revision = self._validate_message(message)
        metadata = revision["metadata"]
        content_hash = str(revision.get("content_hash") or sha256_text(revision["content"]))
        section = int(metadata.get("section") or 1)
        subsection = int(metadata.get("subsection") or 1)
        record_id = (
            f"subsection-handover:{task_id_hash(self.task_id)}:"
            f"S{section}.{subsection}:{content_hash}"
        )
        return revision, metadata, content_hash, section, subsection, record_id

    def apply(self, message: ProjectionMessage):
        if self.recorder.unscoped_records():
            self.bindings.require(task_id=self.task_id, scope=self.scope)
            raise ValueError(
                "legacy Handover ownership is approved but migration clear is required"
            )
        revision, metadata, content_hash, section, subsection, _ = self._record_identity(message)
        note = metadata.get("handover_candidate")
        observation = observation_from_note(note)
        stored_id = self.recorder.capture_committed(
            section=section,
            subsection=subsection,
            output_sha256=content_hash,
            prompt_messages_hash=str(metadata.get("prompt_hash") or "unknown"),
            commit_idempotency_key=message.commit_id,
            handover_note=note if isinstance(note, dict) else None,
            observation=observation,
            canonical_tenant_id=message.tenant_id,
            canonical_project_id=message.project_id,
            stream_position=message.stream_position,
            revision_id=message.revision_id,
        )
        if stored_id is None:
            raise RuntimeError("handover sink rejected canonical record")
        records = self.actual_records(self.scope)
        expected_record = self.expected_records((message,))[0]
        actual = next(
            (record for record in records if record.record_id == expected_record.record_id),
            None,
        )
        if actual is None or (
            actual.stream_position <= message.stream_position and actual != expected_record
        ):
            raise RuntimeError("handover sink did not converge to the canonical record")
        return self._receipt(message, (actual,))

    def _actual_for_message(self, message: ProjectionMessage):
        return tuple(
            record
            for record in self.actual_records(self.scope)
            if record.commit_id == message.commit_id
            and record.stream_position == message.stream_position
        )

    def expected_records(
        self, messages: Iterable[ProjectionMessage]
    ) -> tuple[ProjectionRecord, ...]:
        expected_by_id = {}
        for message in messages:
            revision, metadata, content_hash, section, subsection, record_id = (
                self._record_identity(message)
            )
            note = metadata.get("handover_candidate")
            observation = observation_from_note(note)
            hashed_task = task_id_hash(self.task_id)
            source_id = (
                f"writer-handover:{hashed_task}:S{section}.{subsection}:{content_hash}"
            )
            fields = tuple(
                HandoverFieldArtifact(
                    field_name=name,
                    value=note[name],
                    value_hash=sha256_json(note[name]),
                    item_count=value_item_count(note[name]),
                    source_id=f"{source_id}:{name}",
                    source_hash=content_hash,
                )
                for name in HANDOVER_FIELD_NAMES
                if isinstance(note, dict) and name in note
            )
            record = SubsectionHandoverRecord(
                record_id=record_id,
                canonical_tenant_id=message.tenant_id,
                canonical_project_id=message.project_id,
                stream_position=message.stream_position,
                revision_id=message.revision_id,
                task_id_hash=hashed_task,
                section=section,
                subsection=subsection,
                output_sha256=content_hash,
                prompt_messages_hash=str(metadata.get("prompt_hash") or "unknown"),
                commit_idempotency_key=message.commit_id,
                handover_source_id=source_id,
                handover_note_hash=observation.note_hash,
                execution_status=observation.execution_status,
                fields=fields,
                field_count=len(fields),
                producer_version=observation.producer_version,
                error_type=observation.error_type,
                skip_reason=observation.skip_reason,
                contract_version=observation.contract_version,
                typed_contract_hash=observation.typed_contract_hash,
                accepted_claim_count=observation.accepted_claim_count,
                rejected_claim_count=observation.rejected_claim_count,
                rejection_counts=observation.rejection_counts,
                rejection_shape_skeletons=observation.rejection_shape_skeletons,
                next_boundary_hash=observation.next_boundary_hash,
                source_manifest=observation.source_manifest,
                payload_version=observation.payload_version,
                source_registry_hash=observation.source_registry_hash,
                compact_payload_hash=observation.compact_payload_hash,
                compact_payload=observation.compact_payload,
                raw_output_tokens=observation.raw_output_tokens,
                finish_reason=observation.finish_reason,
                truncation_status=observation.truncation_status,
                restored_claim_count=observation.restored_claim_count,
                locally_rejected_claim_count=observation.locally_rejected_claim_count,
                created_at="ignored-by-projection-record",
            )
            projected = self._projection_record(record)
            existing = expected_by_id.get(projected.record_id)
            if existing is None or projected.stream_position > existing.stream_position:
                expected_by_id[projected.record_id] = projected
            elif projected.stream_position == existing.stream_position and projected != existing:
                raise ValueError("handover expected-record conflict at same stream_position")
        return normalized_records(expected_by_id.values())

    @staticmethod
    def _projection_record(record: SubsectionHandoverRecord) -> ProjectionRecord:
        return ProjectionRecord(
            record_id=record.record_id,
            stream_position=record.stream_position,
            commit_id=record.commit_idempotency_key,
            revision_id=record.revision_id,
            payload=record.model_dump(
                mode="json",
                exclude={
                    "created_at",
                    "canonical_tenant_id",
                    "canonical_project_id",
                    "stream_position",
                    "revision_id",
                },
            ),
        )

    def actual_records(self, scope: ProjectionScope) -> tuple[ProjectionRecord, ...]:
        self._validate_actual_scope(scope)
        if self.recorder.unscoped_records():
            self.bindings.require(task_id=self.task_id, scope=scope)
            raise ValueError(
                "legacy Handover ownership is approved but migration clear is required"
            )
        return normalized_records(
            self._projection_record(record)
            for record in self.recorder.list_canonical_records(
                tenant_id=scope.tenant_id, project_id=scope.project_id
            )
        )

    def clear(self, scope: ProjectionScope) -> None:
        self._validate_actual_scope(scope)
        if self.recorder.unscoped_records():
            self.bindings.require(task_id=self.task_id, scope=scope)
            self.recorder.clear_unscoped_records()
        self.recorder.clear_canonical_records(
            tenant_id=scope.tenant_id, project_id=scope.project_id
        )
