from __future__ import annotations

from uuid import uuid4

from app.canonical.commit_service import CanonicalCommitService
from app.canonical.database import build_engine, build_session_factory
from tests.integration.canonical.helpers import (
    build_prepared,
    scoped_counts,
    seed_project,
)


def test_one_hundred_ordered_and_reordered_replays_keep_one_revision(
    postgres_database_url,
):
    suffix = uuid4().hex[:12]
    tenant_id = f"tenant-{suffix}"
    project_id = f"project-{suffix}"
    seed_project(postgres_database_url, tenant_id, project_id)
    prepared = build_prepared(postgres_database_url, tenant_id, project_id)

    results = []
    for index in list(range(50)) + list(reversed(range(50))):
        engine = build_engine(postgres_database_url)
        with build_session_factory(engine)() as session:
            results.append(
                CanonicalCommitService(session, tenant_id, project_id).commit(
                    prepared, "replay-key"
                )
            )
        engine.dispose()

    assert len({result.commit_id for result in results}) == 1
    assert sum(not result.skipped_as_duplicate for result in results) == 1
    counts = scoped_counts(postgres_database_url, tenant_id, project_id)
    assert counts["commits"] == 1
    assert counts["revisions"] == 1
    assert counts["idempotency"] == 1
