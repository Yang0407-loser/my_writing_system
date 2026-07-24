"""Fail-open capture and recovery for subsection StateFrame history."""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .state_frame_builder import StateFrameBuilder
from .state_frame_history import (
    STATE_FRAME_HISTORY_KEY,
    StateFrameHistoryEnvelope,
    SubsectionStateFrameRecord,
    utc_now,
)
from .state_frame_quality import StateFrameQualityEvaluator
from .state_frame_service import build_state_frame_artifacts
from .state_frame_v1 import StateFrameSnapshot, task_id_hash


logger = logging.getLogger("writing_system.state_frame_persistence")


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
    return StateFrameHistoryEnvelope().model_dump(mode="json")


def normalize_history(value: Any) -> StateFrameHistoryEnvelope:
    payload = _mapping(value)
    if not payload:
        return StateFrameHistoryEnvelope()
    return StateFrameHistoryEnvelope.model_validate(payload)


def legacy_checkpoint_projection(checkpoint: Mapping[str, Any]) -> dict[str, Any]:
    """Projection used to prove the optional history field changes no legacy field."""
    return {
        key: value
        for key, value in dict(checkpoint).items()
        if key != STATE_FRAME_HISTORY_KEY
    }


def history_for_checkpoint(blackboard: Any, task_id: str) -> dict[str, Any] | None:
    try:
        raw = blackboard.get(task_id, STATE_FRAME_HISTORY_KEY)
        if not raw:
            checkpoint = blackboard.load_checkpoint(task_id) or {}
            raw = checkpoint.get(STATE_FRAME_HISTORY_KEY)
        history = normalize_history(raw)
    except Exception:
        return None
    if not history.records and not history.pending_before and not history.errors:
        return None
    return history.model_dump(mode="json")


def merge_history_into_analysis(
    analysis: Mapping[str, Any] | None, history: Any
) -> dict[str, Any]:
    merged = _mapping(analysis)
    envelope = normalize_history(history)
    if envelope.records or envelope.pending_before or envelope.errors:
        merged[STATE_FRAME_HISTORY_KEY] = envelope.model_dump(mode="json")
    return merged


def load_task_history_read_only(
    db_path: str, task_id: str
) -> StateFrameHistoryEnvelope | None:
    """Read analysis_json without initializing or migrating a SQLite database."""
    path = Path(db_path)
    if not path.exists():
        return None
    uri = f"{path.resolve().as_uri()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
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
    analysis = _mapping(row[0])
    payload = analysis.get(STATE_FRAME_HISTORY_KEY)
    if not payload:
        return None
    try:
        return normalize_history(payload)
    except Exception:
        return None


def select_record(
    envelope: StateFrameHistoryEnvelope, section: int, subsection: int
) -> SubsectionStateFrameRecord | None:
    candidates = [
        item
        for item in envelope.records.values()
        if (item.section, item.subsection) == (section, subsection)
    ]
    if not candidates:
        candidates = [
            item
            for item in envelope.pending_before.values()
            if (item.section, item.subsection) == (section, subsection)
        ]
    if not candidates:
        return None
    return sorted(
        candidates,
        key=lambda item: (
            item.finalized_at or "",
            item.output_sha256 or "",
            item.record_id,
        ),
    )[-1]


def record_to_api_payload(
    record: SubsectionStateFrameRecord, *, source: str
) -> dict[str, Any]:
    quality = None
    if record.after_frame is not None and record.delta is not None:
        quality = StateFrameQualityEvaluator().evaluate(
            record.before_frame, record.after_frame, record.delta
        ).model_dump(mode="json")
    return {
        "before": record.before_frame.model_dump(mode="json"),
        "after": (
            record.after_frame.model_dump(mode="json")
            if record.after_frame is not None
            else None
        ),
        "delta": (
            record.delta.model_dump(mode="json")
            if record.delta is not None
            else None
        ),
        "quality": quality,
        "record_id": record.record_id,
        "persistence_status": record.persistence_status,
        "source": source,
        "reconstructed": False,
        "production_effect": False,
        "writer_llm_calls": 0,
    }


