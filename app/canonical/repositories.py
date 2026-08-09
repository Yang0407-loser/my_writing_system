"""Tenant/project-scoped repositories for Canonical Schema v0."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .errors import ScopeRequired
from .hashing import sha256_json, sha256_text
from .models import (
    CanonicalCommit,
    CanonicalDocument,
    CanonicalProject,
    CanonicalStateVersion,
    CanonicalSubsection,
    DocumentRevision,
    EventLedger,
)


class CanonicalRepository:
    """A repository whose every read is bound to tenant and project scope."""

    def __init__(self, session: Session, tenant_id: str, project_id: str):
        if not tenant_id or not project_id:
            raise ScopeRequired("tenant_id and project_id are required")
        self.session = session
        self.tenant_id = tenant_id
        self.project_id = project_id

    def get_project(self) -> CanonicalProject | None:
        return self.session.scalar(
            select(CanonicalProject).where(
                CanonicalProject.id == self.project_id,
                CanonicalProject.tenant_id == self.tenant_id,
            )
        )

    def create_project(
        self,
        *,
        owner_id: str,
        name: str,
        genesis_state_json: dict[str, Any],
        genesis_state_version_id: str,
    ) -> CanonicalProject:
        if self.get_project() is not None:
            raise ValueError(f"project already exists: {self.project_id}")
        project = CanonicalProject(
            id=self.project_id,
            tenant_id=self.tenant_id,
            owner_id=owner_id,
            name=name,
            current_state_version_id=None,
        )
        self.session.add(project)
        self.session.flush()
        genesis = CanonicalStateVersion(
            id=genesis_state_version_id,
            tenant_id=self.tenant_id,
            project_id=self.project_id,
            commit_id=None,
            origin="genesis",
            parent_state_version_id=None,
            transition_version="genesis-v0",
            schema_version="canonical-state-v0",
            state_json=genesis_state_json,
            state_hash=sha256_json(genesis_state_json),
        )
        self.session.add(genesis)
        self.session.flush()
        project.current_state_version_id = genesis.id
        self.session.flush()
        return project

    def get_current_state(self) -> CanonicalStateVersion | None:
        project = self.get_project()
        if project is None or not project.current_state_version_id:
            return None
        return self.session.scalar(
            select(CanonicalStateVersion).where(
                CanonicalStateVersion.id == project.current_state_version_id,
                CanonicalStateVersion.project_id == self.project_id,
                CanonicalStateVersion.tenant_id == self.tenant_id,
            )
        )

    def create_document(self, document_id: str, title: str) -> CanonicalDocument:
        project = self.get_project()
        if project is None or not project.current_state_version_id:
            raise ValueError("cannot create a document for a headless project")
        document = CanonicalDocument(
            id=document_id,
            tenant_id=self.tenant_id,
            project_id=self.project_id,
            title=title,
        )
        self.session.add(document)
        self.session.flush()
        return document

    def get_document(self, document_id: str) -> CanonicalDocument | None:
        return self.session.scalar(
            select(CanonicalDocument).where(
                CanonicalDocument.id == document_id,
                CanonicalDocument.tenant_id == self.tenant_id,
                CanonicalDocument.project_id == self.project_id,
            )
        )

    def create_subsection(
        self,
        subsection_id: str,
        document_id: str,
        ordinal: int,
        legacy_section: int | None = None,
        legacy_subsection: int | None = None,
    ) -> CanonicalSubsection:
        if self.get_document(document_id) is None:
            raise ValueError(f"document is outside scope or missing: {document_id}")
        subsection = CanonicalSubsection(
            id=subsection_id,
            tenant_id=self.tenant_id,
            project_id=self.project_id,
            document_id=document_id,
            ordinal=ordinal,
            legacy_section=legacy_section,
            legacy_subsection=legacy_subsection,
            current_revision_id=None,
        )
        self.session.add(subsection)
        self.session.flush()
        return subsection

    def get_subsection(self, subsection_id: str) -> CanonicalSubsection | None:
        return self.session.scalar(
            select(CanonicalSubsection).where(
                CanonicalSubsection.id == subsection_id,
                CanonicalSubsection.tenant_id == self.tenant_id,
                CanonicalSubsection.project_id == self.project_id,
            )
        )

    def create_commit_envelope(
        self,
        commit_id: str,
        candidate_hash: str,
        base_revision_number: int,
        base_state_version_id: str,
    ) -> CanonicalCommit:
        commit = CanonicalCommit(
            id=commit_id,
            tenant_id=self.tenant_id,
            project_id=self.project_id,
            candidate_hash=candidate_hash,
            base_revision_number=base_revision_number,
            base_state_version_id=base_state_version_id,
            status="committed",
        )
        self.session.add(commit)
        self.session.flush()
        return commit

    def append_revision(
        self,
        revision_id: str,
        commit_id: str,
        subsection_id: str,
        content: str,
        creator: str,
        metadata: dict[str, Any] | None = None,
    ) -> DocumentRevision:
        subsection = self.get_subsection(subsection_id)
        if subsection is None:
            raise ValueError(f"subsection is outside scope or missing: {subsection_id}")
        parent = self.get_current_revision(subsection_id)
        revision = DocumentRevision(
            id=revision_id,
            tenant_id=self.tenant_id,
            project_id=self.project_id,
            commit_id=commit_id,
            subsection_id=subsection_id,
            revision_number=1 if parent is None else parent.revision_number + 1,
            parent_revision_id=None if parent is None else parent.id,
            content=content,
            content_hash=sha256_text(content),
            status="accepted",
            creator=creator,
            metadata_json=metadata or {},
        )
        self.session.add(revision)
        self.session.flush()
        subsection.current_revision_id = revision.id
        self.session.flush()
        return revision

    def get_current_revision(self, subsection_id: str) -> DocumentRevision | None:
        subsection = self.get_subsection(subsection_id)
        if subsection is None or not subsection.current_revision_id:
            return None
        return self.session.scalar(
            select(DocumentRevision).where(
                DocumentRevision.id == subsection.current_revision_id,
                DocumentRevision.subsection_id == subsection_id,
                DocumentRevision.tenant_id == self.tenant_id,
                DocumentRevision.project_id == self.project_id,
            )
        )

    def materialize_document(self, document_id: str) -> str:
        if self.get_document(document_id) is None:
            raise ValueError(f"document is outside scope or missing: {document_id}")
        rows = self.session.execute(
            select(CanonicalSubsection, DocumentRevision)
            .join(
                DocumentRevision,
                DocumentRevision.id == CanonicalSubsection.current_revision_id,
            )
            .where(
                CanonicalSubsection.tenant_id == self.tenant_id,
                CanonicalSubsection.project_id == self.project_id,
                CanonicalSubsection.document_id == document_id,
            )
            .order_by(CanonicalSubsection.ordinal)
        ).all()
        return "\n\n".join(revision.content for _, revision in rows)

    def materialize_document_hash(self, document_id: str) -> str:
        return sha256_text(self.materialize_document(document_id))

    def append_ledger_event(
        self,
        *,
        ledger_id: str,
        commit_id: str,
        ordinal: int,
        event_type: str,
        payload: dict[str, Any],
        evidence_refs: list[Any],
    ) -> EventLedger:
        event = EventLedger(
            id=ledger_id,
            tenant_id=self.tenant_id,
            project_id=self.project_id,
            commit_id=commit_id,
            event_type=event_type,
            payload_json=payload,
            evidence_refs_json=evidence_refs,
            ordinal=ordinal,
        )
        self.session.add(event)
        self.session.flush()
        return event

    def list_ledger_payloads(self) -> list[dict[str, Any]]:
        events = self.session.scalars(
            select(EventLedger)
            .where(
                EventLedger.tenant_id == self.tenant_id,
                EventLedger.project_id == self.project_id,
            )
            .order_by(EventLedger.commit_id, EventLedger.ordinal, EventLedger.id)
        ).all()
        return [
            {
                "id": event.id,
                "commit_id": event.commit_id,
                "event_type": event.event_type,
                "payload": event.payload_json,
                "evidence_refs": event.evidence_refs_json,
                "ordinal": event.ordinal,
            }
            for event in events
        ]
