"""Portable, deterministic Canonical Schema v0 project snapshots."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, inspect as sa_inspect, select, update
from sqlalchemy.orm import Session

from .hashing import sha256_json
from .models import (
    CanonicalCommit,
    CanonicalDocument,
    CanonicalProject,
    CanonicalStateVersion,
    CanonicalSubsection,
    DocumentRevision,
    EventLedger,
    IdempotencyRecord,
    OutboxEvent,
)
from .repositories import CanonicalRepository


SNAPSHOT_MODELS = (
    CanonicalProject,
    CanonicalDocument,
    CanonicalSubsection,
    CanonicalStateVersion,
    CanonicalCommit,
    DocumentRevision,
    EventLedger,
    IdempotencyRecord,
    OutboxEvent,
)


def canonical_snapshot_bytes(snapshot: dict[str, Any]) -> bytes:
    return (
        json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _serialize_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _serialize_model(instance) -> dict[str, Any]:
    return {
        column.key: _serialize_value(getattr(instance, column.key))
        for column in sa_inspect(instance.__class__).columns
    }


def _scoped_rows(session: Session, model, tenant_id: str, project_id: str):
    if model is CanonicalProject:
        statement = select(model).where(
            model.id == project_id, model.tenant_id == tenant_id
        )
    else:
        statement = select(model).where(
            model.project_id == project_id, model.tenant_id == tenant_id
        )
    return session.scalars(statement.order_by(model.id)).all()


def export_project_snapshot(
    session: Session, *, tenant_id: str, project_id: str
) -> dict[str, Any]:
    repo = CanonicalRepository(session, tenant_id, project_id)
    project = repo.get_project()
    if project is None or not project.current_state_version_id:
        raise ValueError("cannot snapshot a missing or headless project")
    tables = {
        model.__tablename__: [
            _serialize_model(row)
            for row in _scoped_rows(session, model, tenant_id, project_id)
        ]
        for model in SNAPSHOT_MODELS
    }
    document_hashes = {
        document.id: repo.materialize_document_hash(document.id)
        for document in session.scalars(
            select(CanonicalDocument)
            .where(
                CanonicalDocument.tenant_id == tenant_id,
                CanonicalDocument.project_id == project_id,
            )
            .order_by(CanonicalDocument.id)
        ).all()
    }
    state = repo.get_current_state()
    ledger_payloads = repo.list_ledger_payloads()
    return {
        "schema_version": "canonical-project-snapshot-v0",
        "scope": {"tenant_id": tenant_id, "project_id": project_id},
        "tables": tables,
        "integrity": {
            "document_hashes": document_hashes,
            "project_state_head": {
                "id": state.id,
                "state_hash": state.state_hash,
            },
            "ledger_hash": sha256_json(ledger_payloads),
        },
    }


def _deserialize_row(model, row: dict[str, Any], **overrides):
    values = dict(row)
    values.update(overrides)
    for column in sa_inspect(model).columns:
        if isinstance(column.type, DateTime) and values.get(column.key):
            values[column.key] = datetime.fromisoformat(values[column.key])
    return model(**values)


def import_project_snapshot(session: Session, snapshot: dict[str, Any]) -> None:
    if snapshot.get("schema_version") != "canonical-project-snapshot-v0":
        raise ValueError("unsupported canonical snapshot version")
    scope = snapshot["scope"]
    tenant_id = scope["tenant_id"]
    project_id = scope["project_id"]
    repo = CanonicalRepository(session, tenant_id, project_id)
    if repo.get_project() is not None:
        raise ValueError("snapshot target project already exists")
    tables = snapshot["tables"]

    project_row = tables[CanonicalProject.__tablename__][0]
    project_head = project_row["current_state_version_id"]
    project_updated_at = project_row["updated_at"]
    session.add(
        _deserialize_row(
            CanonicalProject, project_row, current_state_version_id=None
        )
    )
    session.flush()

    for model in (CanonicalDocument,):
        session.add_all(
            _deserialize_row(model, row) for row in tables[model.__tablename__]
        )
        session.flush()

    subsection_heads = {
        row["id"]: (row["current_revision_id"], row["updated_at"])
        for row in tables[CanonicalSubsection.__tablename__]
    }
    session.add_all(
        _deserialize_row(CanonicalSubsection, row, current_revision_id=None)
        for row in tables[CanonicalSubsection.__tablename__]
    )
    session.flush()

    state_rows = tables[CanonicalStateVersion.__tablename__]
    for row in state_rows:
        if row["origin"] == "genesis":
            session.add(_deserialize_row(CanonicalStateVersion, row))
    session.flush()

    pending_commits = list(tables[CanonicalCommit.__tablename__])
    pending_states = [row for row in state_rows if row["origin"] != "genesis"]
    while pending_commits or pending_states:
        progressed = False
        known_states = set(session.scalars(select(CanonicalStateVersion.id)).all())
        for row in list(pending_commits):
            if row["base_state_version_id"] in known_states:
                session.add(_deserialize_row(CanonicalCommit, row))
                session.flush()
                pending_commits.remove(row)
                progressed = True
        known_commits = set(session.scalars(select(CanonicalCommit.id)).all())
        known_states = set(session.scalars(select(CanonicalStateVersion.id)).all())
        for row in list(pending_states):
            if row["commit_id"] in known_commits and row["parent_state_version_id"] in known_states:
                session.add(_deserialize_row(CanonicalStateVersion, row))
                session.flush()
                pending_states.remove(row)
                progressed = True
        if not progressed:
            raise ValueError("snapshot contains an unresolved commit/state dependency cycle")

    for model in (DocumentRevision, EventLedger, IdempotencyRecord, OutboxEvent):
        session.add_all(
            _deserialize_row(model, row) for row in tables[model.__tablename__]
        )
        session.flush()

    project_dt = datetime.fromisoformat(project_updated_at)
    session.execute(
        update(CanonicalProject)
        .where(
            CanonicalProject.id == project_id,
            CanonicalProject.tenant_id == tenant_id,
        )
        .values(current_state_version_id=project_head, updated_at=project_dt)
    )
    for subsection_id, (head, updated_at) in subsection_heads.items():
        session.execute(
            update(CanonicalSubsection)
            .where(
                CanonicalSubsection.id == subsection_id,
                CanonicalSubsection.tenant_id == tenant_id,
                CanonicalSubsection.project_id == project_id,
            )
            .values(
                current_revision_id=head,
                updated_at=datetime.fromisoformat(updated_at),
            )
        )
    session.flush()
    session.expire_all()

    restored = export_project_snapshot(
        session, tenant_id=tenant_id, project_id=project_id
    )
    if restored["integrity"] != snapshot["integrity"]:
        raise ValueError("restored snapshot integrity does not match source")
