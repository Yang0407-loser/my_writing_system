from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from typing import Any

from experiments.writer_boundary_v12_r3.kernel import digest_bytes, digest_json

from .adapter import (
    DisabledDeepSeekAdapter,
    assert_no_api_key_material,
    execution_payload,
    provider_call_spec,
)
from .models import GenerationGate, GenerationQueueItem


ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "experiments/writer_boundary_v12_r34/fixtures/v1_2_r34_generation_package.json"
R331_APPROVAL = ROOT / "outputs/writer-boundary-v1-2-r3-3-1/review/r3-3-1-targeted-review-aggregate.json"
R3_REQUESTS = ROOT / "outputs/writer-boundary-v1-2-r3/requests/locked-requests.synthetic.json"
LLM_CLIENT_SOURCE = ROOT / "app/utils/llm_client.py"
DEFAULT_OUTPUT = ROOT / "outputs/writer-boundary-v1-2-r3-4-generation-package"
DEFAULT_REPORT = ROOT / "reports/writer-boundary-v1-2-r3-4-generation-package-disabled-2026-07-30.md"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    )


def validate_inputs() -> tuple[dict[str, Any], dict[str, Any]]:
    config = load_json(CONFIG_PATH)
    for path, expected in (
        (R331_APPROVAL, config["r331_approval_sha256"]),
        (R3_REQUESTS, config["r3_locked_request_corpus_sha256"]),
        (LLM_CLIENT_SOURCE, config["llm_client_source_sha256"]),
    ):
        if digest_bytes(path.read_bytes()) != expected:
            raise ValueError(f"pinned generation-package input drift: {path.name}")
    approval = load_json(R331_APPROVAL)
    if (
        approval["aggregate_verdict"] != "authorize_generation_package_build_only"
        or approval["authorization"]["generation_package_build_authorized"] is not True
        or approval["authorization"]["real_generation_authorized"] is not False
        or approval["authorization"]["model_call_authorized"] is not False
    ):
        raise ValueError("R3.3.1 approval does not authorize this build-only package")
    return config, load_json(R3_REQUESTS)


def build_queue(requests: dict[str, Any], gate: GenerationGate) -> list[GenerationQueueItem]:
    gate_hash = digest_json(gate.model_dump(mode="json"))
    call_spec = provider_call_spec()
    ordered = sorted(
        requests.values(),
        key=lambda item: (
            item["envelope"]["block_id"],
            {"A": 0, "B": 1, "C": 2}[item["envelope"]["arm"]],
        ),
    )
    queue = []
    for ordinal, source in enumerate(ordered, 1):
        envelope = source["envelope"]
        if digest_json(envelope) != source["sha256"]:
            raise ValueError("locked source request hash mismatch")
        generation_id = f"GEN-{ordinal:02d}"
        payload = {
            "generation_id": generation_id,
            "messages": envelope["messages"],
            "call_spec": call_spec.model_dump(mode="json"),
            "gate_sha256": gate_hash,
        }
        queue.append(
            GenerationQueueItem(
                schema_version="1.2-r3.4-queue-item",
                generation_id=generation_id,
                ordinal=ordinal,
                block_id=envelope["block_id"],
                scene_id=envelope["scene_id"],
                arm=envelope["arm"],
                text_id=envelope["text_id"],
                source_request_sha256=source["sha256"],
                execution_envelope_sha256=digest_json(payload),
                messages=envelope["messages"],
                call_spec=call_spec,
                gate_sha256=gate_hash,
            )
        )
    if len(queue) != 36 or len({item.generation_id for item in queue}) != 36:
        raise ValueError("generation queue must contain exactly 36 unique items")
    return queue


