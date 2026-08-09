# -*- coding: utf-8 -*-
"""WR4 full-book WR commit canary (real LLM, chained, run-once).

Builds a real WR commit chain for every subsection of the frozen book
instance 20f02dc7: canonical subsection text (longest chunk row) -> frozen
semantic extractor (wr2c513r9) -> validator (wr2c6) -> adapter (wr2c7) ->
WorldRuntimeStateCommitter, chained on the committed after-state.  One LLM
judgment call per subsection, zero retries.  Isolated canary namespace only;
production off.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from app.config import settings
from app.utils.llm_client import get_llm_client
from app.writing.world_runtime_contracts import CanonicalWorldState
from app.writing.world_runtime_state_committer import WorldRuntimeStateCommitter
from app.writing.wr_rag_metadata_wiring import flat_rag_metadata
from experiments.world_runtime_writer_canary.delta_shadow_wr2c6 import validate_delta_v6
from experiments.world_runtime_writer_canary.semantic_extractor_wr2c513r9 import (
    build_messages,
    parse_semantic_response,
)
from experiments.world_runtime_writer_canary.state_commit_adapter_wr2c7 import to_committable


ROOT = Path(__file__).resolve().parents[2]
SOURCE = Path(__file__).resolve()
EXTRACTOR = ROOT / "experiments/world_runtime_writer_canary/semantic_extractor_wr2c513r9.py"
PROJECTOR = ROOT / "experiments/world_runtime_writer_canary/semantic_projector_wr2c513r6.py"
VALIDATOR = ROOT / "experiments/world_runtime_writer_canary/delta_shadow_wr2c6.py"
COMMITTER = ROOT / "app/writing/world_runtime_state_committer.py"
ADAPTER = ROOT / "experiments/world_runtime_writer_canary/state_commit_adapter_wr2c7.py"
PROJECTION = ROOT / "app/writing/world_runtime_metadata_projection.py"
SNAPSHOT = ROOT / "experiments/world_runtime_writer_canary/fixtures/wr4_metadata_holdout_corpus_snapshot_v1.json"
RUNTIME = ROOT / ".world_runtime_wr4_fullbook_wr_commit_canary_runtime"
DEFAULT_AUTHORIZATION = RUNTIME / "private/external-execution-authorization.json"
TASK_ID = "20f02dc7-dc64-4233-bd6c-06a6d8647dbe"
PROJECT_ID = "20f02dc7-saturday-bakery"
EXPERIMENT_ID = "world-runtime-wr4-fullbook-wr-commit-canary-v1"
JUDGMENT_PROVIDER = {"temperature": 0.0, "max_tokens": 4000, "transport_retries": 0, "json_mode": True}


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _digest(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _source_hashes() -> dict[str, str]:
    return {
        "runner_sha256": _sha256_file(SOURCE),
        "extractor_sha256": _sha256_file(EXTRACTOR),
        "projector_sha256": _sha256_file(PROJECTOR),
        "validator_sha256": _sha256_file(VALIDATOR),
        "committer_sha256": _sha256_file(COMMITTER),
        "adapter_sha256": _sha256_file(ADAPTER),
        "projection_sha256": _sha256_file(PROJECTION),
    }


def _assert_component_binding() -> None:
    if SOURCE.name != "wr4_fullbook_wr_commit_canary.py":
        raise RuntimeError(f"canary runner bound to wrong source: {SOURCE.name}")
    if EXTRACTOR.name != "semantic_extractor_wr2c513r9.py":
        raise RuntimeError(f"canary runner bound to wrong extractor: {EXTRACTOR.name}")
    if VALIDATOR.name != "delta_shadow_wr2c6.py":
        raise RuntimeError(f"canary runner bound to wrong validator: {VALIDATOR.name}")
    if ADAPTER.name != "state_commit_adapter_wr2c7.py":
        raise RuntimeError(f"canary runner bound to wrong adapter: {ADAPTER.name}")


def _subsection_keys() -> list[tuple[int, int]]:
    snapshot = _read_json(SNAPSHOT)
    rows = snapshot["tasks"][TASK_ID]["rows"]
    keys = sorted({(int(r["section"]), int(r["subsection"])) for r in rows})
    return keys


def _canonical_text(section: int, subsection: int) -> str:
    snapshot = _read_json(SNAPSHOT)
    rows = snapshot["tasks"][TASK_ID]["rows"]
    candidates = [
        r["text"]
        for r in rows
        if int(r["section"]) == section and int(r["subsection"]) == subsection
    ]
    if not candidates:
        raise ValueError(f"no rows for S{section}.{subsection}")
    return max(candidates, key=len)


def bootstrap_state() -> CanonicalWorldState:
    source = _read_json(
        ROOT / ".world_runtime_state_commit_canary_runtime/c21r10/private/commits/S1.json"
    )
    adapt = {
        ("bakery:wild-bread", "opens_at"): "07:00",
        ("world_clock", "time"): "03:30",
    }
    facts = [
        {**fact, "value": adapt.get((fact["subject"], fact["predicate"]), fact["value"]), "revision": 0}
        for fact in source["before"]["facts"]
    ]
    return CanonicalWorldState.model_validate(
        {"project_id": PROJECT_ID, "revision": 0, "facts": facts}
    )


def build(output_dir: Path = RUNTIME) -> dict[str, Any]:
    if (output_dir / "attempt-ledger.json").exists():
        raise FileExistsError("fullbook ledger exists; refusing rebuild")
    _assert_component_binding()
    keys = _subsection_keys()
    samples = []
    for section, subsection in keys:
        text = _canonical_text(section, subsection)
        messages = build_messages(text=text, state=bootstrap_state())
        samples.append({
            "section": section,
            "subsection": subsection,
            "request_hash": _digest({"messages": messages, "provider": JUDGMENT_PROVIDER}),
            "status": "pending",
            "attempt_count": 0,
        })
    manifest = {
        "schema_version": "world-runtime-wr4-fullbook-canary-manifest-v1",
        "experiment_id": EXPERIMENT_ID,
        "partition_role": "fullbook_real_wr_commit_chain",
        "project_id": PROJECT_ID,
        "task_id": TASK_ID,
        "initial_revision": 0,
        "subsection_count": len(keys),
        "judgment_provider": JUDGMENT_PROVIDER,
        "model_calls_per_subsection": 1,
        "provider_host": "api.deepseek.com",
        "model": settings.WRITER_LLM_MODEL,
        "source_hashes": _source_hashes(),
        "samples": samples,
        "production_behavior_changed": False,
        "state_commit_authorized": True,
    }
    ledger = {
        "schema_version": "world-runtime-wr4-fullbook-canary-ledger-v1",
        "samples": {
            f"{s['section']}_{s['subsection']}": {
                "request_hash": s["request_hash"],
                "status": "pending",
                "attempt_count": 0,
            }
            for s in samples
        },
    }
    _write_json(output_dir / "private/locked-manifest.json", manifest)
    _write_json(output_dir / "attempt-ledger.json", ledger)
    return manifest


def audit(output_dir: Path = RUNTIME) -> dict[str, Any]:
    issues = []
    manifest_path = output_dir / "private/locked-manifest.json"
    ledger_path = output_dir / "attempt-ledger.json"
    if not manifest_path.exists() or not ledger_path.exists():
        issues.append("manifest_or_ledger_missing")
    manifest = _read_json(manifest_path) if manifest_path.exists() else {}
    ledger = _read_json(ledger_path) if ledger_path.exists() else {}
    for field, expected in manifest.get("source_hashes", {}).items():
        current = _source_hashes().get(field)
        if current != expected:
            issues.append(f"source_hash_mismatch:{field}")
    expected_count = len(_subsection_keys())
    pending = sum(item["status"] == "pending" for item in ledger.get("samples", {}).values())
    attempts = sum(item["attempt_count"] for item in ledger.get("samples", {}).values())
    outputs = list((output_dir / "private/outputs").glob("*")) if (output_dir / "private/outputs").exists() else []
    commits = list((output_dir / "private/commits").glob("*.json")) if (output_dir / "private/commits").exists() else []
    if pending != expected_count or attempts != 0 or outputs or commits:
        issues.append("ledger_or_outputs_not_pristine")
    if settings.WRITER_WORLD_RUNTIME_MODE != "off":
        issues.append("production_default_not_off")
    return {
        "schema_version": "world-runtime-wr4-fullbook-canary-preflight-v1",
        "ready": not issues,
        "issues": issues,
        "pending": pending,
        "attempt_count_total": attempts,
        "output_files": len(outputs),
        "commit_files": len(commits),
        "production_default": settings.WRITER_WORLD_RUNTIME_MODE,
    }


def preflight(
    output_dir: Path = RUNTIME,
    authorization_path: Path = DEFAULT_AUTHORIZATION,
) -> dict[str, Any]:
    _assert_component_binding()
    check = audit(output_dir)
    issues = list(check["issues"])
    manifest = _read_json(output_dir / "private/locked-manifest.json")
    if not authorization_path.exists():
        issues.append("authorization_missing")
        authorization = {}
    else:
        authorization = _read_json(authorization_path)
    expected = {
        "schema_version": "world-runtime-wr4-fullbook-external-authorization-v1",
        "authorized": True,
        "experiment_id": manifest["experiment_id"],
        "locked_manifest_sha256": _sha256_file(output_dir / "private/locked-manifest.json"),
        "runner_source_sha256": manifest["source_hashes"]["runner_sha256"],
        "maximum_requests": manifest["subsection_count"],
        "transport_retries": 0,
        "execute_command_exactly_once": True,
        "production_writer_change_authorized": False,
        "state_commit_authorized": True,
    }
    for field, value in expected.items():
        if authorization.get(field) != value:
            issues.append(f"authorization.{field}:expected:{value!r}")
    return {
        "schema_version": "world-runtime-wr4-fullbook-canary-external-preflight-v1",
        "ready": not issues,
        "issues": issues,
        "model_calls": manifest["subsection_count"],
        "production_default": settings.WRITER_WORLD_RUNTIME_MODE,
        "authorization_sha256": _sha256_file(authorization_path) if authorization_path.exists() else None,
        "state_commit_authorized": True,
    }


def run_once(
    output_dir: Path = RUNTIME,
    authorization_path: Path = DEFAULT_AUTHORIZATION,
) -> dict[str, Any]:
    check = preflight(output_dir, authorization_path)
    if not check["ready"]:
        raise RuntimeError("wr4_fullbook_preflight_failed:" + "|".join(check["issues"]))
    manifest = _read_json(output_dir / "private/locked-manifest.json")
    ledger_path = output_dir / "attempt-ledger.json"
    ledger = _read_json(ledger_path)
    client = get_llm_client(manifest["model"])
    committer = WorldRuntimeStateCommitter()
    current_state = bootstrap_state()
    records: list[dict[str, Any]] = []
    commits_dir = output_dir / "private/commits"
    for sample in manifest["samples"]:
        section, subsection = sample["section"], sample["subsection"]
        key = f"{section}_{subsection}"
        entry = ledger["samples"][key]
        if entry["status"] != "pending" or entry["attempt_count"] != 0:
            raise RuntimeError(f"wr4_fullbook_refusing_nonpristine:{key}")
        entry.update(status="started", attempt_count=1)
        _write_json(ledger_path, ledger)
        record: dict[str, Any] = {"section": section, "subsection": subsection, "status": "pending"}
        try:
            text = _canonical_text(section, subsection)
            text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
            record.update(chars=len(text), output_hash=text_hash)
            messages = build_messages(text=text, state=current_state)
            judgment = client.chat_completion(
                messages,
                temperature=JUDGMENT_PROVIDER["temperature"],
                max_tokens=JUDGMENT_PROVIDER["max_tokens"],
                max_retries=0,
                json_mode=JUDGMENT_PROVIDER["json_mode"],
                prompt_name="wr4_fullbook_wr_commit_v1",
                completion_metadata_sink=(lambda md, _r=record: _r.update(judgment_metadata=md)),
            )
            artifact = parse_semantic_response(
                text=text,
                response_text=judgment,
                sample_id=f"WR4-S{section}-{subsection}",
                scene_id="20f02dc7-fullbook",
                state_variant="before",
                state=current_state,
                base_revision=current_state.revision,
            )
            validation = validate_delta_v6(artifact.delta, state=current_state)
            record.update(
                extracted=len(artifact.delta.changes),
                accepted=len(validation.accepted_change_ids),
                rejected=len(validation.rejected_change_ids),
                unresolved=len(validation.unresolved_change_ids),
            )
            if not validation.accepted_change_ids:
                record.update(status="no_commit_no_accepted")
            else:
                delta, committable_validation = to_committable(
                    artifact.delta,
                    validation,
                    project_id=PROJECT_ID,
                    base_state=bootstrap_state(),
                    before_state=current_state,
                )
                record.update(before_revision=current_state.revision)
                committed = committer.commit(
                    idempotency_key=f"wr4-fullbook:S{section}_{subsection}",
                    before=current_state,
                    delta=delta,
                    validation=committable_validation,
                    final_text_hash=text_hash,
                    task_id=TASK_ID,
                    section=section,
                    subsection=subsection,
                )
                record.update(
                    status="committed",
                    after_revision=committed.after.revision,
                    ledger_entries=len(committed.ledger.entries),
                    commit_id=committed.commit_id,
                    rag_metadata=flat_rag_metadata(committed, section=section, subsection=subsection),
                )
                _write_json(
                    commits_dir / f"S{section}_{subsection}.json",
                    committed.model_dump(mode="json"),
                )
                current_state = committed.after
            entry.update(status="succeeded")
        except Exception as exc:
            entry.update(status="failed", error=f"{type(exc).__name__}: {str(exc)[:500]}")
            _write_json(ledger_path, ledger)
            raise
        _write_json(ledger_path, ledger)
        records.append(record)
    summary = {
        "subsections": len(records),
        "model_calls": len(records),
        "committed": sum(r["status"] == "committed" for r in records),
        "no_commit_no_accepted": sum(r["status"] == "no_commit_no_accepted" for r in records),
        "extracted_changes": sum(r.get("extracted", 0) for r in records),
        "accepted_changes": sum(r.get("accepted", 0) for r in records),
        "final_revision": current_state.revision,
        "final_fact_count": len(current_state.facts),
    }
    report = {
        "schema_version": "world-runtime-wr4-fullbook-canary-report-v1",
        "partition_role": "fullbook_real_wr_commit_chain",
        "summary": summary,
        "records": records,
        "human_review_pending": True,
    }
    _write_json(output_dir / "report.json", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="WR4 full-book WR commit canary")
    parser.add_argument("command", choices=("build", "make-authorization", "preflight", "run-once"))
    parser.add_argument("--output", type=Path, default=RUNTIME)
    parser.add_argument("--authorization", type=Path, default=DEFAULT_AUTHORIZATION)
    args = parser.parse_args()
    if args.command == "build":
        result = build(args.output)
    elif args.command == "make-authorization":
        manifest = _read_json(args.output / "private/locked-manifest.json")
        authorization = {
            "schema_version": "world-runtime-wr4-fullbook-external-authorization-v1",
            "authorized": True,
            "authorized_by": "user conversation 2026-08-08",
            "experiment_id": manifest["experiment_id"],
            "locked_manifest_sha256": _sha256_file(args.output / "private/locked-manifest.json"),
            "runner_source_sha256": manifest["source_hashes"]["runner_sha256"],
            "maximum_requests": manifest["subsection_count"],
            "transport_retries": 0,
            "execute_command_exactly_once": True,
            "production_writer_change_authorized": False,
            "state_commit_authorized": True,
        }
        _write_json(args.authorization, authorization)
        result = authorization
    elif args.command == "preflight":
        result = preflight(args.output, args.authorization)
    else:
        result = run_once(args.output, args.authorization)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
