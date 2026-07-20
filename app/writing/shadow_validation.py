"""Failure-isolated post-commit shadow execution for BoundaryValidator."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Callable
from typing import Any, Protocol

from .boundary_validator import VALIDATOR_VERSION, BoundaryValidator, ValidationContract
from .contracts import SceneSpec


logger = logging.getLogger("writing_system.shadow_boundary_validation")


class ShadowValidationSink(Protocol):
    def write(self, record: dict[str, Any]) -> None: ...


class NoOpShadowValidationSink:
    def write(self, record: dict[str, Any]) -> None:
        return None


class InMemoryShadowValidationSink:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    def write(self, record: dict[str, Any]) -> None:
        self.records.append(record)


SceneSpecProvider = Callable[[str, int, int], SceneSpec | None]


class ShadowBoundaryValidationRunner:
    """Observe committed text without changing or retrying production work."""

    def __init__(
        self,
        *,
        enabled: bool = False,
        sink: ShadowValidationSink | None = None,
        scene_spec_provider: SceneSpecProvider | None = None,
        validator: BoundaryValidator | None = None,
    ) -> None:
        self.enabled = enabled
        self.sink = sink or NoOpShadowValidationSink()
        self.scene_spec_provider = scene_spec_provider
        self.validator = validator or BoundaryValidator()
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
    ) -> dict[str, Any] | None:
        if not self.enabled:
            return None
        key = (task_id, section, subsection, output_hash)
        if key in self._observed:
            return None
        self._observed.add(key)
        started = time.perf_counter()
        base = {
            "task_id": task_id,
            "section": section,
            "subsection": subsection,
            "output_sha256": output_hash,
            "validator_version": VALIDATOR_VERSION,
            "contract_hash": "",
            "required_event_results": [],
            "boundary_violations": [],
            "unsupported_fact_warnings": [],
            "source_manifest": _sanitize_manifest(source_manifest or []),
            "skip_reason": None,
            "error_type": None,
            "production_effect": False,
        }
        try:
            if hashlib.sha256(text.encode("utf-8")).hexdigest() != output_hash:
                raise ValueError("output_hash_mismatch")
            if self.scene_spec_provider is None:
                record = {**base, "validation_status": "skipped", "skip_reason": "scene_spec_provider_unavailable"}
            else:
                spec = self.scene_spec_provider(task_id, section, subsection)
                if spec is None:
                    record = {**base, "validation_status": "skipped", "skip_reason": "scene_spec_unavailable"}
                else:
                    contract = ValidationContract.from_scene_spec(spec)
                    base["contract_hash"] = contract.contract_hash
                    if not contract.executable:
                        record = {**base, "validation_status": "skipped", "skip_reason": "no_executable_deterministic_rules"}
                    else:
                        result = self.validator.validate(
                            contract,
                            f"{task_id}:{section}:{subsection}",
                            text,
                            output_hash,
                        )
                        record = {
                            **base,
                            "contract_hash": contract.contract_hash,
                            "validation_status": result["validation_status"],
                            "required_event_results": result["required_event_results"],
                            "boundary_violations": result["boundary_violations"],
                            "unsupported_fact_warnings": result["unsupported_fact_warnings"],
                        }
        except Exception as exc:
            record = {
                **base,
                "validation_status": "shadow_error",
                "error_type": type(exc).__name__,
                "error_rule_version": VALIDATOR_VERSION,
            }
        record["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
        record = _sanitize_record(record)
        try:
            self.sink.write(record)
            logger.info("boundary_validator_shadow=%s", json.dumps(record, ensure_ascii=False, separators=(",", ":")))
        except Exception:
            logger.warning("BoundaryValidator shadow sink failed", exc_info=True)
        return record


def _sanitize_manifest(items: list[dict[str, Any]]) -> list[dict[str, str]]:
    result = []
    for item in items:
        source_id = str(item.get("source_id", ""))
        text_hash = str(item.get("text_hash", ""))
        if source_id or text_hash:
            result.append({"source_id": source_id, "text_hash": text_hash})
    return result


def _sanitize_record(record: dict[str, Any]) -> dict[str, Any]:
    for group in ("required_event_results", "boundary_violations", "unsupported_fact_warnings"):
        for item in record.get(group, []):
            for span in item.get("evidence_spans", []):
                span["excerpt"] = str(span.get("excerpt", ""))[:140]
    return record
