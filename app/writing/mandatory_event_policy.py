"""Execution policy and redacted observability for mandatory-event checks."""

from __future__ import annotations

import hashlib
import logging
import re
import time
import uuid
from dataclasses import dataclass

from ..rule_checks import _extract_lock_keywords


logger = logging.getLogger("writing_system.mandatory_event")

_VALID_MODES = {"off", "warn", "retry"}
_WARNED_INVALID_MODES: set[str] = set()


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_uuid(value: str) -> str | None:
    try:
        parsed = uuid.UUID(value.strip())
    except (AttributeError, ValueError):
        return None
    canonical = str(parsed)
    return canonical if value.strip().lower() == canonical else None


@dataclass(frozen=True)
class MandatoryEventDetection:
    violations: tuple[str, ...]
    observation: dict


class MandatoryEventPolicy:
    """Resolve enforcement mode and run the frozen literal detector."""

    DETECTOR_VERSION = "mandatory-event-literal-v1"
    THRESHOLD = 0.5

    def __init__(self, mode: str = "warn", retry_task_ids: str = "") -> None:
        requested = str(mode or "warn").strip().lower()
        self.mode_requested = requested
        if requested not in _VALID_MODES:
            self.mode = "warn"
            if requested not in _WARNED_INVALID_MODES:
                logger.warning(
                    "WRITER_MANDATORY_EVENT_MODE=%s invalid; using warn",
                    requested,
                )
                _WARNED_INVALID_MODES.add(requested)
        else:
            self.mode = requested
        self.retry_task_ids = frozenset(
            canonical
            for raw in str(retry_task_ids or "").split(",")
            if (canonical := _canonical_uuid(raw)) is not None
        )

    def effective_mode(self, task_id: str) -> str:
        if self.mode != "retry":
            return self.mode
        canonical = _canonical_uuid(task_id)
        return "retry" if canonical in self.retry_task_ids else "warn"

    def detect(
        self,
        *,
        candidate: str,
        mandatory_events_text: str,
        task_id: str,
        section: int,
        subsection: int,
        actual_retry_count: int,
    ) -> MandatoryEventDetection:
        started = time.perf_counter()
        mode_effective = self.effective_mode(task_id)
        event_descs = re.findall(r"【必须】(.+)", mandatory_events_text or "")
        violations: list[str] = []
        event_hashes: list[str] = []
        selected_keyword_hashes: list[dict] = []
        hit_counts: list[dict] = []
        total_counts: list[dict] = []

        for event in event_descs:
            event_hash = _sha256(event)
            keywords = _extract_lock_keywords({"title": event, "description": event})
            hits = sum(keyword in candidate for keyword in keywords)
            violated = bool(keywords) and hits < len(keywords) * self.THRESHOLD
            if violated:
                violations.append(event)
                event_hashes.append(event_hash)
            selected_keyword_hashes.append({
                "event_hash": event_hash,
                "keyword_hashes": [_sha256(keyword) for keyword in keywords],
            })
            hit_counts.append({"event_hash": event_hash, "count": hits})
            total_counts.append({"event_hash": event_hash, "count": len(keywords)})

        observation = {
            "task_id_hash": _sha256(task_id or ""),
            "section": section,
            "subsection": subsection,
            "mode_requested": self.mode_requested,
            "mode_effective": mode_effective,
            "candidate_output_sha256": _sha256(candidate),
            "contract_hash": _sha256(mandatory_events_text or ""),
            "required_event_count": len(event_descs),
            "violated_event_count": len(violations),
            "violated_event_hashes": event_hashes,
            "selected_keyword_hashes": selected_keyword_hashes,
            "keyword_hit_count_by_event": hit_counts,
            "keyword_total_by_event": total_counts,
            "threshold": self.THRESHOLD,
            "would_have_retried": bool(violations),
            "actual_retry_count": actual_retry_count,
            "legacy_retry_behavior": mode_effective == "retry",
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
            "detector_version": self.DETECTOR_VERSION,
            "production_effect": mode_effective == "retry",
        }
        return MandatoryEventDetection(tuple(violations), observation)

    def error_observation(
        self,
        *,
        candidate: str,
        mandatory_events_text: str,
        task_id: str,
        section: int,
        subsection: int,
        actual_retry_count: int,
        error: Exception,
    ) -> dict:
        mode_effective = self.effective_mode(task_id)
        return {
            "task_id_hash": _sha256(task_id or ""),
            "section": section,
            "subsection": subsection,
            "mode_requested": self.mode_requested,
            "mode_effective": mode_effective,
            "candidate_output_sha256": _sha256(candidate),
            "contract_hash": _sha256(mandatory_events_text or ""),
            "required_event_count": None,
            "violated_event_count": None,
            "violated_event_hashes": [],
            "selected_keyword_hashes": [],
            "keyword_hit_count_by_event": [],
            "keyword_total_by_event": [],
            "threshold": self.THRESHOLD,
            "would_have_retried": None,
            "actual_retry_count": actual_retry_count,
            "legacy_retry_behavior": mode_effective == "retry",
            "elapsed_ms": None,
            "detector_version": self.DETECTOR_VERSION,
            "production_effect": mode_effective == "retry",
            "error_type": type(error).__name__,
        }
