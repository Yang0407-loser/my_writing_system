"""Fail-closed verifier for Foundation P2 Golden Slice evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


REQUIRED_PROJECTIONS = {
    "legacy_world_event",
    "handover_context",
    "chroma_story_chunks",
    "redis_stream",
    "task_preview",
    "markdown_export",
    "analytics",
}


def verify_evidence(evidence: dict) -> list[str]:
    errors: list[str] = []
    if evidence.get("schema_version") != "foundation-p2-evidence-v1":
        errors.append("unsupported evidence schema")
    if evidence.get("backend") != "postgresql" or not evidence.get("gate_eligible"):
        errors.append("real PostgreSQL evidence is required")
    if evidence.get("secret_scan", {}).get("contains_secret"):
        errors.append("evidence contains a secret finding")
    hashes = evidence.get("hashes", {})
    if not hashes or len(set((hashes.get("fixture_body"), hashes.get("materialized_document"), hashes.get("revision_content")))) != 1:
        errors.append("document/revision/fixture hashes diverge")
    runtime = evidence.get("runtime", {})
    if runtime.get("phase") != "ready" or runtime.get("critical_projection_status") != "ready":
        errors.append("critical projection barrier is not ready")
    outbox = evidence.get("outbox", {})
    if set(outbox) != REQUIRED_PROJECTIONS:
        errors.append("outbox manifest is incomplete")
    elif any(row.get("status") != "published" for row in outbox.values()):
        errors.append("not every outbox row is published")
    delivery = evidence.get("delivery")
    if delivery is not None:
        if set(delivery) != REQUIRED_PROJECTIONS:
            errors.append("projection delivery manifest is incomplete")
        elif any(
            row.get("status") != "published"
            or not isinstance(row.get("stream_position"), int)
            or row.get("stream_position") < 1
            for row in delivery.values()
        ):
            errors.append("projection delivery authority is not fully published")
        cursors = evidence.get("partition_cursors", {})
        if set(cursors) != REQUIRED_PROJECTIONS or any(
            row.get("runtime_status") != "active"
            or row.get("last_published_position", 0) < 1
            for row in cursors.values()
        ):
            errors.append("projection partition cursors do not cover the commit")
    if evidence.get("counts", {}).get("ledger", 0) < 1:
        errors.append("event ledger is empty")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    evidence = json.loads(args.evidence.read_text(encoding="utf-8"))
    errors = verify_evidence(evidence)
    print(json.dumps({"passed": not errors, "errors": errors}, ensure_ascii=False))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
