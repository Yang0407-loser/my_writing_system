from __future__ import annotations

import multiprocessing
import time
from uuid import uuid4

import pytest

from app.canonical.commit_service import CanonicalCommitService
from app.canonical.contracts import PreparedCanonicalCommit
from app.canonical.database import build_engine, build_session_factory
from tests.integration.canonical.helpers import (
    build_prepared,
    scoped_counts,
    seed_project,
)


pytestmark = pytest.mark.postgres


def _crash_worker(database_url, prepared_json, key, barrier, ready_queue):
    prepared = PreparedCanonicalCommit.model_validate_json(prepared_json)
    if barrier == "before_transaction":
        ready_queue.put(barrier)
        time.sleep(600)

    def hook(stage):
        target = {
            "during_transaction": "after_state",
            "after_commit": "after_commit",
        }.get(barrier)
        if stage == target:
            ready_queue.put(barrier)
            time.sleep(600)

    engine = build_engine(database_url)
    with build_session_factory(engine)() as session:
        CanonicalCommitService(
            session,
            prepared.candidate.tenant_id,
            prepared.candidate.project_id,
            failure_hook=hook,
        ).commit(prepared, key)
    engine.dispose()


@pytest.mark.parametrize(
    "barrier,expect_duplicate",
    [
        ("before_transaction", False),
        ("during_transaction", False),
        ("after_commit", True),
    ],
)
def test_process_termination_recovers_one_complete_commit(
    postgres_database_url, barrier, expect_duplicate
):
    suffix = uuid4().hex[:12]
    tenant_id = f"tenant-{suffix}"
    project_id = f"project-{suffix}"
    seed_project(postgres_database_url, tenant_id, project_id)
    prepared = build_prepared(postgres_database_url, tenant_id, project_id)
    key = f"crash-{barrier}"
    context = multiprocessing.get_context("spawn")
    ready = context.Queue()
    process = context.Process(
        target=_crash_worker,
        args=(
            postgres_database_url,
            prepared.model_dump_json(),
            key,
            barrier,
            ready,
        ),
    )
    process.start()
    assert ready.get(timeout=30) == barrier
    process.kill()
    process.join(timeout=30)
    assert not process.is_alive()

    engine = build_engine(postgres_database_url)
    with build_session_factory(engine)() as session:
        recovered = CanonicalCommitService(session, tenant_id, project_id).commit(
            prepared, key
        )
    engine.dispose()

    assert recovered.skipped_as_duplicate is expect_duplicate
    counts = scoped_counts(postgres_database_url, tenant_id, project_id)
    assert counts["commits"] == 1
    assert counts["revisions"] == 1
    assert counts["idempotency"] == 1
