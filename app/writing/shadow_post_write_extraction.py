"""Failure-isolated post-commit shadow runner for shared typed extraction."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any, Protocol

from .contracts import PostWriteStateBundle
from .post_write_extraction import EXTRACTOR_VERSION, SharedPostWriteExtractor


logger = logging.getLogger("writing_system.shadow_post_write_extraction")
BLACKBOARD_KEY = "post_write_extraction_shadow"


class PostWriteExtractionSink(Protocol):
    def write(self, record: dict[str, Any], bundle: PostWriteStateBundle | None) -> None: ...


class NoOpPostWriteExtractionSink:
    def write(self, record: dict[str, Any], bundle: PostWriteStateBundle | None) -> None:
        return None


class InMemoryPostWriteExtractionSink:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []
        self.bundles: list[PostWriteStateBundle] = []

    def write(self, record: dict[str, Any], bundle: PostWriteStateBundle | None) -> None:
        self.records.append(record)
        if bundle is not None:
            self.bundles.append(bundle)


class BlackboardPostWriteExtractionSink:
    """Keep private bundle values in task-scoped Redis, outside public reports."""

    def __init__(self, blackboard, task_id: str) -> None:
        self.blackboard = blackboard
        self.task_id = task_id

    def write(self, record: dict[str, Any], bundle: PostWriteStateBundle | None) -> None:
        current = self.blackboard.get(self.task_id, BLACKBOARD_KEY) or []
        if isinstance(current, str):
            try:
                current = json.loads(current)
            except (TypeError, json.JSONDecodeError):
                current = []
        if not isinstance(current, list):
            current = []
        current.append({
            "record": record,
            "bundle": bundle.model_dump(mode="json") if bundle is not None else None,
        })
        self.blackboard.set(self.task_id, BLACKBOARD_KEY, current)


class ShadowPostWriteExtractionRunner:
    """Run only after commit; never update authoritative state or retry Writer."""

    def __init__(
        self,
        *,
        enabled: bool = False,
        extractor: SharedPostWriteExtractor | None = None,
        sink: PostWriteExtractionSink | None = None,
    ) -> None:
        self.enabled = enabled
        self.extractor = extractor
        self.sink = sink or NoOpPostWriteExtractionSink()
        self._observed: set[tuple[str, int, int, str]] = set()

    def observe_committed(
        self,
        *,
        task_id: str,
        section: int,
        subsection: int,
        text: str,
        output_hash: str,
        source_manifest: list[dict[str, Any]] | None = None,
        known_context: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if not self.enabled:
            return None
        key = (task_id, section, subsection, output_hash)
        if key in self._observed:
            return None
        self._observed.add(key)
        started = time.perf_counter()
        bundle: PostWriteStateBundle | None = None
        status = "completed"
        error_type = None
        try:
            if self.extractor is None:
                status = "skipped"
                error_type = "extractor_unavailable"
            else:
                bundle = self.extractor.extract(
                    task_id=task_id,
                    section=section,
                    subsection=subsection,
                    text=text,
                    output_hash=output_hash,
                    source_manifest=source_manifest,
                    known_context=known_context,
                )
        except Exception as exc:
            status = "shadow_error"
            error_type = type(exc).__name__

        changes = bundle.changes if bundle else []
        category_counts: dict[str, int] = {}
        for item in changes:
            category_counts[item.category] = category_counts.get(item.category, 0) + 1
        evidence_count = sum(len(item.evidence) for item in changes)
        record = {
            "task_id_hash": hashlib.sha256(task_id.encode("utf-8")).hexdigest(),
            "section": section,
            "subsection": subsection,
            "output_sha256": output_hash,
            "extractor_version": EXTRACTOR_VERSION,
            "bundle_hash": bundle.bundle_hash if bundle else None,
            "status": status,
            "change_count": len(changes),
            "category_counts": category_counts,
            "unknown_count": sum(item.status == "unknown" for item in changes),
            "conflicted_count": sum(item.status == "conflicted" for item in changes),
            "evidence_span_count": evidence_count,
            "evidence_trace_rate": 1.0 if changes and evidence_count == len(changes) else (1.0 if not changes else 0.0),
            "warning_count": len(bundle.extraction_warnings) if bundle else 0,
            "source_ids": [item.get("source_id", "") for item in (bundle.source_manifest if bundle else [])],
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
            "error_type": error_type,
            "production_effect": False,
        }
        try:
            self.sink.write(record, bundle)
            logger.info(
                "post_write_extraction_shadow=%s",
                json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            )
        except Exception:
            logger.warning("Post-write extraction shadow sink failed", exc_info=True)
        return record
