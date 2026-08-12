"""Operator-approved ownership for otherwise unscoped legacy task data."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from pydantic import Field, field_validator

from ..canonical.contracts import FrozenArtifact
from ..canonical.projection_ports import ProjectionScope


LEGACY_SCOPE_BINDING_KEY = "canonical_legacy_scope_binding_v1"


class LegacyScopeBinding(FrozenArtifact):
    schema_version: str = "canonical-legacy-scope-binding-v1"
    tenant_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    operator_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    approved_at: str = Field(min_length=1)

    @field_validator("tenant_id", "project_id", "task_id", "operator_id", "reason")
    @classmethod
    def _non_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("legacy binding fields must not be blank")
        return value

    @field_validator("approved_at")
    @classmethod
    def _utc_timestamp(cls, value: str) -> str:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
            raise ValueError("approved_at must be an aware UTC timestamp")
        return parsed.astimezone(timezone.utc).isoformat()


class LegacyScopeBindingStore:
    def __init__(self, blackboard) -> None:
        self.blackboard = blackboard

    def approve(
        self,
        *,
        task_id: str,
        tenant_id: str,
        project_id: str,
        operator_id: str,
        reason: str,
        approved_at: str | None,
    ) -> LegacyScopeBinding:
        raw = self.blackboard.get(task_id, LEGACY_SCOPE_BINDING_KEY)
        if raw:
            existing = self._validate(raw)
            requested = LegacyScopeBinding(
                tenant_id=tenant_id,
                project_id=project_id,
                task_id=task_id,
                operator_id=operator_id,
                reason=reason,
                approved_at=approved_at or existing.approved_at,
            )
            if existing != requested:
                raise ValueError("legacy task namespace is already bound; conflicting rebind")
            return existing
        proposed = LegacyScopeBinding(
            tenant_id=tenant_id,
            project_id=project_id,
            task_id=task_id,
            operator_id=operator_id,
            reason=reason,
            approved_at=approved_at or datetime.now(timezone.utc).isoformat(),
        )
        if self.blackboard.set_if_absent(
            task_id, LEGACY_SCOPE_BINDING_KEY, proposed.model_dump(mode="json")
        ):
            return proposed
        winner = self.get(task_id=task_id)
        if winner != proposed:
            raise ValueError("legacy task namespace is already bound; conflicting rebind")
        return winner

    def get(self, *, task_id: str) -> LegacyScopeBinding | None:
        raw = self.blackboard.get(task_id, LEGACY_SCOPE_BINDING_KEY)
        return self._validate(raw) if raw else None

    @staticmethod
    def _validate(raw) -> LegacyScopeBinding:
        if isinstance(raw, str):
            raw = json.loads(raw)
        return LegacyScopeBinding.model_validate(raw)

    def require(
        self, *, task_id: str, scope: ProjectionScope
    ) -> LegacyScopeBinding:
        binding = self.get(task_id=task_id)
        if binding is None:
            raise ValueError("legacy ownership binding is required")
        if (
            binding.task_id != task_id
            or binding.tenant_id != scope.tenant_id
            or binding.project_id != scope.project_id
        ):
            raise ValueError("legacy ownership binding does not match canonical scope")
        return binding
