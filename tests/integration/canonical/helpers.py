from __future__ import annotations

from sqlalchemy import func, select

from app.canonical.contracts import (
    CandidateValidation,
    CanonicalEventCandidate,
    CanonicalStateSnapshot,
    PreparedCanonicalCommit,
    SubsectionCandidate,
)
from app.canonical.database import build_engine, build_session_factory
from app.canonical.hashing import sha256_text
from app.canonical.models import (
    CanonicalCommit,
    DocumentRevision,
    IdempotencyRecord,
    OutboxEvent,
)
from app.canonical.repositories import CanonicalRepository
from app.canonical.state_transition import LegacyStateTransitionAdapter


def seed_project(database_url, tenant_id, project_id, *, subsection_count=2):
    engine = build_engine(database_url)
    with build_session_factory(engine)() as session:
        repo = CanonicalRepository(session, tenant_id, project_id)
        state_id = f"state-{project_id}"
        repo.create_project(
            owner_id="integration-test",
            name="Concurrency",
            genesis_state_json={"foundation_state_v0": {"world_mutations": [], "ledger_events": []}},
            genesis_state_version_id=state_id,
        )
        document_id = f"document-{project_id}"
        repo.create_document(document_id, "Concurrency")
        for ordinal in range(1, subsection_count + 1):
            repo.create_subsection(
                f"subsection-{project_id}-{ordinal}",
                document_id,
                ordinal,
                1,
                ordinal,
            )
        session.commit()
    engine.dispose()
    return state_id


def build_prepared(
    database_url,
    tenant_id,
    project_id,
    *,
    ordinal=1,
    draft="concurrent draft",
    attempt_id="attempt-1",
):
    engine = build_engine(database_url)
    with build_session_factory(engine)() as session:
        repo = CanonicalRepository(session, tenant_id, project_id)
        state = repo.get_current_state()
        subsection_id = f"subsection-{project_id}-{ordinal}"
        current_revision = repo.get_current_revision(subsection_id)
        base_revision_number = 0 if current_revision is None else current_revision.revision_number
        candidate = SubsectionCandidate.create(
            tenant_id=tenant_id,
            project_id=project_id,
            document_id=f"document-{project_id}",
            subsection_id=subsection_id,
            task_id=f"task-{project_id}",
            section=1,
            subsection=ordinal,
            ordinal=ordinal,
            title=f"Subsection {ordinal}",
            topic="Concurrency",
            base_revision_number=base_revision_number,
            base_state_version_id=state.id,
            draft=draft,
            prompt_hash=sha256_text("prompt"),
            validation=CandidateValidation(complete=True),
            handover_candidate={},
            world_mutations=(),
            events=(
                CanonicalEventCandidate(
                    event_id=f"event-{project_id}-{ordinal}-{attempt_id}",
                    event_type="subsection.accepted",
                    payload={"ordinal": ordinal},
                    provenance={"source": "postgres-integration"},
                ),
            ),
            state_frame=None,
            generation_metadata={"attempt_id": attempt_id},
        )
        base = CanonicalStateSnapshot.create(
            version_id=state.id,
            project_id=project_id,
            schema_version=state.schema_version,
            state_json=state.state_json,
        )
        transition = LegacyStateTransitionAdapter().compile(
            base_state=base, candidate=candidate
        )
        prepared = PreparedCanonicalCommit(
            candidate=candidate, state_transition=transition
        )
    engine.dispose()
    return prepared


def scoped_counts(database_url, tenant_id, project_id):
    engine = build_engine(database_url)
    with build_session_factory(engine)() as session:
        result = {
            "commits": session.scalar(
                select(func.count()).select_from(CanonicalCommit).where(
                    CanonicalCommit.tenant_id == tenant_id,
                    CanonicalCommit.project_id == project_id,
                )
            ),
            "revisions": session.scalar(
                select(func.count()).select_from(DocumentRevision).where(
                    DocumentRevision.tenant_id == tenant_id,
                    DocumentRevision.project_id == project_id,
                )
            ),
            "idempotency": session.scalar(
                select(func.count()).select_from(IdempotencyRecord).where(
                    IdempotencyRecord.tenant_id == tenant_id,
                    IdempotencyRecord.project_id == project_id,
                )
            ),
            "outbox": session.scalar(
                select(func.count()).select_from(OutboxEvent).where(
                    OutboxEvent.tenant_id == tenant_id,
                    OutboxEvent.project_id == project_id,
                )
            ),
        }
    engine.dispose()
    return result
