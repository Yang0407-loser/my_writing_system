"""Rebuildable adapter for deterministic legacy World facts."""

from __future__ import annotations

from collections.abc import Iterable

from ..canonical.hashing import sha256_text
from ..canonical.projection_ports import ProjectionMessage, ProjectionRecord, ProjectionScope
from ..canonical.projection_registry import DEFAULT_PROJECTOR_REGISTRY
from ..world_state import WorldStateManager
from .base import ProjectionAdapterBase, normalized_records
from .legacy_scope import LegacyScopeBindingStore


def _normalized_fact(value: object) -> str:
    return " ".join(value.split()) if isinstance(value, str) else ""


class LegacyWorldProjectionAdapter(ProjectionAdapterBase):
    spec = DEFAULT_PROJECTOR_REGISTRY.get("legacy_world_event")

    def __init__(self, blackboard, scope: ProjectionScope, task_id: str) -> None:
        super().__init__(scope, task_id)
        self.manager = WorldStateManager(blackboard, task_id)
        self.bindings = LegacyScopeBindingStore(blackboard)

    def _reject_unscoped(self) -> None:
        self.manager.list_projected_facts(
            tenant_id=self.scope.tenant_id, project_id=self.scope.project_id
        )
        if self.manager.list_malformed_projection_facts():
            raise ValueError("malformed World canonical identity markers")
        if self.manager.list_unscoped_facts():
            self.bindings.require(task_id=self.task_id, scope=self.scope)
            raise ValueError(
                "legacy World ownership is approved but migration clear is required"
            )

    def _records_for(self, message: ProjectionMessage) -> tuple[ProjectionRecord, ...]:
        revision = self._validate_message(message)
        metadata = revision["metadata"]
        handover = metadata.get("handover_candidate")
        facts = handover.get("new_facts", ()) if isinstance(handover, dict) else ()
        if not isinstance(facts, (list, tuple)):
            facts = ()
        records = []
        for ordinal, raw in enumerate(facts, start=1):
            text = _normalized_fact(raw)
            if not text:
                continue
            fact_id = "world-fact-" + sha256_text(
                f"{message.projection_event_id}:{ordinal}:{text}"
            )
            records.append(
                ProjectionRecord(
                    record_id=fact_id,
                    stream_position=message.stream_position,
                    commit_id=message.commit_id,
                    revision_id=message.revision_id,
                    payload={
                        "category": "subplot_derived",
                        "fact": text,
                        "source_section": int(metadata.get("section") or 0),
                        "source_subsection": int(metadata.get("subsection") or 0),
                        "immutable": True,
                        "verified": False,
                        "projection_event_id": message.projection_event_id,
                    },
                )
            )
        return normalized_records(records)

    def apply(self, message: ProjectionMessage):
        self._reject_unscoped()
        records = self._records_for(message)
        for record in records:
            payload = record.payload
            self.manager.upsert_fact(
                fact_id=record.record_id,
                category=payload["category"],
                fact=payload["fact"],
                source_section=payload["source_section"],
                source_subsection=payload["source_subsection"],
                immutable=payload["immutable"],
                verified=payload["verified"],
                canonical_tenant_id=message.tenant_id,
                canonical_project_id=message.project_id,
                stream_position=record.stream_position,
                commit_id=record.commit_id,
                revision_id=record.revision_id,
                projection_event_id=message.projection_event_id,
            )
        return self._receipt(message, records)

    def expected_records(
        self, messages: Iterable[ProjectionMessage]
    ) -> tuple[ProjectionRecord, ...]:
        return normalized_records(
            record for message in messages for record in self._records_for(message)
        )

    def actual_records(self, scope: ProjectionScope) -> tuple[ProjectionRecord, ...]:
        self._validate_actual_scope(scope)
        self._reject_unscoped()
        return normalized_records(
            ProjectionRecord(
                record_id=item["fact_id"],
                stream_position=item["stream_position"],
                commit_id=item["commit_id"],
                revision_id=item["revision_id"],
                payload={
                    key: item[key]
                    for key in (
                        "category",
                        "fact",
                        "source_section",
                        "source_subsection",
                        "immutable",
                        "verified",
                        "projection_event_id",
                    )
                },
            )
            for item in self.manager.list_projected_facts(
                tenant_id=scope.tenant_id, project_id=scope.project_id
            )
        )

    def clear(self, scope: ProjectionScope) -> None:
        self._validate_actual_scope(scope)
        self.manager.list_projected_facts(
            tenant_id=scope.tenant_id, project_id=scope.project_id
        )
        if self.manager.list_malformed_projection_facts():
            raise ValueError("malformed World canonical identity markers")
        if self.manager.list_unscoped_facts():
            self.bindings.require(task_id=self.task_id, scope=scope)
            self.manager.clear_unscoped_facts()
        self.manager.clear_projected_facts(
            tenant_id=scope.tenant_id, project_id=scope.project_id
        )
