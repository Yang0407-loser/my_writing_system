"""Best-effort Celery wake-up for the PostgreSQL projection scanner."""

from __future__ import annotations

import logging
from functools import lru_cache
from threading import Lock

from .canonical.projection_delivery import ScanFilter
from .canonical.projection_worker import build_production_projection_worker
from .celery_app import celery_app
from .config import settings


logger = logging.getLogger("writing_system.projection")
_failure_lock = Lock()
_wakeup_failure_count = 0


@lru_cache(maxsize=1)
def _production_worker():
    return build_production_projection_worker("celery-projection-wakeup")


def wakeup_failure_count() -> int:
    with _failure_lock:
        return _wakeup_failure_count


def try_wake_projection_scanner(
    *,
    tenant_id: str | None = None,
    project_id: str | None = None,
    projector_id: str | None = None,
    barrier_kind: str | None = None,
) -> bool:
    """Publish only optional scope hints; broker failure never changes Canon."""
    global _wakeup_failure_count
    try:
        wake_projection_scanner.delay(
            tenant_id=tenant_id,
            project_id=project_id,
            projector_id=projector_id,
            barrier_kind=barrier_kind,
        )
        return True
    except Exception as exc:
        with _failure_lock:
            _wakeup_failure_count += 1
        logger.warning(
            "projection wake-up publish failed: tenant=%s project=%s error=%s",
            tenant_id,
            project_id,
            type(exc).__name__,
        )
        return False


@celery_app.task(name="wake_projection_scanner", ignore_result=True)
def wake_projection_scanner(
    *,
    tenant_id: str | None = None,
    project_id: str | None = None,
    projector_id: str | None = None,
    barrier_kind: str | None = None,
) -> dict[str, int]:
    worker = _production_worker()
    summary = worker.scan_once(
        ScanFilter(
            tenant_id=tenant_id,
            project_id=project_id,
            projector_id=projector_id,
            barrier_kind=barrier_kind,
            limit=settings.PROJECTION_SCAN_BATCH_SIZE,
        )
    )
    return {
        "claimed": summary.claimed,
        "published": summary.published,
        "retried": summary.retried,
        "dead_lettered": summary.dead_lettered,
        "stale": summary.stale,
    }
