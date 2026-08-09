from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from app.canonical.commit_service import CanonicalCommitService
from app.canonical.database import build_engine, build_session_factory
from tests.integration.canonical.helpers import (
    build_prepared,
    scoped_counts,
    seed_project,
)


pytestmark = pytest.mark.postgres


def _commit_once(database_url, prepared, key):
    engine = build_engine(database_url)
    try:
        with build_session_factory(engine)() as session:
            service = CanonicalCommitService(
                session,
                prepared.candidate.tenant_id,
                prepared.candidate.project_id,
            )
            result = service.commit(prepared, key)
            return ("ok", result.candidate_hash, result.skipped_as_duplicate)
    except Exception as exc:
        return ("error", type(exc).__name__, str(exc))
    finally:
        engine.dispose()


def _scope():
    suffix = uuid4().hex[:12]
    return f"tenant-{suffix}", f"project-{suffix}"


def test_twenty_same_key_same_hash_create_one_canon(postgres_database_url):
    tenant_id, project_id = _scope()
    seed_project(postgres_database_url, tenant_id, project_id)
    prepared = build_prepared(postgres_database_url, tenant_id, project_id)

    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(
            pool.map(
                lambda _: _commit_once(
                    postgres_database_url, prepared, "same-key"
                ),
                range(20),
            )
        )

    assert all(item[0] == "ok" for item in results)
    assert sum(not item[2] for item in results) == 1
    counts = scoped_counts(postgres_database_url, tenant_id, project_id)
    assert counts["commits"] == 1
    assert counts["revisions"] == 1
    assert counts["idempotency"] == 1


def test_same_key_different_hash_has_one_winner_and_conflicts(
    postgres_database_url,
):
    tenant_id, project_id = _scope()
    seed_project(postgres_database_url, tenant_id, project_id)
    first = build_prepared(
        postgres_database_url, tenant_id, project_id, draft="first", attempt_id="a"
    )
    second = build_prepared(
        postgres_database_url, tenant_id, project_id, draft="second", attempt_id="b"
    )
    work = [first] * 10 + [second] * 10

    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(
            pool.map(
                lambda prepared: _commit_once(
                    postgres_database_url, prepared, "shared-key"
                ),
                work,
            )
        )

    successes = [item for item in results if item[0] == "ok"]
    failures = [item for item in results if item[0] == "error"]
    assert successes
    assert len({item[1] for item in successes}) == 1
    assert failures and all(item[1] == "IdempotencyConflict" for item in failures)
    assert scoped_counts(postgres_database_url, tenant_id, project_id)["commits"] == 1


def test_same_subsection_different_keys_yield_revision_conflicts(
    postgres_database_url,
):
    tenant_id, project_id = _scope()
    seed_project(postgres_database_url, tenant_id, project_id)
    prepared = [
        build_prepared(
            postgres_database_url,
            tenant_id,
            project_id,
            draft=f"draft-{index}",
            attempt_id=f"attempt-{index}",
        )
        for index in range(20)
    ]

    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(
            pool.map(
                lambda pair: _commit_once(
                    postgres_database_url, pair[1], f"key-{pair[0]}"
                ),
                enumerate(prepared),
            )
        )

    assert sum(item[0] == "ok" for item in results) == 1
    assert all(
        item[0] == "ok" or item[1] == "RevisionConflict" for item in results
    )
    assert scoped_counts(postgres_database_url, tenant_id, project_id)["revisions"] == 1


def test_different_subsections_on_same_state_head_yield_state_conflict(
    postgres_database_url,
):
    tenant_id, project_id = _scope()
    seed_project(postgres_database_url, tenant_id, project_id, subsection_count=2)
    first = build_prepared(postgres_database_url, tenant_id, project_id, ordinal=1)
    second = build_prepared(postgres_database_url, tenant_id, project_id, ordinal=2)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda pair: _commit_once(
                    postgres_database_url, pair[1], f"key-{pair[0]}"
                ),
                enumerate((first, second)),
            )
        )

    assert sum(item[0] == "ok" for item in results) == 1
    assert any(item[0] == "error" and item[1] == "StateVersionConflict" for item in results)
