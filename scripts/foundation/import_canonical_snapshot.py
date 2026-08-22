from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.canonical.database import build_engine, build_session_factory
from app.canonical.snapshot import import_project_snapshot


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--input", type=Path, required=True)
    args = parser.parse_args()
    snapshot = json.loads(args.input.read_text(encoding="utf-8"))
    engine = build_engine(args.database_url)
    with build_session_factory(engine)() as session:
        import_project_snapshot(session, snapshot)
        session.commit()
    engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
