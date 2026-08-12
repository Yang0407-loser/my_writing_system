"""PostgreSQL-authoritative ordered projection delivery state transitions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from .errors import (
    PermanentProjectionError,
    ProjectionConflictError,
    RetryableProjectionError,
)
from .hashing import sha256_json
from .models import (
    CanonicalCommit,
    OutboxEvent,
    ProjectionAttempt,
    ProjectionDelivery,
    ProjectionPartition,
    ProjectionRequeueAudit,
)
from .projection_registry import (
    DEFAULT_PROJECTOR_REGISTRY,
    ProjectorRegistry,
    ProjectorSpec,
    RetryPolicy,
)


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


@dataclass(frozen=True)
class _ProcessingIdentity:
    delivery: ProjectionDelivery
    envelope: OutboxEvent
    commit: CanonicalCommit
    partition: ProjectionPartition | None
    attempt: ProjectionAttempt | None
    spec: ProjectorSpec | None

    def agrees(self, *, require_exact_next: bool = True) -> bool:
        return bool(
            self.partition is not None
            and self.attempt is not None
            and self.spec is not None
            and self.delivery.outbox_event_id == self.envelope.id
            and self.envelope.commit_id == self.commit.id
            and self.envelope.tenant_id == self.commit.tenant_id
            and self.envelope.project_id == self.commit.project_id
            and self.envelope.stream_position == self.commit.stream_position
            and self.partition.tenant_id == self.envelope.tenant_id
            and self.partition.project_id == self.envelope.project_id
            and self.partition.projector_id == self.envelope.projection_name
            and self.partition.enrollment_status == "active"
            and self.partition.runtime_status == "active"
            and self.spec.projector_id == self.envelope.projection_name
            and self.partition.projector_version == self.spec.version
            and (
                not require_exact_next
                or self.partition.last_published_position
                == self.envelope.stream_position - 1
            )
            and self.envelope.barrier_kind == self.spec.barrier_kind
            and self.delivery.tenant_id == self.envelope.tenant_id
            and self.delivery.project_id == self.envelope.project_id
            and self.delivery.projector_id == self.envelope.projection_name
            and self.delivery.projector_version == self.partition.projector_version
            and self.delivery.barrier_kind == self.envelope.barrier_kind
            and self.delivery.stream_position == self.envelope.stream_position
            and self.attempt.delivery_id == self.delivery.id
            and self.attempt.attempt_number == self.delivery.attempt_count
            and self.attempt.lease_token == self.delivery.lease_token
            and self.attempt.leased_by == self.delivery.leased_by
            and self.attempt.outcome == "claimed"
        )


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
            ("envelope.tenant_id", scan_filter.tenant_id),
            ("envelope.project_id", scan_filter.project_id),
            ("envelope.projection_name", scan_filter.projector_id),
            ("envelope.barrier_kind", scan_filter.barrier_kind),
        ):
            if value is not None:
                parameter = column.rsplit(".", 1)[-1]
                if parameter == "projection_name":
                    parameter = "projector_id"
                filters.append(f"{column} = :{parameter}")
                params[parameter] = value
        if scan_filter.commit_id is not None:
            filters.append("envelope.commit_id = :commit_id")
            params["commit_id"] = scan_filter.commit_id

        registry_rows = []
        for index, spec in enumerate(self.registry.all()):
            registry_rows.append(
                f"(:registry_id_{index}, :registry_version_{index}, "
                f":registry_barrier_{index}, :lease_{index})"
            )
            params[f"registry_id_{index}"] = spec.projector_id
            params[f"registry_version_{index}"] = spec.version
            params[f"registry_barrier_{index}"] = spec.barrier_kind
            params[f"lease_{index}"] = spec.retry.lease_seconds
        registry_sql = (
            ", ".join(registry_rows)
            if registry_rows
            else "(NULL::varchar, NULL::varchar, NULL::varchar, NULL::integer)"
        )
        token = uuid4().hex
        params["lease_token"] = token
        predicate = " AND ".join(filters)
        if predicate:
            predicate = " AND " + predicate
        statement = text(
            f"""
            WITH registered(
              projector_id, projector_version, barrier_kind, lease_seconds
            ) AS (
              VALUES {registry_sql}
            ), eligible AS (
              SELECT candidate.id, registered.lease_seconds
              FROM outbox_events envelope
              JOIN projection_deliveries candidate
                ON candidate.outbox_event_id = envelope.id
              JOIN projection_partitions partition
                ON partition.tenant_id = envelope.tenant_id
               AND partition.project_id = envelope.project_id
               AND partition.projector_id = envelope.projection_name
              JOIN registered
                ON registered.projector_id = envelope.projection_name
               AND registered.projector_version = partition.projector_version
               AND registered.barrier_kind = envelope.barrier_kind
              WHERE partition.enrollment_status = 'active'
                AND partition.runtime_status = 'active'
                AND candidate.tenant_id = envelope.tenant_id
                AND candidate.project_id = envelope.project_id
                AND candidate.projector_id = envelope.projection_name
                AND candidate.projector_version = partition.projector_version
                AND candidate.barrier_kind = envelope.barrier_kind
                AND candidate.stream_position = envelope.stream_position
                AND candidate.stream_position = partition.last_published_position + 1
                AND (
                  (candidate.status = 'pending' AND candidate.available_at <= :now)
                  OR (candidate.status = 'processing' AND candidate.leased_until < :now)
                )
                {predicate}
                AND NOT EXISTS (
                  SELECT 1
                  FROM outbox_events prior_envelope
                  JOIN projection_deliveries prior
                    ON prior.outbox_event_id = prior_envelope.id
                  WHERE prior_envelope.tenant_id = envelope.tenant_id
                    AND prior_envelope.project_id = envelope.project_id
                    AND prior_envelope.projection_name = envelope.projection_name
                    AND prior_envelope.stream_position < envelope.stream_position
                    AND prior.status <> 'published'
                )
              ORDER BY candidate.available_at, candidate.stream_position, candidate.id
              FOR UPDATE OF candidate SKIP LOCKED
              LIMIT 1
            ), claimed AS (
              UPDATE projection_deliveries candidate
              SET status = 'processing', lease_token = :lease_token,
                  leased_by = :leased_by,
                  leased_until = :now + eligible.lease_seconds * interval '1 second',
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
            self._dead_letter_expired_invalid_processing(scan_filter, now)
            self._dead_letter_unregistered(scan_filter, registry_sql, params, now)
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

    def _dead_letter_unregistered(
        self,
        scan_filter: ScanFilter,
        registry_sql: str,
        params: dict[str, Any],
        now: datetime,
    ) -> None:
        filters = []
        if scan_filter.tenant_id is not None:
            filters.append("envelope.tenant_id = :tenant_id")
        if scan_filter.project_id is not None:
            filters.append("envelope.project_id = :project_id")
        if scan_filter.projector_id is not None:
            filters.append("envelope.projection_name = :projector_id")
        if scan_filter.barrier_kind is not None:
            filters.append("envelope.barrier_kind = :barrier_kind")
        if scan_filter.commit_id is not None:
            filters.append("envelope.commit_id = :commit_id")
        predicate = ""
        if filters:
            predicate = " AND " + " AND ".join(filters)
        error_message = "projector id/version is not registered"
        invalid_rows = self.session.execute(
            text(
                f"""
                WITH registered(
                  projector_id, projector_version, barrier_kind, lease_seconds
                ) AS (
                  VALUES {registry_sql}
                ), invalid AS (
                  SELECT candidate.id, candidate.outbox_event_id
                  FROM outbox_events envelope
                  JOIN projection_deliveries candidate
                    ON candidate.outbox_event_id = envelope.id
                  JOIN projection_partitions partition
                    ON partition.tenant_id = envelope.tenant_id
                   AND partition.project_id = envelope.project_id
                   AND partition.projector_id = envelope.projection_name
                  LEFT JOIN registered
                    ON registered.projector_id = envelope.projection_name
                   AND registered.projector_version = partition.projector_version
                  WHERE candidate.status = 'pending'
                    AND partition.enrollment_status = 'active'
                    AND partition.runtime_status = 'active'
                    AND (
                      registered.projector_id IS NULL
                      OR registered.barrier_kind <> envelope.barrier_kind
                      OR candidate.tenant_id <> envelope.tenant_id
                      OR candidate.project_id <> envelope.project_id
                      OR candidate.projector_id <> envelope.projection_name
                      OR candidate.projector_version <> partition.projector_version
                      OR candidate.barrier_kind <> envelope.barrier_kind
                      OR candidate.stream_position <> envelope.stream_position
                    )
                    {predicate}
                  FOR UPDATE OF candidate SKIP LOCKED
                )
                UPDATE projection_deliveries candidate
                SET status = 'dead_letter',
                    last_error_class = 'UnknownProjectorVersionError',
                    last_error_message = :unknown_error_message,
                    updated_at = :now
                FROM invalid
                WHERE candidate.id = invalid.id
                RETURNING candidate.outbox_event_id
                """
            ),
            {**params, "unknown_error_message": error_message},
        ).scalars().all()
        if invalid_rows:
            self.session.execute(
                update(OutboxEvent)
                .where(OutboxEvent.id.in_(invalid_rows))
                .values(
                    status="failed",
                    last_error=f"UnknownProjectorVersionError: {error_message}",
                    updated_at=now,
                )
            )

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
        envelope = self.session.get(OutboxEvent, candidate.outbox_event_id)
        prior = self.session.scalar(
            select(func.count())
            .select_from(ProjectionDelivery)
            .join(OutboxEvent, OutboxEvent.id == ProjectionDelivery.outbox_event_id)
            .where(
                OutboxEvent.tenant_id == envelope.tenant_id,
                OutboxEvent.project_id == envelope.project_id,
                OutboxEvent.projection_name == envelope.projection_name,
                OutboxEvent.stream_position < envelope.stream_position,
                ProjectionDelivery.status != "published",
            )
        )
        if prior:
            self.session.commit()
            return None
        partition = self.session.scalar(
            select(ProjectionPartition).where(
                ProjectionPartition.tenant_id == envelope.tenant_id,
                ProjectionPartition.project_id == envelope.project_id,
                ProjectionPartition.projector_id == envelope.projection_name,
                ProjectionPartition.projector_version == candidate.projector_version,
                ProjectionPartition.enrollment_status == "active",
                ProjectionPartition.runtime_status == "active",
                ProjectionPartition.last_published_position + 1
                == candidate.stream_position,
            )
        )
        if partition is None:
            self.session.commit()
            return None
        try:
            spec = self.registry.get(envelope.projection_name)
        except KeyError:
            self.session.commit()
            return None
        if (
            candidate.tenant_id != envelope.tenant_id
            or candidate.project_id != envelope.project_id
            or candidate.projector_id != envelope.projection_name
            or candidate.barrier_kind != envelope.barrier_kind
            or candidate.stream_position != envelope.stream_position
            or spec.version != candidate.projector_version
            or spec.barrier_kind != envelope.barrier_kind
        ):
            self.session.commit()
            return None
        policy = spec.retry
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
            (OutboxEvent.tenant_id, scan_filter.tenant_id),
            (OutboxEvent.project_id, scan_filter.project_id),
            (OutboxEvent.projection_name, scan_filter.projector_id),
            (OutboxEvent.barrier_kind, scan_filter.barrier_kind),
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
        identity = self._lock_processing_identity(claim)
        if identity is None:
            self.session.rollback()
            return False
        if not identity.agrees():
            self._dead_letter_processing_conflict(identity, now)
            self.session.commit()
            return False
        result = self.session.execute(
            update(ProjectionDelivery)
            .where(
                ProjectionDelivery.id == identity.delivery.id,
                ProjectionDelivery.status == "processing",
                ProjectionDelivery.lease_token == claim.lease_token,
            )
            .values(
                leased_until=now + timedelta(seconds=identity.spec.retry.lease_seconds),
                updated_at=now,
            )
        )
        if result.rowcount != 1:
            self.session.rollback()
            return False
        self.session.commit()
        return True

    def mark_published(self, claim: DeliveryClaim, receipt: dict[str, Any], *, now=None) -> bool:
        now = now or datetime.now(timezone.utc)
        identity = self._lock_processing_identity(claim)
        if identity is None:
            self.session.rollback()
            return False
        if not identity.agrees():
            self._dead_letter_processing_conflict(identity, now)
            self.session.commit()
            return False
        digest = sha256_json(receipt)
        result = self.session.execute(
            update(ProjectionDelivery)
            .where(
                ProjectionDelivery.id == identity.delivery.id,
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
        attempt_result = self.session.execute(
            update(ProjectionAttempt)
            .where(
                ProjectionAttempt.id == claim.attempt_id,
                ProjectionAttempt.delivery_id == identity.delivery.id,
                ProjectionAttempt.lease_token == claim.lease_token,
                ProjectionAttempt.outcome == "claimed",
            )
            .values(outcome="succeeded", finished_at=now)
        )
        if attempt_result.rowcount != 1:
            self.session.rollback()
            return False
        cursor_result = self.session.execute(
            update(ProjectionPartition)
            .where(
                ProjectionPartition.tenant_id == identity.envelope.tenant_id,
                ProjectionPartition.project_id == identity.envelope.project_id,
                ProjectionPartition.projector_id == identity.envelope.projection_name,
                ProjectionPartition.projector_version == identity.partition.projector_version,
                ProjectionPartition.last_published_position
                == identity.envelope.stream_position - 1,
            )
            .values(
                last_published_position=identity.envelope.stream_position,
                last_published_event_id=identity.envelope.id,
                updated_at=now,
            )
        )
        if cursor_result.rowcount != 1:
            self.session.rollback()
            return False
        self.session.execute(
            update(OutboxEvent).where(OutboxEvent.id == identity.envelope.id).values(
                status="published", published_at=now, last_error=None, updated_at=now
            )
        )
        self.session.commit()
        return True

    def record_failure(self, claim: DeliveryClaim, error: Exception, *, now=None) -> bool:
        now = now or datetime.now(timezone.utc)
        identity = self._lock_processing_identity(claim)
        if identity is None:
            self.session.rollback()
            return False
        if not identity.agrees():
            self._dead_letter_processing_conflict(identity, now)
            self.session.commit()
            return False
        attempt_count = identity.delivery.attempt_count
        transition = failure_transition(error, attempt_count, identity.spec.retry, now)
        attempt_result = self.session.execute(
            update(ProjectionAttempt)
            .where(
                ProjectionAttempt.id == claim.attempt_id,
                ProjectionAttempt.delivery_id == identity.delivery.id,
                ProjectionAttempt.lease_token == claim.lease_token,
                ProjectionAttempt.outcome == "claimed",
            )
            .values(
                outcome=(
                    "retry_scheduled"
                    if transition.status == "pending"
                    else "dead_lettered"
                ),
                finished_at=now,
                error_class=transition.error_class[:255],
                error_message=transition.error_message[:4000],
                retry_delay_seconds=transition.retry_delay_seconds,
            )
        )
        if attempt_result.rowcount != 1:
            self.session.rollback()
            return False
        result = self.session.execute(
            update(ProjectionDelivery)
            .where(
                ProjectionDelivery.id == identity.delivery.id,
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
            update(OutboxEvent).where(OutboxEvent.id == identity.envelope.id).values(
                status="failed", attempts=attempt_count,
                available_at=transition.available_at,
                last_error=f"{transition.error_class}: {transition.error_message}"[:4000],
                published_at=None, updated_at=now,
            )
        )
        self.session.commit()
        return True

    def _lock_processing_identity(
        self,
        claim: DeliveryClaim,
        *,
        expired_before: datetime | None = None,
    ) -> _ProcessingIdentity | None:
        predicates = [
            ProjectionDelivery.id == claim.delivery_id,
            ProjectionDelivery.status == "processing",
            ProjectionDelivery.lease_token == claim.lease_token,
        ]
        if expired_before is not None:
            predicates.append(ProjectionDelivery.leased_until < expired_before)
        delivery = self.session.scalar(
            select(ProjectionDelivery).where(*predicates).with_for_update()
        )
        if delivery is None:
            return None
        envelope = self.session.scalar(
            select(OutboxEvent)
            .where(OutboxEvent.id == delivery.outbox_event_id)
            .with_for_update()
        )
        if envelope is None:
            return None
        commit = self.session.scalar(
            select(CanonicalCommit)
            .where(CanonicalCommit.id == envelope.commit_id)
            .with_for_update()
        )
        if commit is None:
            return None
        partition = self.session.scalar(
            select(ProjectionPartition)
            .where(
                ProjectionPartition.tenant_id == envelope.tenant_id,
                ProjectionPartition.project_id == envelope.project_id,
                ProjectionPartition.projector_id == envelope.projection_name,
            )
            .with_for_update()
        )
        attempt = self.session.scalar(
            select(ProjectionAttempt)
            .where(
                ProjectionAttempt.id == claim.attempt_id,
                ProjectionAttempt.delivery_id == delivery.id,
                ProjectionAttempt.attempt_number == delivery.attempt_count,
                ProjectionAttempt.lease_token == claim.lease_token,
                ProjectionAttempt.outcome == "claimed",
            )
            .with_for_update()
        )
        if attempt is None:
            return None
        try:
            spec = self.registry.get(envelope.projection_name)
        except KeyError:
            spec = None
        return _ProcessingIdentity(delivery, envelope, commit, partition, attempt, spec)

    def _dead_letter_processing_conflict(
        self,
        identity: _ProcessingIdentity,
        now: datetime,
    ) -> None:
        message = "processing delivery identity no longer matches immutable envelope"
        attempt_result = self.session.execute(
            update(ProjectionAttempt)
            .where(
                ProjectionAttempt.id == identity.attempt.id,
                ProjectionAttempt.delivery_id == identity.delivery.id,
                ProjectionAttempt.lease_token == identity.delivery.lease_token,
                ProjectionAttempt.outcome == "claimed",
            )
            .values(
                outcome="dead_lettered",
                finished_at=now,
                error_class="ProjectionConflictError",
                error_message=message,
                retry_delay_seconds=None,
            )
        )
        if attempt_result.rowcount != 1:
            raise ProjectionConflictError("processing attempt changed during quarantine")
        delivery_result = self.session.execute(
            update(ProjectionDelivery)
            .where(
                ProjectionDelivery.id == identity.delivery.id,
                ProjectionDelivery.status == "processing",
                ProjectionDelivery.lease_token == identity.delivery.lease_token,
            )
            .values(
                status="dead_letter",
                available_at=now,
                lease_token=None,
                leased_by=None,
                leased_until=None,
                last_error_class="ProjectionConflictError",
                last_error_message=message,
                published_at=None,
                receipt_json=None,
                receipt_digest=None,
                updated_at=now,
            )
        )
        if delivery_result.rowcount != 1:
            raise ProjectionConflictError("processing delivery changed during quarantine")
        self.session.execute(
            update(OutboxEvent)
            .where(OutboxEvent.id == identity.envelope.id)
            .values(
                status="failed",
                attempts=identity.delivery.attempt_count,
                available_at=now,
                last_error=f"ProjectionConflictError: {message}",
                published_at=None,
                updated_at=now,
            )
        )

    def _dead_letter_expired_invalid_processing(
        self,
        scan_filter: ScanFilter,
        now: datetime,
    ) -> None:
        delivery_ids = self.session.scalars(
            select(ProjectionDelivery.id)
            .join(OutboxEvent, OutboxEvent.id == ProjectionDelivery.outbox_event_id)
            .where(
                ProjectionDelivery.status == "processing",
                ProjectionDelivery.leased_until < now,
                *self._orm_filters(scan_filter),
            )
            .order_by(ProjectionDelivery.id)
            .with_for_update(of=ProjectionDelivery, skip_locked=True)
        ).all()
        for delivery_id in delivery_ids:
            delivery = self.session.get(ProjectionDelivery, delivery_id)
            attempt = self.session.scalar(
                select(ProjectionAttempt).where(
                    ProjectionAttempt.delivery_id == delivery_id,
                    ProjectionAttempt.attempt_number == delivery.attempt_count,
                    ProjectionAttempt.lease_token == delivery.lease_token,
                    ProjectionAttempt.outcome == "claimed",
                )
            )
            if attempt is None:
                continue
            claim = DeliveryClaim(
                delivery_id=delivery.id,
                outbox_event_id=delivery.outbox_event_id,
                attempt_id=attempt.id,
                lease_token=delivery.lease_token,
                leased_by=delivery.leased_by,
                leased_until=delivery.leased_until,
                tenant_id=delivery.tenant_id,
                project_id=delivery.project_id,
                projector_id=delivery.projector_id,
                stream_position=delivery.stream_position,
            )
            identity = self._lock_processing_identity(claim, expired_before=now)
            if identity is not None and not identity.agrees():
                self._dead_letter_processing_conflict(identity, now)

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
