from __future__ import annotations

import argparse
from pathlib import Path

from app.canonical.database import build_engine, build_session_factory
from app.canonical.snapshot import canonical_snapshot_bytes, export_project_snapshot


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    engine = build_engine(args.database_url)
    with build_session_factory(engine)() as session:
        snapshot = export_project_snapshot(
            session, tenant_id=args.tenant_id, project_id=args.project_id
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical_snapshot_bytes(snapshot))
    engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
