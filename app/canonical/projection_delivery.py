"""PostgreSQL-authoritative ordered projection delivery state transitions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from .errors import PermanentProjectionError, RetryableProjectionError
from .hashing import sha256_json
from .models import (
    CanonicalCommit,
    OutboxEvent,
    ProjectionAttempt,
    ProjectionDelivery,
    ProjectionPartition,
    ProjectionRequeueAudit,
)
from .projection_registry import DEFAULT_PROJECTOR_REGISTRY, ProjectorRegistry, RetryPolicy


@dataclass(frozen=True)
class ScanFilter:
    tenant_id: str | None = None
    project_id: str | None = None
    projector_id: str | None = None
    commit_id: str | None = None
    barrier_kind: str | None = None
    limit: int = 100


@dataclass(frozen=True)
class DeliveryClaim:
    delivery_id: str
    outbox_event_id: str
    attempt_id: str
    lease_token: str
    leased_by: str
    leased_until: datetime
    tenant_id: str
    project_id: str
    projector_id: str
    stream_position: int


@dataclass(frozen=True)
class FailureTransition:
    status: str
    available_at: datetime
    error_class: str
    error_message: str
    retry_delay_seconds: int | None


def failure_transition(
    error: Exception,
    attempt_count: int,
    policy: RetryPolicy,
    now: datetime,
) -> FailureTransition:
    """Classify a failure without consulting mutable runtime state."""
    permanent = isinstance(error, PermanentProjectionError)
    retryable = isinstance(
        error, (RetryableProjectionError, ConnectionError, TimeoutError)
    )
    exhausted = attempt_count >= policy.max_attempts
    if permanent or exhausted:
        return FailureTransition(
            "dead_letter", now, type(error).__name__, str(error), None
        )
    # Unknown exceptions intentionally retry until the registry limit.
    if retryable or not permanent:
        delay = min(
            policy.max_delay_seconds,
            policy.base_delay_seconds * (2 ** max(0, attempt_count - 1)),
        )
        return FailureTransition(
            "pending",
            now + timedelta(seconds=delay),
            type(error).__name__,
            str(error),
            delay,
        )
    raise AssertionError("unreachable classification")


class ProjectionDeliveryStore:
    def __init__(
        self,
        session: Session,
        registry: ProjectorRegistry = DEFAULT_PROJECTOR_REGISTRY,
    ) -> None:
        self.session = session
        self.registry = registry

    def claim_next(
        self,
        leased_by: str,
        scan_filter: ScanFilter = ScanFilter(),
        *,
        now: datetime | None = None,
        require_multi_worker_safety: bool = False,
    ) -> DeliveryClaim | None:
        if not leased_by.strip():
            raise ValueError("leased_by is required")
        if scan_filter.limit < 1:
            raise ValueError("limit must be positive")
        if require_multi_worker_safety and self.session.bind.dialect.name != "postgresql":
            raise RuntimeError("multi-worker safety requires PostgreSQL")
        now = now or datetime.now(timezone.utc)
        if self.session.bind.dialect.name != "postgresql":
            return self._claim_sqlite(leased_by, scan_filter, now)

        filters = []
        params: dict[str, Any] = {"now": now, "leased_by": leased_by}
        for column, value in (
            ("tenant_id", scan_filter.tenant_id),
            ("project_id", scan_filter.project_id),
            ("projector_id", scan_filter.projector_id),
            ("barrier_kind", scan_filter.barrier_kind),
        ):
            if value is not None:
                filters.append(f"candidate.{column} = :{column}")
                params[column] = value
        if scan_filter.commit_id is not None:
            filters.append("envelope.commit_id = :commit_id")
            params["commit_id"] = scan_filter.commit_id

        lease_cases = []
        for index, spec in enumerate(self.registry.all()):
            key = f"lease_{index}"
            lease_cases.append(f"WHEN '{spec.projector_id}' THEN :{key}")
            params[key] = spec.retry.lease_seconds
        lease_sql = "CASE candidate.projector_id " + " ".join(lease_cases) + " ELSE 120 END"
        token = uuid4().hex
        params["lease_token"] = token
        predicate = " AND ".join(filters)
        if predicate:
            predicate = " AND " + predicate
        statement = text(
            f"""
            WITH eligible AS (
              SELECT candidate.id
              FROM projection_deliveries candidate
              JOIN outbox_events envelope ON envelope.id = candidate.outbox_event_id
              JOIN projection_partitions partition
                ON partition.tenant_id = candidate.tenant_id
               AND partition.project_id = candidate.project_id
               AND partition.projector_id = candidate.projector_id
              WHERE partition.enrollment_status = 'active'
                AND partition.runtime_status = 'active'
                AND (
                  (candidate.status = 'pending' AND candidate.available_at <= :now)
                  OR (candidate.status = 'processing' AND candidate.leased_until < :now)
                )
                {predicate}
                AND NOT EXISTS (
                  SELECT 1 FROM projection_deliveries prior
                  WHERE prior.tenant_id = candidate.tenant_id
                    AND prior.project_id = candidate.project_id
                    AND prior.projector_id = candidate.projector_id
                    AND prior.stream_position < candidate.stream_position
                    AND prior.status <> 'published'
                )
              ORDER BY candidate.available_at, candidate.stream_position, candidate.id
              FOR UPDATE OF candidate SKIP LOCKED
              LIMIT 1
            ), claimed AS (
              UPDATE projection_deliveries candidate
              SET status = 'processing', lease_token = :lease_token,
                  leased_by = :leased_by,
                  leased_until = :now + ({lease_sql}) * interval '1 second',
                  attempt_count = candidate.attempt_count + 1,
                  last_attempt_at = :now, updated_at = :now
              FROM eligible
              WHERE candidate.id = eligible.id
              RETURNING candidate.*
            )
            SELECT * FROM claimed
            """
        )
        try:
            row = self.session.execute(statement, params).mappings().one_or_none()
            if row is None:
                self.session.commit()
                return None
            self._expire_prior_attempt(row["id"], token, now)
            attempt_id = self._add_claim_attempt(row, leased_by, token, now)
            self.session.execute(
                update(OutboxEvent)
                .where(OutboxEvent.id == row["outbox_event_id"])
                .values(
                    status="processing",
                    attempts=row["attempt_count"],
                    updated_at=now,
                )
            )
            self.session.commit()
            return self._to_claim(row, attempt_id)
        except Exception:
            self.session.rollback()
            raise

    def _claim_sqlite(self, leased_by, scan_filter, now):
        candidate = self.session.scalar(
            select(ProjectionDelivery)
            .join(OutboxEvent, OutboxEvent.id == ProjectionDelivery.outbox_event_id)
            .where(
                ProjectionDelivery.status == "pending",
                ProjectionDelivery.available_at <= now,
                *self._orm_filters(scan_filter),
            )
            .order_by(ProjectionDelivery.stream_position)
            .limit(1)
        )
        if candidate is None:
            self.session.commit()
            return None
        prior = self.session.scalar(
            select(func.count()).select_from(ProjectionDelivery).where(
                ProjectionDelivery.tenant_id == candidate.tenant_id,
                ProjectionDelivery.project_id == candidate.project_id,
                ProjectionDelivery.projector_id == candidate.projector_id,
                ProjectionDelivery.stream_position < candidate.stream_position,
                ProjectionDelivery.status != "published",
            )
        )
        if prior:
            self.session.commit()
            return None
        policy = self.registry.get(candidate.projector_id).retry
        candidate.status = "processing"
        candidate.lease_token = uuid4().hex
        candidate.leased_by = leased_by
        candidate.leased_until = now + timedelta(seconds=policy.lease_seconds)
        candidate.attempt_count += 1
        candidate.last_attempt_at = now
        self.session.flush()
        row = {column.name: getattr(candidate, column.name) for column in candidate.__table__.columns}
        attempt_id = self._add_claim_attempt(row, leased_by, candidate.lease_token, now)
        self.session.commit()
        return self._to_claim(row, attempt_id)

    def _orm_filters(self, scan_filter):
        result = []
        for column, value in (
            (ProjectionDelivery.tenant_id, scan_filter.tenant_id),
            (ProjectionDelivery.project_id, scan_filter.project_id),
            (ProjectionDelivery.projector_id, scan_filter.projector_id),
            (ProjectionDelivery.barrier_kind, scan_filter.barrier_kind),
            (OutboxEvent.commit_id, scan_filter.commit_id),
        ):
            if value is not None:
                result.append(column == value)
        return result

    def _expire_prior_attempt(self, delivery_id, new_token, now):
        self.session.execute(
            update(ProjectionAttempt)
            .where(
                ProjectionAttempt.delivery_id == delivery_id,
                ProjectionAttempt.outcome == "claimed",
                ProjectionAttempt.lease_token != new_token,
            )
            .values(outcome="lease_expired", finished_at=now)
        )

    def _add_claim_attempt(self, row, leased_by, token, now):
        attempt_id = str(uuid4())
        self.session.add(
            ProjectionAttempt(
                id=attempt_id,
                delivery_id=row["id"],
                attempt_number=row["attempt_count"],
                lease_token=token,
                leased_by=leased_by,
                trigger_source="scanner",
                outcome="claimed",
                started_at=now,
            )
        )
        self.session.flush()
        return attempt_id

    @staticmethod
    def _to_claim(row, attempt_id):
        return DeliveryClaim(
            delivery_id=row["id"],
            outbox_event_id=row["outbox_event_id"],
            attempt_id=attempt_id,
            lease_token=row["lease_token"],
            leased_by=row["leased_by"],
            leased_until=row["leased_until"],
            tenant_id=row["tenant_id"],
            project_id=row["project_id"],
            projector_id=row["projector_id"],
            stream_position=row["stream_position"],
        )

    def heartbeat(self, claim: DeliveryClaim, *, now=None) -> bool:
        now = now or datetime.now(timezone.utc)
        policy = self.registry.get(claim.projector_id).retry
        result = self.session.execute(
            update(ProjectionDelivery)
            .where(
                ProjectionDelivery.id == claim.delivery_id,
                ProjectionDelivery.status == "processing",
                ProjectionDelivery.lease_token == claim.lease_token,
            )
            .values(leased_until=now + timedelta(seconds=policy.lease_seconds), updated_at=now)
        )
        self.session.commit()
        return result.rowcount == 1

    def mark_published(self, claim: DeliveryClaim, receipt: dict[str, Any], *, now=None) -> bool:
        now = now or datetime.now(timezone.utc)
        digest = sha256_json(receipt)
        result = self.session.execute(
            update(ProjectionDelivery)
            .where(
                ProjectionDelivery.id == claim.delivery_id,
                ProjectionDelivery.status == "processing",
                ProjectionDelivery.lease_token == claim.lease_token,
            )
            .values(
                status="published", published_at=now, receipt_json=receipt,
                receipt_digest=digest, lease_token=None, leased_by=None,
                leased_until=None, last_error_code=None, last_error_class=None,
                last_error_message=None, updated_at=now,
            )
        )
        if result.rowcount != 1:
            self.session.rollback()
            return False
        self.session.execute(
            update(ProjectionAttempt)
            .where(ProjectionAttempt.id == claim.attempt_id, ProjectionAttempt.lease_token == claim.lease_token)
            .values(outcome="succeeded", finished_at=now)
        )
        cursor_result = self.session.execute(
            update(ProjectionPartition)
            .where(
                ProjectionPartition.tenant_id == claim.tenant_id,
                ProjectionPartition.project_id == claim.project_id,
                ProjectionPartition.projector_id == claim.projector_id,
                ProjectionPartition.last_published_position == claim.stream_position - 1,
            )
            .values(last_published_position=claim.stream_position, last_published_event_id=claim.outbox_event_id, updated_at=now)
        )
        if cursor_result.rowcount != 1:
            self.session.rollback()
            return False
        self.session.execute(
            update(OutboxEvent).where(OutboxEvent.id == claim.outbox_event_id).values(
                status="published", published_at=now, last_error=None, updated_at=now
            )
        )
        self.session.commit()
        return True

    def record_failure(self, claim: DeliveryClaim, error: Exception, *, now=None) -> bool:
        now = now or datetime.now(timezone.utc)
        attempt_count = self.session.scalar(
            select(ProjectionDelivery.attempt_count).where(
                ProjectionDelivery.id == claim.delivery_id,
                ProjectionDelivery.status == "processing",
                ProjectionDelivery.lease_token == claim.lease_token,
            )
        )
        if attempt_count is None:
            self.session.rollback()
            return False
        transition = failure_transition(
            error,
            attempt_count,
            self.registry.get(claim.projector_id).retry,
            now,
        )
        result = self.session.execute(
            update(ProjectionDelivery)
            .where(
                ProjectionDelivery.id == claim.delivery_id,
                ProjectionDelivery.status == "processing",
                ProjectionDelivery.lease_token == claim.lease_token,
            )
            .values(
                status=transition.status,
                available_at=transition.available_at,
                lease_token=None,
                leased_by=None,
                leased_until=None,
                last_error_class=transition.error_class[:255],
                last_error_message=transition.error_message[:4000],
                published_at=None,
                receipt_json=None,
                receipt_digest=None,
                updated_at=now,
            )
        )
        if result.rowcount != 1:
            self.session.rollback()
            return False
        self.session.execute(
            update(ProjectionAttempt).where(
                ProjectionAttempt.id == claim.attempt_id,
                ProjectionAttempt.lease_token == claim.lease_token,
            ).values(
                outcome=("retry_scheduled" if transition.status == "pending" else "dead_lettered"),
                finished_at=now, error_class=transition.error_class[:255],
                error_message=transition.error_message[:4000],
                retry_delay_seconds=transition.retry_delay_seconds,
            )
        )
        self.session.execute(
            update(OutboxEvent).where(OutboxEvent.id == claim.outbox_event_id).values(
                status="failed", attempts=attempt_count,
                available_at=transition.available_at,
                last_error=f"{transition.error_class}: {transition.error_message}"[:4000],
                published_at=None, updated_at=now,
            )
        )
        self.session.commit()
        return True

    def requeue_dead_letter(self, delivery_id, operator_id, reason, *, now=None) -> bool:
        if not operator_id.strip() or not reason.strip():
            raise ValueError("operator_id and reason are required")
        now = now or datetime.now(timezone.utc)
        delivery = self.session.scalar(
            select(ProjectionDelivery).where(
                ProjectionDelivery.id == delivery_id,
                ProjectionDelivery.status == "dead_letter",
            ).with_for_update()
        )
        if delivery is None:
            self.session.rollback()
            return False
        self.session.add(ProjectionRequeueAudit(
            id=str(uuid4()), delivery_id=delivery.id, tenant_id=delivery.tenant_id,
            project_id=delivery.project_id, projector_id=delivery.projector_id,
            prior_attempt_count=delivery.attempt_count, operator_id=operator_id.strip(),
            reason=reason.strip(), created_at=now,
        ))
        delivery.status = "pending"
        delivery.available_at = now
        delivery.lease_token = delivery.leased_by = delivery.leased_until = None
        delivery.published_at = None
        delivery.receipt_json = delivery.receipt_digest = None
        self.session.execute(
            update(OutboxEvent).where(OutboxEvent.id == delivery.outbox_event_id).values(
                status="pending", available_at=now, published_at=None, updated_at=now
            )
        )
        self.session.commit()
        return True

    def lag(self, scan_filter: ScanFilter = ScanFilter()) -> int:
        statement = select(func.coalesce(func.sum(
            CanonicalCommit.stream_position - ProjectionPartition.last_published_position
        ), 0)).select_from(ProjectionPartition).join(
            CanonicalCommit,
            (CanonicalCommit.tenant_id == ProjectionPartition.tenant_id)
            & (CanonicalCommit.project_id == ProjectionPartition.project_id),
        ).where(
            CanonicalCommit.stream_position == select(func.max(CanonicalCommit.stream_position)).where(
                CanonicalCommit.project_id == ProjectionPartition.project_id
            ).correlate(ProjectionPartition).scalar_subquery()
        )
        if scan_filter.tenant_id is not None:
            statement = statement.where(ProjectionPartition.tenant_id == scan_filter.tenant_id)
        if scan_filter.project_id is not None:
            statement = statement.where(ProjectionPartition.project_id == scan_filter.project_id)
        if scan_filter.projector_id is not None:
            statement = statement.where(ProjectionPartition.projector_id == scan_filter.projector_id)
        return int(self.session.scalar(statement) or 0)
