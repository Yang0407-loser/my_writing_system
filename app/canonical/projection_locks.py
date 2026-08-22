"""PostgreSQL advisory maintenance fences for projection sink writes."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
from typing import Iterator, Literal

from sqlalchemy import Engine, text


@dataclass(frozen=True)
class ProjectionLockScope:
    tenant_id: str
    project_id: str
    projector_id: str

    def __post_init__(self) -> None:
        if not self.tenant_id or not self.project_id or not self.projector_id:
            raise ValueError("tenant_id, project_id and projector_id are required")


def advisory_keys(scope: ProjectionLockScope) -> tuple[int, int]:
    """Return two stable signed int32 keys for one projection partition."""
    digest = sha256(
        f"{scope.tenant_id}/{scope.project_id}/{scope.projector_id}".encode("utf-8")
    ).digest()
    return (
        int.from_bytes(digest[:4], "big", signed=True),
        int.from_bytes(digest[4:8], "big", signed=True),
    )


class ProjectionMaintenanceLocks:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def shared(self, scope: ProjectionLockScope):
        return self._locked(scope, "shared")

    def exclusive(self, scope: ProjectionLockScope):
        return self._locked(scope, "exclusive")

    @contextmanager
    def _locked(
        self, scope: ProjectionLockScope, mode: Literal["shared", "exclusive"]
    ) -> Iterator[None]:
        if self.engine.dialect.name != "postgresql":
            yield
            return
        key_a, key_b = advisory_keys(scope)
        suffix = "_shared" if mode == "shared" else ""
        lock = text(f"SELECT pg_advisory_lock{suffix}(:key_a, :key_b)")
        unlock = text(f"SELECT pg_advisory_unlock{suffix}(:key_a, :key_b)")
        connection = self.engine.connect()
        try:
            connection.execute(lock, {"key_a": key_a, "key_b": key_b})
            try:
                yield
            finally:
                connection.execute(unlock, {"key_a": key_a, "key_b": key_b})
        finally:
            connection.close()
