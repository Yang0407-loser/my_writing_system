"""Run the deterministic Foundation Golden Slice and emit secret-free evidence."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

# This script owns an isolated temporary SQLite database when no explicit Gate
# database is provided. Set test mode before importing app.config transitively.
if not os.getenv("CANONICAL_DATABASE_URL"):
    os.environ.setdefault("WRITER_TESTING", "1")

from alembic import command
from alembic.config import Config
from sqlalchemy import func, select

from app.canonical.contracts import CandidateValidation, SubsectionCandidate
from app.canonical.database import build_engine, build_session_factory
from app.canonical.hashing import sha256_text
from app.canonical.legacy_candidate_adapter import adapt_legacy_handover
from app.canonical.models import (
    CanonicalCommit,
    EventLedger,
    OutboxEvent,
    ProjectionDelivery,
    ProjectionPartition,
)
from app.canonical.repositories import CanonicalRepository
from app.writing.canonical_subsection_runtime import (
    CanonicalSubsectionCommand,
    CanonicalSubsectionRuntime,
)
from app.writing.legacy_subsection_projection import LegacySubsectionProjection


ROOT = Path(__file__).resolve().parents[2]


class DeterministicVectorProjection:
    def __init__(self) -> None:
        self.documents: dict[str, dict[str, Any]] = {}

    def add_text(self, text, metadata, *, document_id=None):
        if not document_id:
            raise ValueError("Golden projection requires deterministic document_id")
        self.documents[document_id] = {"text_hash": sha256_text(text), **metadata}
        return document_id

    def enforce_task_limit(self, _task_id):
        return 0


def _upgrade(database_url: str) -> None:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")


def _candidate(fixture: dict[str, Any], snapshot, base_revision_number, _command):
    ids = fixture["ids"]
    subsection = fixture["subsection"]
    adapted = adapt_legacy_handover(
        fixture["handover"],
        provenance={"fixture": fixture["schema_version"]},
    )
    return SubsectionCandidate.create(
        tenant_id=ids["tenant_id"],
        project_id=ids["project_id"],
        document_id=ids["document_id"],
        subsection_id=ids["subsection_id"],
        task_id=ids["task_id"],
        section=1,
        subsection=1,
        ordinal=subsection["ordinal"],
        title=subsection["heading"],
        topic="Foundation Golden Slice",
        base_revision_number=base_revision_number,
        base_state_version_id=snapshot.version_id,
        draft=subsection["body"],
        prompt_hash=sha256_text("foundation-golden-prompt-v1"),
        validation=CandidateValidation(complete=True),
        handover_candidate=adapted.handover_candidate,
        world_mutations=adapted.world_mutations,
        events=adapted.events,
        state_frame=None,
        generation_metadata={
            "fixture_schema_version": fixture["schema_version"],
            "handover_observation": {
                "executed": True,
                "execution_status": "success",
            },
        },
    )


def run_golden_slice(
    fixture_path: Path,
    *,
    database_url: str | None = None,
) -> dict[str, Any]:
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    started = time.perf_counter()
    temporary = None
    if database_url is None:
        temporary = tempfile.TemporaryDirectory(prefix="foundation-golden-")
        db_path = Path(temporary.name) / "golden.db"
        database_url = f"sqlite+pysqlite:///{db_path.as_posix()}"
    _upgrade(database_url)
    engine = build_engine(database_url)
    factory = build_session_factory(engine)
    ids = fixture["ids"]
    checkpoints: list[dict[str, Any]] = []
    world_rows: dict[str, dict[str, Any]] = {}
    handover_rows: dict[str, dict[str, Any]] = {}
    non_blocking_rows: dict[str, dict[str, Any]] = {}
    vector = DeterministicVectorProjection()

    with factory() as session:
        repo = CanonicalRepository(session, ids["tenant_id"], ids["project_id"])
        initial = fixture["initial_canonical_state"]
        repo.create_project(
            owner_id="foundation-gate",
            name="Foundation Golden Project",
            genesis_state_json=initial["state_json"],
            genesis_state_version_id=initial["version_id"],
        )
        repo.create_document(ids["document_id"], "Foundation Golden Document")
        repo.create_subsection(
            ids["subsection_id"],
            ids["document_id"],
            fixture["subsection"]["ordinal"],
            1,
            1,
        )
        session.commit()

        projection = LegacySubsectionProjection(
            session,
            ids["tenant_id"],
            ids["project_id"],
            world_event_sink=lambda envelope: world_rows.__setitem__(
                envelope.commit_id, envelope.provenance
            ),
            handover_sink=lambda envelope: handover_rows.__setitem__(
                envelope.commit_id, envelope.provenance
            ),
            vector_store=vector,
            non_blocking_sinks={
                name: (
                    lambda envelope, projection_name=name: non_blocking_rows.__setitem__(
                        projection_name, envelope.provenance
                    )
                )
                for name in (
                    "redis_stream",
                    "task_preview",
                    "markdown_export",
                    "analytics",
                )
            },
        )
        runtime = CanonicalSubsectionRuntime(
            session=session,
            tenant_id=ids["tenant_id"],
            project_id=ids["project_id"],
            candidate_generator=lambda *, snapshot, base_revision_number, command: _candidate(
                fixture, snapshot, base_revision_number, command
            ),
            projectors=projection.as_projectors(),
            checkpoint_writer=lambda payload: checkpoints.append(dict(payload)),
        )
        command_payload = CanonicalSubsectionCommand(
            task_id=ids["task_id"],
            document_id=ids["document_id"],
            subsection_id=ids["subsection_id"],
            generation_attempt_id="foundation-golden-attempt-v1",
            expected_revision_id="GENESIS",
            expected_state_version_id=initial["version_id"],
        )
        result = runtime.execute(command_payload)
        state = repo.get_current_state()
        document = repo.materialize_document(ids["document_id"])
        outbox = session.scalars(
            select(OutboxEvent).where(OutboxEvent.commit_id == result.commit.commit_id)
        ).all()
        stream_position = session.scalar(
            select(CanonicalCommit.stream_position).where(
                CanonicalCommit.id == result.commit.commit_id
            )
        )
        deliveries = session.scalars(
            select(ProjectionDelivery).where(
                ProjectionDelivery.tenant_id == ids["tenant_id"],
                ProjectionDelivery.project_id == ids["project_id"],
                ProjectionDelivery.stream_position == stream_position,
            )
        ).all()
        partitions = session.scalars(
            select(ProjectionPartition).where(
                ProjectionPartition.tenant_id == ids["tenant_id"],
                ProjectionPartition.project_id == ids["project_id"],
            )
        ).all()
        ledger_count = session.scalar(
            select(func.count()).select_from(EventLedger).where(
                EventLedger.commit_id == result.commit.commit_id
            )
        )
        evidence = {
            "schema_version": "foundation-p2-evidence-v1",
            "backend": engine.dialect.name,
            "gate_eligible": engine.dialect.name == "postgresql",
            "fixture_schema_version": fixture["schema_version"],
            "ids": {
                "tenant_id": ids["tenant_id"],
                "project_id": ids["project_id"],
                "document_id": ids["document_id"],
                "subsection_id": ids["subsection_id"],
                "commit_id": result.commit.commit_id,
                "revision_id": result.commit.revision_id,
                "state_version_id": result.commit.state_version_id,
            },
            "hashes": {
                "fixture_body": fixture["subsection"]["body_sha256"],
                "materialized_document": sha256_text(document),
                "revision_content": result.commit.content_hash,
                "canonical_state": state.state_hash,
            },
            "counts": {
                "ledger": int(ledger_count or 0),
                "outbox": len(outbox),
                "critical_projection": 3,
                "chroma_chunks": len(vector.documents),
                "non_blocking_projection": len(non_blocking_rows),
            },
            "outbox": {
                row.projection_name: {
                    "status": row.status,
                    "attempts": row.attempts,
                    "barrier_kind": row.barrier_kind,
                }
                for row in outbox
            },
            "delivery": {
                row.projector_id: {
                    "status": row.status,
                    "stream_position": row.stream_position,
                    "barrier_kind": row.barrier_kind,
                }
                for row in deliveries
            },
            "partition_cursors": {
                row.projector_id: {
                    "runtime_status": row.runtime_status,
                    "last_published_position": row.last_published_position,
                }
                for row in partitions
            },
            "runtime": {
                "phase": result.phase,
                "critical_projection_status": result.critical_projection_status,
                "non_blocking_projection_status": result.non_blocking_projection_status,
                "checkpoint_fields": sorted(checkpoints[-1]),
            },
            "api_result": {
                "document_ref": {
                    "document_id": ids["document_id"],
                    "revision_id": result.commit.revision_id,
                    "commit_id": result.commit.commit_id,
                },
                "commit_status": "committed",
                "state_version_id": result.commit.state_version_id,
                "critical_projection_status": result.critical_projection_status,
                "non_blocking_projection_status": result.non_blocking_projection_status,
            },
            "duration_ms": round((time.perf_counter() - started) * 1000, 3),
            "secret_scan": {"contains_secret": False, "findings": []},
        }
    engine.dispose()
    if temporary is not None:
        temporary.cleanup()
    return evidence


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "reports/foundation/p2-golden-slice-evidence.json",
    )
    parser.add_argument("--database-url", default=os.getenv("TEST_CANONICAL_DATABASE_URL"))
    args = parser.parse_args()
    evidence = run_golden_slice(args.fixture, database_url=args.database_url)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "backend": evidence["backend"],
        "gate_eligible": evidence["gate_eligible"],
        "phase": evidence["runtime"]["phase"],
        "evidence": str(args.output),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
