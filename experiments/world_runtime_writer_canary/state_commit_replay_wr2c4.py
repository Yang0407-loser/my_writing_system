"""WR2-C4 State Commit Canary Phase C1: shadow replay.

Replays already-generated outputs (sealed holdout + Development-v4) through
Validator -> Committer with zero provider calls, writing only to the canary
namespace.  Production state, checkpoints, databases and Writer are untouched.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app.writing.world_runtime_state_committer import WorldRuntimeStateCommitter
from experiments.world_runtime_writer_canary import adversarial_experiment as wr1r
from experiments.world_runtime_writer_canary.delta_shadow_wr2b import validate_delta_v2
from experiments.world_runtime_writer_canary.semantic_extractor_wr2c4 import (
    parse_semantic_response,
)
from experiments.world_runtime_writer_canary.state_commit_adapter_wr2c4 import (
    to_committable,
)


ROOT = Path(__file__).resolve().parents[2]
SOURCES = {
    "sealed_holdout": ROOT / ".world_runtime_wr2c4_sealed_holdout_runtime",
    "development_v4": ROOT / ".world_runtime_wr2c4_semantic_canary_runtime",
}
CANARY_RUNTIME = ROOT / ".world_runtime_state_commit_canary_runtime"
C1_DIR = CANARY_RUNTIME / "c1"
C2_DIR = CANARY_RUNTIME / "c2"
REPORT_JSON = C1_DIR / "replay-report.json"
C2_REPORT_JSON = C2_DIR / "replay-report.json"
REPORT_MD = ROOT / "reports/world-runtime-state-commit-canary-c1-2026-08-05.md"


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def replay_sample(
    *,
    source: str,
    sample: dict[str, Any],
    output_path: Path,
    committer: WorldRuntimeStateCommitter,
    states,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "source": source,
        "sample_id": sample["sample_id"],
        "status": "pending",
    }
    try:
        response_text = output_path.read_text(encoding="utf-8")
        artifact = parse_semantic_response(
            text=sample["text"],
            response_text=response_text,
            sample_id=sample["sample_id"],
            scene_id=sample["scene_id"],
            state_variant=sample["state_variant"],
            base_revision=sample["base_revision"],
        )
        validation = validate_delta_v2(artifact.delta)
    except Exception as exc:
        record.update(status="parse_or_validate_failed", error=f"{type(exc).__name__}: {str(exc)[:300]}")
        return record
    record["accepted_count"] = len(validation.accepted_change_ids)
    record["rejected_count"] = len(validation.rejected_change_ids)
    record["unresolved_count"] = len(validation.unresolved_change_ids)
    if not validation.accepted_change_ids:
        record.update(status="no_commit_no_accepted")
        return record
    state = states[sample["state_variant"]]
    committable_delta, committable_validation = to_committable(
        artifact.delta,
        validation,
        project_id=state.project_id,
    )
    idempotency_key = f"c1:{source}:{sample['sample_id']}"
    try:
        result = committer.commit(
            idempotency_key=idempotency_key,
            before=state,
            delta=committable_delta,
            validation=committable_validation,
            final_text_hash=artifact.output_hash,
            task_id=f"c1-{source}",
            section=1,
            subsection=1,
        )
        duplicate = committer.commit(
            idempotency_key=idempotency_key,
            before=state,
            delta=committable_delta,
            validation=committable_validation,
            final_text_hash=artifact.output_hash,
            task_id=f"c1-{source}",
            section=1,
            subsection=1,
        )
    except Exception as exc:
        record.update(
            status="blocked",
            blocked_reason=f"{type(exc).__name__}: {str(exc)[:300]}",
        )
        return record
    record.update(
        status="committed",
        base_revision=state.revision,
        after_revision=result.after.revision,
        ledger_entries=len(result.ledger.entries),
        after_facts=len(result.after.facts),
        commit_id=result.commit_id,
        artifact_hash=result.artifact_hash,
        idempotent_replay=duplicate.skipped_as_duplicate,
    )
    return record


def replay_manifest(
    *,
    source: str,
    runtime_dir: Path,
    committer: WorldRuntimeStateCommitter,
    states,
) -> list[dict[str, Any]]:
    manifest = _read_json(runtime_dir / "private/locked-manifest.json")
    outputs_dir = runtime_dir / "private/outputs"
    records = []
    for sample in manifest["samples"]:
        output_path = outputs_dir / f"{sample['sample_id']}.json"
        records.append(
            replay_sample(
                source=source,
                sample=sample,
                output_path=output_path,
                committer=committer,
                states=states,
            )
        )
    return records


def run_replay(phase: str = "c1") -> dict[str, Any]:
    _, states, _ = wr1r._artifacts()
    committer = WorldRuntimeStateCommitter()
    records: list[dict[str, Any]] = []
    for source, runtime_dir in SOURCES.items():
        if not (runtime_dir / "private/locked-manifest.json").exists():
            continue
        records.extend(
            replay_manifest(
                source=source,
                runtime_dir=runtime_dir,
                committer=committer,
                states=states,
            )
        )
    summary = {
        "total": len(records),
        "committed": sum(r["status"] == "committed" for r in records),
        "no_commit_no_accepted": sum(r["status"] == "no_commit_no_accepted" for r in records),
        "blocked": sum(r["status"] == "blocked" for r in records),
        "parse_or_validate_failed": sum(r["status"] == "parse_or_validate_failed" for r in records),
        "blocked_reasons": sorted({r.get("blocked_reason", "") for r in records if r["status"] == "blocked"}),
        "idempotent_replays": sum(r.get("idempotent_replay", False) for r in records),
    }
    report = {
        "schema_version": "world-runtime-state-commit-canary-c1-v1",
        "partition_role": "shadow_replay_no_provider_calls",
        "sources": list(SOURCES.keys()),
        "summary": summary,
        "records": records,
    }
    _write_json(C2_REPORT_JSON if phase == "c2" else REPORT_JSON, report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="WR2-C4 State Commit C1 shadow replay")
    parser.add_argument("--run", action="store_true", help="execute the C1 replay")
    parser.add_argument("--phase", choices=("c1", "c2"), default="c1")
    args = parser.parse_args()
    if not args.run:
        parser.error("--run is required; C1 replay is a one-way offline report generator")
    report = run_replay(phase=args.phase)
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