def create_retry_ledger(path: Path, queue: list[GenerationQueueItem]) -> None:
    with closing(sqlite3.connect(path)) as db, db:
        db.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE queue(
                generation_id TEXT PRIMARY KEY,
                ordinal INTEGER UNIQUE NOT NULL,
                envelope_sha256 TEXT UNIQUE NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('pending_disabled')),
                attempt_count INTEGER NOT NULL CHECK(attempt_count=0)
            );
            CREATE TABLE attempts(
                attempt_id INTEGER PRIMARY KEY,
                generation_id TEXT NOT NULL,
                attempt_number INTEGER NOT NULL,
                started_at TEXT,
                completed_at TEXT,
                outcome TEXT,
                FOREIGN KEY(generation_id) REFERENCES queue(generation_id)
            );
            """
        )
        db.executemany(
            "INSERT INTO queue VALUES(?,?,?,?,?)",
            [
                (
                    item.generation_id,
                    item.ordinal,
                    item.execution_envelope_sha256,
                    "pending_disabled",
                    0,
                )
                for item in queue
            ],
        )


def verify_retry_ledger(path: Path, queue: list[GenerationQueueItem]) -> dict[str, Any]:
    with closing(sqlite3.connect(path)) as db:
        rows = db.execute(
            "SELECT generation_id,ordinal,envelope_sha256,status,attempt_count "
            "FROM queue ORDER BY ordinal"
        ).fetchall()
        attempts = db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0]
    expected = [
        (
            item.generation_id,
            item.ordinal,
            item.execution_envelope_sha256,
            "pending_disabled",
            0,
        )
        for item in queue
    ]
    if rows != expected or attempts != 0:
        raise ValueError("disabled retry ledger contains attempts or queue drift")
    return {
        "queue_rows": len(rows),
        "attempt_rows": attempts,
        "all_statuses": ["pending_disabled"],
        "all_attempt_counts": [0],
    }


def build(output_dir: Path = DEFAULT_OUTPUT, report_path: Path = DEFAULT_REPORT) -> dict[str, Any]:
    config, requests = validate_inputs()
    gate = GenerationGate(
        schema_version="1.2-r3.4-generation-gate",
        package_build_authorized=True,
        capability_probe_authorized=False,
        real_generation_authorized=False,
        model_call_authorized=False,
        generation_enabled=False,
    )
    queue = build_queue(requests, gate)
    adapter = DisabledDeepSeekAdapter(gate)
    dry_receipts = [adapter.dry_run(item) for item in queue]
    capability_plan = {
        "schema_version": "1.2-r3.4-capability-probe-plan",
        "status": "not_run_not_authorized",
        "provider": "deepseek",
        "model": "deepseek-v4-pro",
        "base_url": "https://api.deepseek.com/v1",
        "checks": [
            "endpoint and model availability",
            "thinking disabled accepted",
            "json_mode false accepted for prose",
            "seed omitted and seed capability recorded as unverified_dependency",
            "finish_reason and token usage metadata available",
            "nonempty UTF-8 content returned for a non-fiction probe",
        ],
        "probe_payload_kind": "non-fiction capability text only",
        "activation_prerequisites": [
            "independent generation-package audit passes",
            "new versioned activation layer is built",
            "user explicitly authorizes capability probe",
        ],
        "provider_request_sent": False,
        "model_calls": 0,
    }
    retry_policy = {
        "schema_version": "1.2-r3.4-retry-policy",
        "transport_max_retries": 0,
        "silent_reruns_allowed": False,
        "reserve_runs_allowed": False,
        "one_locked_request_per_generation_id": True,
        "failed_or_missing_sample_retained": True,
        "activation_change_requires_new_version": True,
    }
    assert_no_api_key_material([item.model_dump(mode="json") for item in queue])
    assert_no_api_key_material(capability_plan)
    assert_no_api_key_material(retry_policy)

    with tempfile.TemporaryDirectory() as temporary:
        ledger_path = Path(temporary) / "retry-ledger.sqlite"
        create_retry_ledger(ledger_path, queue)
        retry_audit = verify_retry_ledger(ledger_path, queue)
        target = output_dir / "private/retry-ledger.sqlite"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ledger_path, target)

    write_json(output_dir / "private/generation-queue.locked.json", [item.model_dump(mode="json") for item in queue])
    write_json(output_dir / "private/dry-run-receipts.json", [item.model_dump(mode="json") for item in dry_receipts])
    write_json(output_dir / "capability-probe-plan.json", capability_plan)
    write_json(output_dir / "retry-policy.json", retry_policy)
    write_json(output_dir / "generation-gate.json", gate.model_dump(mode="json"))
    write_json(output_dir / "retry-ledger-audit.json", retry_audit)
    call_spec = provider_call_spec()
    package_manifest = {
        "schema_version": config["schema_version"],
        "experiment_id": config["experiment_id"],
        "source_approval_sha256": config["r331_approval_sha256"],
        "source_request_corpus_sha256": config["r3_locked_request_corpus_sha256"],
        "llm_client_source_sha256": config["llm_client_source_sha256"],
        "queue_items": len(queue),
        "dry_run_receipts": len(dry_receipts),
        "call_spec": call_spec.model_dump(mode="json"),
        "capability_probe_status": capability_plan["status"],
        "generation_enabled": False,
        "generation_package_build_authorized": True,
        "capability_probe_authorized": False,
        "real_generation_authorized": False,
        "model_call_authorized": False,
        "model_calls": 0,
        "fiction_texts": 0,
        "next_stage_authorized": "independent_r3_4_generation_package_audit",
    }
    write_json(output_dir / "manifest.json", package_manifest)
    audit = {
        "schema_version": config["schema_version"],
        "source_pins_valid": True,
        "queue_items": len(queue),
        "source_request_hashes_valid": 36,
        "execution_envelope_hashes_valid": len(dry_receipts),
        "dry_run_receipts": len(dry_receipts),
        "provider_requests_sent": 0,
        "retry_queue_rows": retry_audit["queue_rows"],
        "retry_attempt_rows": retry_audit["attempt_rows"],
        "thinking": call_spec.thinking,
        "json_mode": call_spec.json_mode,
        "seed": call_spec.seed,
        "seed_capability": call_spec.seed_capability,
        "transport_max_retries": call_spec.transport_max_retries,
        "capability_probe_status": capability_plan["status"],
        "api_key_material_present": False,
        "generation_enabled": False,
        "model_calls": 0,
        "fiction_texts": 0,
        "r3_4_static_pass": (
            len(queue) == len(dry_receipts) == 36
            and retry_audit["attempt_rows"] == 0
            and capability_plan["provider_request_sent"] is False
        ),
    }
    write_json(output_dir / "r3-4-static-audit.json", audit)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_bytes(
        """# Writer Boundary V1.2 R3.4 真实 Provider Package（生成关闭）

## 结论

R3.4 已构建 DeepSeek 真实 provider 的版本化执行包，但所有网络与生成入口保持 fail-closed。本轮没有 capability probe、模型调用或小说生成。

## 包内容

- 36 个由 R3 locked requests 派生的 generation queue items。
- 每项绑定 source request hash、execution envelope hash、provider call spec 和 generation gate。
- 36 份 dry-run receipt，attempt_count 均为 0。
- SQLite retry ledger：36 个 pending_disabled，0 个 attempt。
- capability probe 仅形成计划，状态为 not_run_not_authorized。
- thinking=disabled、json_mode=false、seed omitted、transport retries=0。

## 授权边界

当前只授权独立审计 R3.4 generation package。Capability probe、真实 DeepSeek 请求、文本生成和生产接入仍需新的版本化激活层与用户明确授权。
""".encode("utf-8")
    )
    return audit


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["build", "audit"])
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report-path", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    result = build(args.output_dir, args.report_path)
    if args.action == "audit" and not result["r3_4_static_pass"]:
        raise SystemExit("R3.4 static audit failed")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
