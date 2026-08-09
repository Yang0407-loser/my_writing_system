from __future__ import annotations

import pytest

from app.config import CanonicalSettings


def _env(**overrides: str) -> dict[str, str]:
    values = {
        "WRITER_TESTING": "1",
        "CANONICAL_DATABASE_URL": "sqlite:///./foundation-test.db",
        "CANONICAL_COMMIT_MODE": "legacy",
    }
    values.update(overrides)
    return values


@pytest.mark.parametrize(
    "url",
    [
        "sqlite:///./foundation-test.db",
        "postgresql+psycopg://writer:password@postgres:5432/writer",
    ],
)
def test_sqlite_and_postgres_database_urls_are_accepted(url):
    settings = CanonicalSettings.from_env(_env(CANONICAL_DATABASE_URL=url))

    assert settings.database_url == url


@pytest.mark.parametrize("mode", ["legacy", "canary", "internal_required"])
def test_supported_commit_modes_are_exact(mode):
    settings = CanonicalSettings.from_env(_env(CANONICAL_COMMIT_MODE=mode))

    assert settings.commit_mode == mode


def test_invalid_commit_mode_fails_closed():
    with pytest.raises(ValueError, match="CANONICAL_COMMIT_MODE"):
        CanonicalSettings.from_env(_env(CANONICAL_COMMIT_MODE="shadow-ish"))


def test_canary_allowlists_are_trimmed_deduplicated_and_immutable():
    settings = CanonicalSettings.from_env(
        _env(
            CANONICAL_COMMIT_MODE="canary",
            CANONICAL_CANARY_TASK_IDS=" task-b,task-a, task-b ,,",
            CANONICAL_CANARY_SUBSECTION_IDS=" sub-2, sub-1 ",
        )
    )

    assert settings.canary_task_ids == frozenset({"task-a", "task-b"})
    assert settings.canary_subsection_ids == frozenset({"sub-1", "sub-2"})


def test_production_without_database_url_fails_closed():
    with pytest.raises(ValueError, match="CANONICAL_DATABASE_URL"):
        CanonicalSettings.from_env(
            {"WRITER_TESTING": "0", "CANONICAL_COMMIT_MODE": "legacy"}
        )


def test_test_environment_has_an_isolated_sqlite_default():
    settings = CanonicalSettings.from_env(
        {"WRITER_TESTING": "1", "CANONICAL_COMMIT_MODE": "legacy"}
    )

    assert settings.database_url == "sqlite+pysqlite:///:memory:"
    assert settings.commit_mode == "legacy"


def test_non_database_scheme_is_rejected():
    with pytest.raises(ValueError, match="SQLAlchemy SQLite or PostgreSQL"):
        CanonicalSettings.from_env(_env(CANONICAL_DATABASE_URL="redis://localhost/0"))


def test_internal_required_rejects_sqlite_outside_tests():
    with pytest.raises(ValueError, match="requires PostgreSQL"):
        CanonicalSettings.from_env(
            {
                "WRITER_TESTING": "0",
                "CANONICAL_DATABASE_URL": "sqlite:///./production.db",
                "CANONICAL_COMMIT_MODE": "internal_required",
            }
        )


def test_internal_required_accepts_postgres_outside_tests():
    configured = CanonicalSettings.from_env(
        {
            "WRITER_TESTING": "0",
            "CANONICAL_DATABASE_URL": (
                "postgresql+psycopg://writer:secret@postgres:5432/writer"
            ),
            "CANONICAL_COMMIT_MODE": "internal_required",
        }
    )

    assert configured.commit_mode == "internal_required"


def test_rollout_route_is_exact_and_pre_foundation_resume_stays_legacy():
    canary = CanonicalSettings.from_env(
        _env(
            CANONICAL_COMMIT_MODE="canary",
            CANONICAL_CANARY_TASK_IDS="task-a",
            CANONICAL_CANARY_SUBSECTION_IDS="sub-1",
        )
    )
    assert canary.resolve_path("task-a", "sub-1") == "canonical"
    assert canary.resolve_path("task-a", "sub-2") == "legacy"
    assert canary.resolve_path("task-b", "sub-1") == "legacy"

    required = CanonicalSettings.from_env(
        _env(CANONICAL_COMMIT_MODE="internal_required")
    )
    assert required.resolve_path("internal", "sub-1") == "canonical"
    assert (
        required.resolve_path(
            "legacy-resume", "sub-1", pre_foundation_resume=True
        )
        == "legacy"
    )
