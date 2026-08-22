from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from alembic import command
from alembic.config import Config


def _database_name(database_url: str) -> str:
    normalized = database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    return urlsplit(normalized).path.rsplit("/", 1)[-1]


@pytest.fixture(scope="session")
def postgres_database_url() -> str:
    database_url = os.getenv("TEST_CANONICAL_DATABASE_URL", "").strip()
    if not database_url:
        pytest.skip("TEST_CANONICAL_DATABASE_URL is required for the PostgreSQL gate")
    if not database_url.startswith(("postgresql://", "postgresql+psycopg://")):
        pytest.fail("TEST_CANONICAL_DATABASE_URL must use PostgreSQL")
    if not _database_name(database_url).endswith("_test"):
        pytest.fail("PostgreSQL integration tests require a database ending in _test")

    root = Path(__file__).resolve().parents[3]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    return database_url