class StateFrameHistoryRecorder:
    """Captures immutable boundaries while isolating every persistence failure."""

    def __init__(self, blackboard: Any, task_id: str):
        self.blackboard = blackboard
        self.task_id = task_id

    def _read_sources(self) -> tuple[dict, dict, list, list]:
        task_data = self.blackboard.get_all(self.task_id) or {}
        checkpoint = self.blackboard.load_checkpoint(self.task_id) or {}
        relations: list = []
        foreshadows: list = []
        try:
            from ..character_relation_store import list_relations_read_only

            relations = list_relations_read_only(self.task_id)
        except Exception:
            relations = []
        try:
            from ..foreshadowing_store import list_foreshadowings_read_only

            foreshadows = list_foreshadowings_read_only(self.task_id)
        except Exception:
            foreshadows = []
        return task_data, checkpoint, relations, foreshadows

    def _load(self) -> StateFrameHistoryEnvelope:
        raw = self.blackboard.get(self.task_id, STATE_FRAME_HISTORY_KEY)
        if not raw:
            checkpoint = self.blackboard.load_checkpoint(self.task_id) or {}
            raw = checkpoint.get(STATE_FRAME_HISTORY_KEY)
        return normalize_history(raw)

    def _save(self, envelope: StateFrameHistoryEnvelope) -> None:
        self.blackboard.set(
            self.task_id,
            STATE_FRAME_HISTORY_KEY,
            envelope.model_dump(mode="json"),
        )

    def _record_error(
        self,
        *,
        section: int,
        subsection: int,
        phase: str,
        error: Exception,
        elapsed_ms: float,
        output_sha256: str | None = None,
    ) -> None:
        try:
            envelope = self._load()
            errors = [
                *envelope.errors,
                {
                    "task_id_hash": task_id_hash(self.task_id),
                    "section": section,
                    "subsection": subsection,
                    "phase": phase,
                    "error_type": type(error).__name__,
                    "source_type": "state_frame_history",
                    "elapsed_ms": round(elapsed_ms, 3),
                    "output_sha256": output_sha256,
                    "production_effect": False,
                },
            ]
            self._save(envelope.model_copy(update={"errors": tuple(errors[-50:])}))
        except Exception:
            pass
        logger.warning(
            "state_frame_history_capture_failed phase=%s error_type=%s",
            phase,
            type(error).__name__,
        )

    @staticmethod
    def _granularity(frame: StateFrameSnapshot) -> dict[str, str]:
        result: dict[str, str] = {}
        for source in frame.source_manifest:
            if source.source_type == "post_write_state_bundle":
                value = "subsection_exact"
            elif source.source_type == "handover":
                value = "section_level_only"
            elif source.source_type in {
                "character_state_store",
                "character_relation_store",
                "foreshadowing_store",
            }:
                value = "current_store_snapshot"
            else:
                value = "subsection_exact"
            result[source.source_type] = value
        for source_type in frame.unavailable_source_types:
            result.setdefault(source_type, "unavailable")
        return result

    def capture_before(
        self,
        *,
        section: int,
        subsection: int,
        prompt_messages_hash: str = "",
        before_source_hash: str | None = None,
        checkpoint_version: str | None,
    ) -> str | None:
        started = time.perf_counter()
        try:
            task_data, checkpoint, relations, foreshadows = self._read_sources()
            artifacts = build_state_frame_artifacts(
                task_id=self.task_id,
                section=section,
                subsection=subsection,
                task_data=task_data,
                checkpoint=checkpoint,
                relations=relations,
                foreshadows=foreshadows,
            )
            before = StateFrameSnapshot.model_validate(artifacts["before"])
            boundary_hash = (
                prompt_messages_hash
                or before_source_hash
                or before.frame_hash
            )
            pending_id = (
                f"state-frame-before:{task_id_hash(self.task_id)}:"
                f"S{section}.{subsection}:{boundary_hash}"
            )
            envelope = self._load()
            if pending_id in envelope.pending_before:
                return pending_id
            now = utc_now()
            record = SubsectionStateFrameRecord(
                record_id=pending_id,
                task_id_hash=task_id_hash(self.task_id),
                section=section,
                subsection=subsection,
                before_frame=before,
                before_frame_hash=before.frame_hash,
                prompt_messages_hash=prompt_messages_hash,
                checkpoint_version=checkpoint_version,
                source_manifest=before.source_manifest,
                unavailable_source_types=before.unavailable_source_types,
                pending_source_types=before.pending_source_types,
                source_granularity=self._granularity(before),
                persistence_status="captured_before",
                created_at=now,
            )
            pending = dict(envelope.pending_before)
            pending[pending_id] = record
            self._save(envelope.model_copy(update={"pending_before": pending}))
            return pending_id
        except Exception as error:
            self._record_error(
                section=section,
                subsection=subsection,
                phase="capture_before",
                error=error,
                elapsed_ms=(time.perf_counter() - started) * 1000,
            )
            return None

    def bind_prompt_hash(
        self, before_record_id: str | None, prompt_messages_hash: str
    ) -> None:
        """Attach the real PromptBuilder hash without rebuilding the frozen Before."""
        if not before_record_id:
            return
        record = None
        try:
            envelope = self._load()
            record = envelope.pending_before.get(before_record_id)
            if record is None or record.prompt_messages_hash == prompt_messages_hash:
                return
            pending = dict(envelope.pending_before)
            pending[before_record_id] = record.model_copy(update={
                "prompt_messages_hash": prompt_messages_hash,
            })
            self._save(envelope.model_copy(update={"pending_before": pending}))
        except Exception as error:
            self._record_error(
                section=record.section if record is not None else 0,
                subsection=record.subsection if record is not None else 0,
                phase="bind_prompt_hash",
                error=error,
                elapsed_ms=0.0,
            )

    def capture_after(
        self,
        *,
        section: int,
        subsection: int,
        prompt_messages_hash: str,
        output_sha256: str,
        checkpoint_version: str | None,
        commit_idempotency_key: str,
        before_record_id: str | None = None,
    ) -> str | None:
        started = time.perf_counter()
        try:
            envelope = self._load()
            record_id = (
                f"state-frame-history:{task_id_hash(self.task_id)}:"
                f"S{section}.{subsection}:{output_sha256}"
            )
            if record_id in envelope.records:
                return record_id
            pending_id = before_record_id or (
                f"state-frame-before:{task_id_hash(self.task_id)}:"
                f"S{section}.{subsection}:{prompt_messages_hash}"
            )
            pending = envelope.pending_before.get(pending_id)
            if pending is None:
                raise ValueError("missing_before_frame")
            task_data, checkpoint, relations, foreshadows = self._read_sources()
            artifacts = build_state_frame_artifacts(
                task_id=self.task_id,
                section=section,
                subsection=subsection,
                task_data=task_data,
                checkpoint=checkpoint,
                relations=relations,
                foreshadows=foreshadows,
            )
            after = StateFrameSnapshot.model_validate(artifacts["after"])
            delta = StateFrameBuilder.delta(pending.before_frame, after)
            unavailable = tuple(sorted(set(
                pending.before_frame.unavailable_source_types
                + after.unavailable_source_types
            )))
            pending_sources = tuple(sorted(set(
                pending.before_frame.pending_source_types + after.pending_source_types
            )))
            status = (
                "finalized" if not unavailable and not pending_sources else "partial"
            )
            record = pending.model_copy(update={
                "record_id": record_id,
                "after_frame": after,
                "delta": delta,
                "after_frame_hash": after.frame_hash,
                "delta_id": delta.delta_id,
                "output_sha256": output_sha256,
                "checkpoint_version": checkpoint_version,
                "commit_idempotency_key": commit_idempotency_key,
                "source_manifest": tuple({
                    (item.source_type, item.source_id, item.source_hash): item
                    for item in (*pending.before_frame.source_manifest, *after.source_manifest)
                }.values()),
                "unavailable_source_types": unavailable,
                "pending_source_types": pending_sources,
                "source_granularity": {
                    **pending.source_granularity,
                    **self._granularity(after),
                },
                "persistence_status": status,
                "finalized_at": utc_now(),
            })
            records = dict(envelope.records)
            records[record_id] = record
            pending_records = dict(envelope.pending_before)
            pending_records.pop(pending_id, None)
            self._save(envelope.model_copy(update={
                "records": records,
                "pending_before": pending_records,
            }))
            return record_id
        except Exception as error:
            self._record_error(
                section=section,
                subsection=subsection,
                phase="capture_after",
                error=error,
                elapsed_ms=(time.perf_counter() - started) * 1000,
                output_sha256=output_sha256,
            )
            return None
