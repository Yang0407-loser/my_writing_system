"""One-shot external executor for the frozen WR2-C3 Development-v3 canary."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from app.config import settings
from app.utils.llm_client import get_llm_client
from experiments.world_runtime_writer_canary import semantic_canary_wr2c3 as wr2c3


SOURCE = Path(__file__).resolve()
DEFAULT_AUTHORIZATION = wr2c3.DEFAULT_OUTPUT / "private/external-execution-authorization.json"


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def preflight(
    output_dir: Path = wr2c3.DEFAULT_OUTPUT,
    authorization_path: Path = DEFAULT_AUTHORIZATION,
) -> dict[str, Any]:
    audit = wr2c3.audit(output_dir)
    manifest_path = output_dir / "private/locked-manifest.json"
    manifest = _read(manifest_path)
    issues = []
    if not authorization_path.exists():
        issues.append("authorization_missing")
        authorization = {}
    else:
        authorization = _read(authorization_path)
    expected = {
        "schema_version": "world-runtime-semantic-canary-wr2c3-external-authorization-v1",
        "authorized": True,
        "experiment_id": manifest["experiment_id"],
        "locked_manifest_sha256": _sha256(manifest_path),
        "external_runner_sha256": _sha256(SOURCE),
        "semantic_extractor_source_sha256": manifest["semantic_extractor_source_sha256"],
        "projector_source_sha256": manifest["projector_source_sha256"],
        "maximum_requests": manifest["sample_count"],
        "transport_retries": 0,
        "execute_command_exactly_once": True,
        "prior_development_partition_reuse_authorized": False,
        "sealed_holdout_use_authorized": False,
        "production_writer_change_authorized": False,
        "state_commit_authorized": False,
    }
    for field, value in expected.items():
        if authorization.get(field) != value:
            issues.append(f"authorization.{field}:expected:{value!r}")
    if audit["status"] != "ready_zero_call_external_execution_not_authorized":
        issues.append(f"audit_status:{audit['status']}")
    if audit["pending"] != manifest["sample_count"] or audit["attempt_count_total"] != 0 or audit["output_files"] != 0:
        issues.append("ledger_not_pristine")
    if settings.WRITER_WORLD_RUNTIME_MODE != "off":
        issues.append("production_default_not_off")
    return {
        "schema_version": "world-runtime-semantic-canary-wr2c3-external-preflight-v1",
        "ready": not issues,
        "issues": issues,
        "provider_host": manifest["provider_host"],
        "model": manifest["model"],
        "sample_count": manifest["sample_count"],
        "pending": audit["pending"],
        "attempt_count_total": audit["attempt_count_total"],
        "transport_retries": 0,
        "production_default": settings.WRITER_WORLD_RUNTIME_MODE,
        "locked_manifest_sha256": _sha256(manifest_path),
        "external_runner_sha256": _sha256(SOURCE),
        "authorization_sha256": _sha256(authorization_path) if authorization_path.exists() else None,
        "prior_development_partition_reused": False,
        "sealed_holdout_used": False,
        "state_commit_authorized": False,
    }


def run_once(
    output_dir: Path = wr2c3.DEFAULT_OUTPUT,
    authorization_path: Path = DEFAULT_AUTHORIZATION,
) -> dict[str, Any]:
    check = preflight(output_dir, authorization_path)
    if not check["ready"]:
        raise RuntimeError("wr2c3_external_preflight_failed:" + "|".join(check["issues"]))
    manifest = _read(output_dir / "private/locked-manifest.json")
    ledger_path = output_dir / "attempt-ledger.json"
    ledger = _read(ledger_path)
    client = get_llm_client(manifest["model"])
    completed = 0
    for sample in manifest["samples"]:
        entry = ledger["samples"][sample["sample_id"]]
        if entry["status"] != "pending" or entry["attempt_count"] != 0:
            raise RuntimeError(f"wr2c3_refusing_nonpristine_sample:{sample['sample_id']}")
        entry.update(status="started", attempt_count=1)
        _write(ledger_path, ledger)
        metadata: dict[str, Any] = {}
        try:
            response = client.chat_completion(
                sample["messages"],
                temperature=sample["provider"]["temperature"],
                max_tokens=sample["provider"]["max_tokens"],
                max_retries=0,
                json_mode=sample["provider"]["json_mode"],
                prompt_name="world_runtime_semantic_judgment_wr2c3_v1",
                completion_metadata_sink=metadata.update,
            )
            output_path = output_dir / "private/outputs" / f"{sample['sample_id']}.json"
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(response, encoding="utf-8", newline="\n")
            entry.update(
                status="succeeded",
                output_sha256=hashlib.sha256(response.encode("utf-8")).hexdigest(),
                completion_metadata=metadata,
            )
            completed += 1
        except Exception as exc:
            entry.update(status="failed", error_type=type(exc).__name__, error=str(exc)[:500])
            _write(ledger_path, ledger)
            raise
        _write(ledger_path, ledger)
    evaluation = wr2c3.evaluate(output_dir)
    return {
        "schema_version": "world-runtime-semantic-canary-wr2c3-external-result-v1",
        "command_executed_exactly_once": True,
        "succeeded": completed,
        "failed": 0,
        "attempt_count_total": sum(item["attempt_count"] for item in ledger["samples"].values()),
        "transport_retries": 0,
        "development_gate_passed": evaluation["development_gate_passed"],
        "decision": evaluation["decision"],
        "production_promotion_eligible": False,
        "new_unseen_holdout_authorized": evaluation["new_unseen_holdout_authorized"],
        "state_commit_authorized": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("preflight", "run-once"))
    parser.add_argument("--output", type=Path, default=wr2c3.DEFAULT_OUTPUT)
    parser.add_argument("--authorization", type=Path, default=DEFAULT_AUTHORIZATION)
    args = parser.parse_args()
    result = (preflight if args.command == "preflight" else run_once)(args.output, args.authorization)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
