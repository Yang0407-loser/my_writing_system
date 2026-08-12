"""Fail-open persistence for existing subsection handover results."""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .subsection_handover_history import (
    HANDOVER_FIELD_NAMES,
    SUBSECTION_HANDOVER_HISTORY_KEY,
    HandoverExtractionObservation,
    HandoverFieldArtifact,
    SubsectionHandoverHistoryEnvelope,
    SubsectionHandoverRecord,
    sha256_json,
    task_id_hash,
    utc_now,
    value_item_count,
)


logger = logging.getLogger("writing_system.subsection_handover_persistence")


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return {}
        return dict(parsed) if isinstance(parsed, Mapping) else {}
    return {}


def empty_history() -> dict[str, Any]:
    return SubsectionHandoverHistoryEnvelope().model_dump(mode="json")


def normalize_history(value: Any) -> SubsectionHandoverHistoryEnvelope:
    payload = _mapping(value)
    if not payload:
        return SubsectionHandoverHistoryEnvelope()
    return SubsectionHandoverHistoryEnvelope.model_validate(payload)


def legacy_checkpoint_projection(checkpoint: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in dict(checkpoint).items()
        if key != SUBSECTION_HANDOVER_HISTORY_KEY
    }


def history_for_checkpoint(blackboard: Any, task_id: str) -> dict[str, Any] | None:
    try:
        raw = blackboard.get(task_id, SUBSECTION_HANDOVER_HISTORY_KEY)
        if not raw:
            checkpoint = blackboard.load_checkpoint(task_id) or {}
            raw = checkpoint.get(SUBSECTION_HANDOVER_HISTORY_KEY)
        history = normalize_history(raw)
    except Exception:
        return None
    records = {
        record_id: record
        for record_id, record in history.records.items()
        if SubsectionHandoverHistoryRecorder._record_kind(record) == "legacy"
    }
    history = history.model_copy(update={"records": records})
    if not history.records and not history.pending and not history.errors:
        return None
    return history.model_dump(mode="json")


def merge_history_into_analysis(
    analysis: Mapping[str, Any] | None, history: Any
) -> dict[str, Any]:
    merged = _mapping(analysis)
    envelope = normalize_history(history)
    if envelope.records or envelope.pending or envelope.errors:
        merged[SUBSECTION_HANDOVER_HISTORY_KEY] = envelope.model_dump(mode="json")
    return merged


def load_task_history_read_only(
    db_path: str, task_id: str
) -> SubsectionHandoverHistoryEnvelope | None:
    """Read an existing TaskStore without initializing or migrating it."""
    path = Path(db_path)
    if not path.exists():
        return None
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        row = connection.execute(
            "SELECT analysis_json FROM task_history WHERE task_id = ?", (task_id,)
        ).fetchone()
    except sqlite3.Error:
        return None
    finally:
        connection.close()
    if not row:
        return None
    payload = _mapping(row[0]).get(SUBSECTION_HANDOVER_HISTORY_KEY)
    if not payload:
        return None
    try:
        return normalize_history(payload)
    except Exception:
        return None


def history_source_status(value: Any) -> str:
    history = normalize_history(value)
    return (
        "available"
        if history.records
        else "historical_subsection_handover_unavailable"
    )


