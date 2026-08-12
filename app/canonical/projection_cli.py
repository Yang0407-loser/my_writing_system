"""Operational CLI for the PostgreSQL-authoritative projection runtime."""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict
from uuid import uuid4

from ..config import settings
from ..projection_tasks import wakeup_failure_count
from ..projections.factory import build_projection_adapters
from .database import build_engine, build_session_factory
from .projection_delivery import ProjectionDeliveryStore, ScanFilter
from .projection_health import projection_health_snapshot
from .projection_ports import ProjectionScope
from .projection_rebuild import ProjectionRebuildService
from .projection_worker import ProjectionWorker, ScanSummary


def _runtime(worker_id: str | None = None):
    engine = build_engine(settings.CANONICAL_DATABASE_URL)
    session_factory = build_session_factory(engine)
    adapters = build_projection_adapters(
        session_factory,
        markdown_root=settings.PROJECTION_MARKDOWN_ROOT,
    )
    worker = ProjectionWorker(
        session_factory,
        adapters,
        worker_id=worker_id or f"projection-cli:{uuid4().hex}",
    )
    return engine, session_factory, adapters, worker


def _scan_filter(args, *, limit: int | None = None) -> ScanFilter:
    return ScanFilter(
        tenant_id=getattr(args, "tenant_id", None),
        project_id=getattr(args, "project_id", None),
        projector_id=getattr(args, "projector_id", None),
        barrier_kind=getattr(args, "barrier_kind", None),
        limit=limit or args.limit,
    )


def _summary_json(summary) -> dict[str, int]:
    return asdict(summary)


def _scan(args) -> int:
    engine, _, _, worker = _runtime()
    try:
        while True:
            summary = worker.scan_once(_scan_filter(args))
            print(json.dumps(_summary_json(summary), sort_keys=True))
            if not args.continuous:
                return 0
            time.sleep(settings.PROJECTION_SCAN_INTERVAL_MS / 1000)
    finally:
        engine.dispose()


def _drain(args) -> int:
    engine, _, _, worker = _runtime()
    aggregate = {name: 0 for name in _summary_json(ScanSummary())}
    try:
        while aggregate["claimed"] < args.max_events:
            remaining = args.max_events - aggregate["claimed"]
            summary = worker.scan_once(
                _scan_filter(
                    args,
                    limit=min(settings.PROJECTION_SCAN_BATCH_SIZE, remaining),
                )
            )
            values = _summary_json(summary)
            for name, value in values.items():
                aggregate[name] += value
            if summary.claimed == 0:
                break
        print(json.dumps(aggregate, sort_keys=True))
        return 0
    finally:
        engine.dispose()


def _status(args) -> int:
    engine = build_engine(settings.CANONICAL_DATABASE_URL)
    session_factory = build_session_factory(engine)
    try:
        with session_factory() as session:
            snapshot = projection_health_snapshot(
                session,
                _scan_filter(args),
                wakeup_failures=wakeup_failure_count(),
            )
        print(snapshot.model_dump_json())
        return 0
    finally:
        engine.dispose()


def _rebuild(args, *, bootstrap: bool) -> int:
    engine, session_factory, adapters, _ = _runtime()
    try:
        service = ProjectionRebuildService(session_factory, adapters)
        scope = ProjectionScope(args.tenant_id, args.project_id)
        starter = service.start_bootstrap if bootstrap else service.start_maintenance
        run_id = starter(
            scope,
            args.projector_id,
            operator_id=args.operator_id,
            reason=args.reason,
        )
        status = service.resume(run_id, worker_id=f"projection-cli:{uuid4().hex}")
        print(status.model_dump_json())
        return 0 if status.status != "reconciliation_failed" else 2
    finally:
        engine.dispose()


def _requeue(args) -> int:
    engine = build_engine(settings.CANONICAL_DATABASE_URL)
    session_factory = build_session_factory(engine)
    try:
        with session_factory() as session:
            changed = ProjectionDeliveryStore(session).requeue_dead_letter(
                args.delivery_id,
                args.operator_id,
                args.reason,
            )
        print(json.dumps({"requeued": changed}, sort_keys=True))
        return 0 if changed else 2
    finally:
        engine.dispose()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="projection-cli")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_scope(command, *, require_project=False, require_projector=False):
        command.add_argument("--tenant-id", required=require_project)
        command.add_argument("--project-id", required=require_project)
        command.add_argument("--projector-id", required=require_projector)

    scan = subparsers.add_parser("scan")
    add_scope(scan)
    scan.add_argument("--barrier-kind", choices=("critical", "non_blocking"))
    scan.add_argument("--limit", type=int, default=settings.PROJECTION_SCAN_BATCH_SIZE)
    scan.add_argument("--continuous", action="store_true")
    scan.set_defaults(handler=_scan)

    drain = subparsers.add_parser("drain")
    add_scope(drain)
    drain.add_argument("--barrier-kind", choices=("critical", "non_blocking"))
    drain.add_argument("--limit", type=int, default=settings.PROJECTION_SCAN_BATCH_SIZE)
    drain.add_argument("--max-events", type=int, default=1000)
    drain.set_defaults(handler=_drain)

    status = subparsers.add_parser("status")
    add_scope(status)
    status.add_argument("--barrier-kind", choices=("critical", "non_blocking"))
    status.add_argument("--limit", type=int, default=1)
    status.set_defaults(handler=_status)

    for name, bootstrap in (("rebuild", False), ("bootstrap", True)):
        command = subparsers.add_parser(name)
        add_scope(command, require_project=True, require_projector=True)
        command.add_argument("--operator-id", required=True)
        command.add_argument("--reason", required=True)
        command.set_defaults(handler=lambda args, value=bootstrap: _rebuild(args, bootstrap=value))

    requeue = subparsers.add_parser("requeue")
    requeue.add_argument("--delivery-id", required=True)
    requeue.add_argument("--operator-id", required=True)
    requeue.add_argument("--reason", required=True)
    requeue.set_defaults(handler=_requeue)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if hasattr(args, "limit") and args.limit < 1:
        raise SystemExit("--limit must be positive")
    if hasattr(args, "max_events") and args.max_events < 1:
        raise SystemExit("--max-events must be positive")
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
