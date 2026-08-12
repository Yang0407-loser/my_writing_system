from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select

from app.canonical.database import build_engine, build_session_factory
from app.canonical.hashing import sha256_json
from app.canonical.models import (
    Base,
    CanonicalCommit,
    CanonicalProject,
    CanonicalStateVersion,
    OutboxEvent,
    ProjectionDelivery,
    ProjectionPartition,
)
from app.canonical.repositories import CanonicalRepository
from app.canonical.snapshot import (
    canonical_snapshot_bytes,
    export_project_snapshot,
    import_project_snapshot,
)


def _seed(session):
    repo = CanonicalRepository(session, "tenant-golden", "project-golden")
    repo.create_project(
        owner_id="owner-golden",
        name="Golden",
        genesis_state_json={"foundation_state_v0": {"ledger_events": []}},
        genesis_state_version_id="state-genesis",
    )
    repo.create_document("document-golden", "Golden document")
    repo.create_subsection("subsection-1", "document-golden", 1, 1, 1)
    repo.create_subsection("subsection-2", "document-golden", 2, 1, 2)
    position = repo.next_stream_position(repo.get_project_for_update())
    repo.create_commit_envelope(
        "commit-1", "a" * 64, 0, "state-genesis", position
    )
    committed_state = {"foundation_state_v0": {"ledger_events": ["golden_seeded"]}}
    session.add(
        CanonicalStateVersion(
            id="state-commit-1",
            tenant_id="tenant-golden",
            project_id="project-golden",
            commit_id="commit-1",
            origin="commit",
            parent_state_version_id="state-genesis",
            transition_version="snapshot-test-v0",
            schema_version="canonical-state-v0",
            state_json=committed_state,
            state_hash=sha256_json(committed_state),
        )
    )
    repo.get_project().current_state_version_id = "state-commit-1"
    repo.append_revision(
        "revision-1", "commit-1", "subsection-1", "Golden first", "test"
    )
    repo.append_revision(
        "revision-2", "commit-1", "subsection-2", "Golden second", "test"
    )
    repo.append_ledger_event(
        ledger_id="ledger-1",
        commit_id="commit-1",
        ordinal=1,
        event_type="golden_seeded",
        payload={"subsections": 2},
        evidence_refs=["fixture"],
    )
    now = datetime.now(timezone.utc)
    session.add_all(
        OutboxEvent(
            id=f"envelope-{projector_id}",
            tenant_id="tenant-golden",
            project_id="project-golden",
            commit_id="commit-1",
            projection_name=projector_id,
            barrier_kind=barrier_kind,
            event_type="canonical.subsection.committed",
            payload_json={"commit_id": "commit-1"},
            stream_position=position,
            status=("published" if projector_id == "legacy_world_event" else "pending"),
            attempts=(1 if projector_id == "legacy_world_event" else 0),
            available_at=now,
            published_at=(now if projector_id == "legacy_world_event" else None),
        )
        for projector_id, barrier_kind in (
            ("legacy_world_event", "critical"),
            ("analytics", "non_blocking"),
        )
    )
    session.commit()
    return repo


def test_portable_snapshot_restores_without_redis_chroma_or_output(tmp_path):
    source_url = f"sqlite+pysqlite:///{(tmp_path / 'source.db').as_posix()}"
    target_url = f"sqlite+pysqlite:///{(tmp_path / 'target.db').as_posix()}"
    source_engine = build_engine(source_url)
    target_engine = build_engine(target_url)
    Base.metadata.create_all(source_engine)
    Base.metadata.create_all(target_engine)

    with build_session_factory(source_engine)() as source_session:
        source_repo = _seed(source_session)
        expected_text_hash = source_repo.materialize_document_hash("document-golden")
        expected_state = source_repo.get_current_state()
        snapshot = export_project_snapshot(
            source_session, tenant_id="tenant-golden", project_id="project-golden"
        )
        assert canonical_snapshot_bytes(snapshot) == canonical_snapshot_bytes(
            export_project_snapshot(
                source_session,
                tenant_id="tenant-golden",
                project_id="project-golden",
            )
        )

    assert not (tmp_path / "redis").exists()
    assert not (tmp_path / "chroma").exists()
    assert not (tmp_path / "output").exists()

    with build_session_factory(target_engine)() as target_session:
        import_project_snapshot(target_session, snapshot)
        target_session.commit()
        restored = CanonicalRepository(
            target_session, "tenant-golden", "project-golden"
        )
        assert restored.materialize_document_hash("document-golden") == expected_text_hash
        assert restored.get_current_state().id == expected_state.id
        assert restored.get_current_state().state_hash == expected_state.state_hash
        assert sha256_json(restored.list_ledger_payloads()) == snapshot["integrity"]["ledger_hash"]
        assert target_session.scalar(
            select(func.count()).select_from(ProjectionPartition)
        ) == 7

    source_engine.dispose()
    target_engine.dispose()


def test_v0_restore_creates_deliveries_only_for_imported_envelopes(tmp_path):
    source_url = f"sqlite+pysqlite:///{(tmp_path / 'source-v0.db').as_posix()}"
    target_url = f"sqlite+pysqlite:///{(tmp_path / 'target-v0.db').as_posix()}"
    source_engine = build_engine(source_url)
    target_engine = build_engine(target_url)
    Base.metadata.create_all(source_engine)
    Base.metadata.create_all(target_engine)

    with build_session_factory(source_engine)() as source_session:
        _seed(source_session)
        old_snapshot = export_project_snapshot(
            source_session,
            tenant_id="tenant-golden",
            project_id="project-golden",
        )
        old_snapshot["tables"].pop(ProjectionDelivery.__tablename__)
        old_snapshot["tables"].pop(ProjectionPartition.__tablename__)
        old_snapshot["tables"][CanonicalProject.__tablename__][0].pop(
            "next_stream_position"
        )
        for row in old_snapshot["tables"][CanonicalCommit.__tablename__]:
            row.pop("stream_position")
        for row in old_snapshot["tables"][OutboxEvent.__tablename__]:
            row.pop("stream_position")

    with build_session_factory(target_engine)() as target_session:
        import_project_snapshot(target_session, old_snapshot)
        target_session.commit()
        envelope_ids = set(target_session.scalars(select(OutboxEvent.id)).all())
        deliveries = target_session.scalars(select(ProjectionDelivery)).all()
        assert len(envelope_ids) == len(deliveries) == 2
        assert {row.outbox_event_id for row in deliveries} == envelope_ids
        assert {row.stream_position for row in deliveries} == {1}
        assert target_session.get(
            CanonicalProject, "project-golden"
        ).next_stream_position == 1
        delivery_by_projector = {row.projector_id: row for row in deliveries}
        assert delivery_by_projector["legacy_world_event"].status == "published"
        assert delivery_by_projector["analytics"].status == "pending"
        partitions = {
            row.projector_id: row
            for row in target_session.scalars(select(ProjectionPartition)).all()
        }
        assert partitions["legacy_world_event"].last_published_position == 1
        assert (
            partitions["legacy_world_event"].last_published_event_id
            == "envelope-legacy_world_event"
        )
        assert partitions["analytics"].last_published_position == 0
        assert target_session.scalar(
            select(func.count()).select_from(ProjectionPartition)
        ) == 7

    source_engine.dispose()
    target_engine.dispose()