class SubsectionHandoverHistoryRecorder:
    """Mirrors a handover result only after its subsection commit succeeds."""

    def __init__(self, blackboard: Any, task_id: str):
        self.blackboard = blackboard
        self.task_id = task_id

    def _load(self) -> SubsectionHandoverHistoryEnvelope:
        raw = self.blackboard.get(self.task_id, SUBSECTION_HANDOVER_HISTORY_KEY)
        if not raw:
            checkpoint = self.blackboard.load_checkpoint(self.task_id) or {}
            raw = checkpoint.get(SUBSECTION_HANDOVER_HISTORY_KEY)
        return normalize_history(raw)

    def _save(self, envelope: SubsectionHandoverHistoryEnvelope) -> None:
        self.blackboard.set(
            self.task_id,
            SUBSECTION_HANDOVER_HISTORY_KEY,
            envelope.model_dump(mode="json"),
        )

    def _canonical_namespace(self, tenant_id: str, project_id: str) -> str:
        scope_hash = sha256_json({"tenant_id": tenant_id, "project_id": project_id})
        return f"canonical:handover:{task_id_hash(self.task_id)}:{scope_hash}"

    @staticmethod
    def _physical_record_key(
        record_id: str,
        *,
        canonical_tenant_id: str | None,
        canonical_project_id: str | None,
    ) -> str:
        if canonical_tenant_id is None or canonical_project_id is None:
            return record_id
        scope_hash = sha256_json(
            {"tenant_id": canonical_tenant_id, "project_id": canonical_project_id}
        )
        return f"canonical:{scope_hash}:{record_id}"

    def _record_error(
        self,
        *,
        section: int,
        subsection: int,
        phase: str,
        error: Exception,
        output_sha256: str,
        elapsed_ms: float,
    ) -> None:
        try:
            envelope = self._load()
            errors = (
                *envelope.errors,
                {
                    "task_id_hash": task_id_hash(self.task_id),
                    "section": section,
                    "subsection": subsection,
                    "phase": phase,
                    "error_type": type(error).__name__,
                    "output_sha256": output_sha256,
                    "elapsed_ms": round(elapsed_ms, 3),
                    "production_effect": False,
                },
            )
            self._save(envelope.model_copy(update={"errors": errors[-50:]}))
        except Exception:
            pass
        logger.warning(
            "subsection_handover_persistence_failed phase=%s error_type=%s",
            phase,
            type(error).__name__,
        )

    def capture_committed(
        self,
        *,
        section: int,
        subsection: int,
        output_sha256: str,
        prompt_messages_hash: str,
        commit_idempotency_key: str,
        handover_note: dict | None,
        observation: HandoverExtractionObservation,
        canonical_tenant_id: str | None = None,
        canonical_project_id: str | None = None,
        stream_position: int | None = None,
        revision_id: str | None = None,
    ) -> str | None:
        started = time.perf_counter()
        try:
            hashed_task = task_id_hash(self.task_id)
            record_id = (
                f"subsection-handover:{hashed_task}:"
                f"S{section}.{subsection}:{output_sha256}"
            )
            source_id = (
                f"writer-handover:{hashed_task}:"
                f"S{section}.{subsection}:{output_sha256}"
            )
            fields = ()
            if isinstance(handover_note, dict):
                fields = tuple(
                    HandoverFieldArtifact(
                        field_name=name,
                        value=handover_note[name],
                        value_hash=sha256_json(handover_note[name]),
                        item_count=value_item_count(handover_note[name]),
                        source_id=f"{source_id}:{name}",
                        source_hash=output_sha256,
                    )
                    for name in HANDOVER_FIELD_NAMES
                    if name in handover_note
                )
            record = SubsectionHandoverRecord(
                record_id=record_id,
                canonical_tenant_id=canonical_tenant_id,
                canonical_project_id=canonical_project_id,
                stream_position=stream_position,
                revision_id=revision_id,
                task_id_hash=hashed_task,
                section=section,
                subsection=subsection,
                output_sha256=output_sha256,
                prompt_messages_hash=prompt_messages_hash,
                commit_idempotency_key=commit_idempotency_key,
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
                created_at=utc_now(),
            )
            if canonical_tenant_id is not None and canonical_project_id is not None:
                result = self.blackboard.hash_upsert_by_position(
                    self._canonical_namespace(
                        canonical_tenant_id, canonical_project_id
                    ),
                    record_id,
                    record.model_dump(mode="json"),
                    stream_position,
                )
                if result == "conflict":
                    raise ValueError(
                        "handover semantic conflict at the same stream_position"
                    )
                return record_id

            envelope = self._load()
            existing = envelope.records.get(record_id)
            if existing is not None:
                return record_id
            records = dict(envelope.records)
            records[record_id] = record
            self._save(envelope.model_copy(update={"records": records}))
            return record_id
        except ValueError:
            if canonical_tenant_id is not None:
                raise
            return None
        except Exception as error:
            self._record_error(
                section=section,
                subsection=subsection,
                phase="capture_committed",
                error=error,
                output_sha256=output_sha256,
                elapsed_ms=(time.perf_counter() - started) * 1000,
            )
            return None

    def list_canonical_records(
        self, *, tenant_id: str, project_id: str
    ) -> tuple[SubsectionHandoverRecord, ...]:
        scoped = tuple(
            SubsectionHandoverRecord.model_validate(record)
            for record in self.blackboard.hash_get_all(
                self._canonical_namespace(tenant_id, project_id)
            ).values()
        )
        for record in scoped:
            self._validate_canonical_record(record, tenant_id, project_id)
        legacy_envelope = tuple(
            record
            for record in self._load().records.values()
            if self._record_kind(record) == "canonical"
            and record.canonical_tenant_id == tenant_id
            and record.canonical_project_id == project_id
        )
        records = (*scoped, *legacy_envelope)
        return tuple(sorted(records, key=lambda item: (item.stream_position or 0, item.record_id)))

    def clear_canonical_records(self, *, tenant_id: str, project_id: str) -> int:
        namespace = self._canonical_namespace(tenant_id, project_id)
        scoped = self.blackboard.hash_get_all(namespace)
        for payload in scoped.values():
            self._validate_canonical_record(
                SubsectionHandoverRecord.model_validate(payload), tenant_id, project_id
            )
        record_ids = tuple(scoped)
        removed = self.blackboard.hash_delete(namespace, *record_ids)
        envelope = self._load()
        retained = {
            storage_id: record
            for storage_id, record in envelope.records.items()
            if not (
                self._record_kind(record) == "canonical"
                and record.canonical_tenant_id == tenant_id
                and record.canonical_project_id == project_id
            )
        }
        legacy_removed = len(envelope.records) - len(retained)
        if legacy_removed:
            self._save(envelope.model_copy(update={"records": retained}))
        return removed + legacy_removed

    @staticmethod
    def _canonical_marker_values(
        record: SubsectionHandoverRecord,
    ) -> tuple[object, ...]:
        return (
            record.canonical_tenant_id,
            record.canonical_project_id,
            record.stream_position,
            record.revision_id,
        )

    @classmethod
    def _record_kind(cls, record: SubsectionHandoverRecord) -> str:
        values = cls._canonical_marker_values(record)
        if all(value is None for value in values):
            return "legacy"
        if all(value is not None for value in values):
            return "canonical"
        return "malformed"

    @classmethod
    def _validate_canonical_record(
        cls,
        record: SubsectionHandoverRecord,
        tenant_id: str,
        project_id: str,
    ) -> None:
        if cls._record_kind(record) != "canonical":
            raise ValueError("malformed Handover canonical identity markers")
        if (
            record.canonical_tenant_id != tenant_id
            or record.canonical_project_id != project_id
        ):
            raise ValueError("Handover canonical identity scope mismatch")

    def malformed_projection_records(self) -> tuple[SubsectionHandoverRecord, ...]:
        return tuple(
            record
            for record in self._load().records.values()
            if self._record_kind(record) == "malformed"
        )

    def unscoped_records(self) -> tuple[SubsectionHandoverRecord, ...]:
        return tuple(
            record
            for record in self._load().records.values()
            if self._record_kind(record) == "legacy"
        )

    def clear_unscoped_records(self) -> int:
        envelope = self._load()
        records = {
            storage_id: record
            for storage_id, record in envelope.records.items()
            if self._record_kind(record) != "legacy"
        }
        removed = len(envelope.records) - len(records)
        if removed:
            self._save(envelope.model_copy(update={"records": records}))
        return removed

    def capture_canonical_projection(self, envelope: Any) -> str | None:
        """Persist a handover only from an accepted Canonical revision."""

        observation_payload = dict(
            envelope.generation_metadata.get("handover_observation") or {}
        )
        observation = (
            HandoverExtractionObservation.model_validate(observation_payload)
            if observation_payload
            else HandoverExtractionObservation(
                executed=bool(envelope.handover_candidate),
                execution_status=(
                    "success" if envelope.handover_candidate else "skipped"
                ),
                skip_reason=(None if envelope.handover_candidate else "missing"),
            )
        )
        return self.capture_committed(
            section=envelope.section,
            subsection=envelope.subsection,
            output_sha256=envelope.content_hash,
            prompt_messages_hash=envelope.prompt_hash,
            commit_idempotency_key=envelope.commit_id,
            handover_note=envelope.handover_candidate,
            observation=observation,
        )
