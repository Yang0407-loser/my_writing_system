"""One-shot external executor for the frozen WR1-P ledger.

This file is intentionally separate from the zero-call experiment builder.  It
requires a post-freeze user authorization receipt bound to the locked manifest
and to this runner's source hash.  A started or failed sample can never be
retried by this runner.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from app.config import settings
from app.utils.llm_client import get_llm_client
from experiments.world_runtime_writer_canary import prose_canary_wr1p as wr1p


SOURCE = Path(__file__).resolve()
DEFAULT_AUTHORIZATION = (
    wr1p.DEFAULT_OUTPUT / "private/external-execution-authorization.json"
)


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def preflight(
    output_dir: Path = wr1p.DEFAULT_OUTPUT,
    authorization_path: Path = DEFAULT_AUTHORIZATION,
) -> dict[str, Any]:
    manifest_path = output_dir / "private/locked-manifest.json"
    manifest = _read(manifest_path)
    authorization = _read(authorization_path)
    audit = wr1p.audit(output_dir)
    issues = []
    expected = {
        "schema_version": "world-runtime-wr1p-external-authorization-v1",
        "authorized": True,
        "experiment_id": manifest["experiment_id"],
        "locked_manifest_sha256": _sha256(manifest_path),
        "external_runner_sha256": _sha256(SOURCE),
        "maximum_requests": 8,
        "transport_retries": 0,
        "execute_command_exactly_once": True,
        "production_writer_change_authorized": False,
        "wr2_authorized": False,
    }
    for field, value in expected.items():
        if authorization.get(field) != value:
            issues.append(f"authorization.{field}: expected {value!r}")
    if audit["status"] != "ready_zero_call_external_generation_not_authorized":
        issues.append(f"pre_generation_audit_status:{audit['status']}")
    if audit["pending"] != 8 or audit["attempt_count_total"] != 0 or audit["output_files"] != 0:
        issues.append("ledger_not_pristine")
    if manifest["sample_count"] != 8 or manifest["scene_count"] != 4:
        issues.append("manifest_sample_or_scene_count_mismatch")
    if settings.WRITER_WORLD_RUNTIME_MODE != "off":
        issues.append("production_default_not_off")
    result = {
        "schema_version": "world-runtime-wr1p-external-preflight-v1",
        "ready": not issues,
        "issues": issues,
        "provider_host": audit["provider_host"],
        "model": audit["model"],
        "sample_count": manifest["sample_count"],
        "pending": audit["pending"],
        "attempt_count_total": audit["attempt_count_total"],
        "transport_retries": 0,
        "production_default": settings.WRITER_WORLD_RUNTIME_MODE,
        "locked_manifest_sha256": _sha256(manifest_path),
        "external_runner_sha256": _sha256(SOURCE),
        "authorization_sha256": _sha256(authorization_path),
    }
    return result


def run_once(
    output_dir: Path = wr1p.DEFAULT_OUTPUT,
    authorization_path: Path = DEFAULT_AUTHORIZATION,
) -> dict[str, Any]:
    check = preflight(output_dir, authorization_path)
    if not check["ready"]:
        raise RuntimeError("wr1p_external_preflight_failed:" + "|".join(check["issues"]))
    manifest = _read(output_dir / "private/locked-manifest.json")
    ledger_path = output_dir / "attempt-ledger.json"
    ledger = _read(ledger_path)
    client = get_llm_client(settings.WRITER_LLM_MODEL)
    completed = 0
    for sample in manifest["samples"]:
        sample_id = sample["sample_id"]
        entry = ledger["samples"][sample_id]
        if entry["status"] != "pending" or entry["attempt_count"] != 0:
            raise RuntimeError(f"wr1p_refusing_nonpristine_sample:{sample_id}")
        entry.update(status="started", attempt_count=1)
        _write(ledger_path, ledger)
        metadata: dict[str, Any] = {}
        try:
            text = client.chat_completion(
                sample["messages"],
                temperature=sample["provider"]["temperature"],
                max_tokens=sample["provider"]["max_tokens"],
                max_retries=0,
                prompt_name="world_runtime_writer_prose_canary_wr1p_v1",
                completion_metadata_sink=metadata.update,
            )
            output_path = output_dir / "private/outputs" / f"{sample_id}.txt"
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(text, encoding="utf-8", newline="\n")
            entry.update(
                status="succeeded",
                output_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                completion_metadata=metadata,
            )
            completed += 1
        except Exception as exc:
            entry.update(
                status="failed",
                error_type=type(exc).__name__,
                error=str(exc)[:500],
            )
            _write(ledger_path, ledger)
            raise
        _write(ledger_path, ledger)
    evaluation = wr1p.evaluate(output_dir)
    return {
        "schema_version": "world-runtime-wr1p-external-run-result-v1",
        "command_executed_exactly_once": True,
        "succeeded": completed,
        "failed": 0,
        "attempt_count_total": sum(
            item["attempt_count"] for item in ledger["samples"].values()
        ),
        "transport_retries": 0,
        "machine_gate_passed": evaluation["machine_gate_passed"],
        "single_owner_review_required": evaluation["single_owner_review_required"],
        "decision": evaluation["decision"],
        "production_promotion_eligible": False,
        "real_task_canary_authorized": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("preflight", "run-once"))
    parser.add_argument("--output", type=Path, default=wr1p.DEFAULT_OUTPUT)
    parser.add_argument("--authorization", type=Path, default=DEFAULT_AUTHORIZATION)
    args = parser.parse_args()
    function = preflight if args.command == "preflight" else run_once
    result = function(args.output, args.authorization)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
