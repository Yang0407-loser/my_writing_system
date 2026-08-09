from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from typing import Any

from experiments.writer_boundary_v12_r3.kernel import digest_bytes, digest_json

from .controller import ProbeActivationController, probe_payload
from .models import (
    ActivationGate,
    LayerBuildAuthorization,
    ProbeCallSpec,
    ProbeEnvelope,
)


ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "experiments/writer_boundary_v12_r35/fixtures/v1_2_r35_activation_layer.json"
R34_MANIFEST = ROOT / "outputs/writer-boundary-v1-2-r3-4-generation-package/manifest.json"
R34_PROBE_PLAN = ROOT / "outputs/writer-boundary-v1-2-r3-4-generation-package/capability-probe-plan.json"
R341_AGGREGATE = ROOT / "outputs/writer-boundary-v1-2-r3-4-1-review-protocol/review/r3-4-1-targeted-review-aggregate.json"
LLM_CLIENT_SOURCE = ROOT / "app/utils/llm_client.py"
DEFAULT_OUTPUT = ROOT / "outputs/writer-boundary-v1-2-r3-5-activation-layer"
DEFAULT_REPORT = ROOT / "reports/writer-boundary-v1-2-r3-5-activation-layer-disabled-2026-07-31.md"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    )


def validate_inputs() -> dict[str, Any]:
    config = load_json(CONFIG)
    for path, key in (
        (R34_MANIFEST, "r34_manifest_sha256"),
        (R34_PROBE_PLAN, "r34_probe_plan_sha256"),
        (R341_AGGREGATE, "r341_review_aggregate_sha256"),
        (LLM_CLIENT_SOURCE, "llm_client_source_sha256"),
    ):
        if digest_bytes(path.read_bytes()) != config[key]:
            raise ValueError(f"pinned R3.5 input drift: {path.name}")
    aggregate = load_json(R341_AGGREGATE)
    if (
        aggregate["aggregate_verdict"]
        != "recommend_user_authorize_capability_probe_layer_build_only"
        or aggregate["recommendation_tally"]["recommend_layer_build"] != 3
        or aggregate["authorization"]["capability_probe_layer_build_authorized"] is not False
        or aggregate["authorization"]["capability_probe_call_authorized"] is not False
        or aggregate["authorization"]["real_generation_authorized"] is not False
        or aggregate["authorization"]["model_call_authorized"] is not False
    ):
        raise ValueError("R3.4.1 aggregate is not the pinned unanimous advisory result")
    if (
        config["activation_layer_build_authorized"] is not True
        or config["capability_probe_call_authorized"] is not False
        or config["real_generation_authorized"] is not False
        or config["model_call_authorized"] is not False
    ):
        raise ValueError("R3.5 build authorization exceeds the user-approved boundary")
    return config


def build_authorization(config: dict[str, Any]) -> LayerBuildAuthorization:
    return LayerBuildAuthorization(
        schema_version="1.2-r3.5-layer-build-authorization",
        authorization_date="2026-07-31",
        authorization_quote=(
            "授权构建版本化 capability-probe activation layer，但保持 probe 调用、"
            "真实生成和模型调用全部关闭。"
        ),
        source_review_aggregate_sha256=config["r341_review_aggregate_sha256"],
        capability_probe_layer_build_authorized=True,
        capability_probe_call_authorized=False,
        real_generation_authorized=False,
        model_call_authorized=False,
    )


def activation_gate() -> ActivationGate:
    return ActivationGate(
        schema_version="1.2-r3.5-activation-gate",
        activation_layer_built=True,
        layer_build_authorized=True,
        capability_probe_call_authorized=False,
        probe_execution_enabled=False,
        probe_request_quota=0,
        real_generation_authorized=False,
        real_generation_enabled=False,
        model_call_authorized=False,
        provider_client_creation_authorized=False,
        network_access_authorized=False,
    )


def build_probe_envelope(gate: ActivationGate) -> ProbeEnvelope:
    call_spec = ProbeCallSpec(
        provider="deepseek",
        model="deepseek-v4-pro",
        base_url="https://api.deepseek.com/v1",
        temperature=0.0,
        max_tokens=16,
        json_mode=False,
        thinking="disabled",
        transport_max_retries=0,
        seed=None,
    )
    base = {
        "schema_version": "1.2-r3.5-probe-envelope",
        "probe_id": "CAPABILITY-PROBE-R35-001",
        "payload_kind": "non-fiction capability text only",
        "messages": [
            {
                "role": "system",
                "content": "This is a provider capability check. Do not produce creative prose.",
            },
            {
                "role": "user",
                "content": "Reply with exactly CAPABILITY_OK.",
            },
        ],
        "call_spec": call_spec.model_dump(mode="json"),
        "gate_sha256": digest_json(gate.model_dump(mode="json")),
    }
    return ProbeEnvelope(**base, envelope_sha256=digest_json(base))


def create_zero_call_ledger(path: Path, envelope: ProbeEnvelope) -> None:
    with closing(sqlite3.connect(path)) as db, db:
        db.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE probe_plan(
                probe_id TEXT PRIMARY KEY,
                envelope_sha256 TEXT UNIQUE NOT NULL,
                status TEXT NOT NULL CHECK(status='built_disabled'),
                request_quota INTEGER NOT NULL CHECK(request_quota=0),
                attempt_count INTEGER NOT NULL CHECK(attempt_count=0)
            );
            CREATE TABLE probe_attempts(
                attempt_id INTEGER PRIMARY KEY,
                probe_id TEXT NOT NULL,
                outcome TEXT,
                FOREIGN KEY(probe_id) REFERENCES probe_plan(probe_id)
            );
            """
        )
        db.execute(
            "INSERT INTO probe_plan VALUES(?,?,?,?,?)",
            (
                envelope.probe_id,
                envelope.envelope_sha256,
                "built_disabled",
                0,
                0,
            ),
        )


def verify_zero_call_ledger(path: Path, envelope: ProbeEnvelope) -> dict[str, Any]:
    with closing(sqlite3.connect(path)) as db:
        plan = db.execute(
            "SELECT probe_id,envelope_sha256,status,request_quota,attempt_count "
            "FROM probe_plan"
        ).fetchall()
        attempts = db.execute("SELECT COUNT(*) FROM probe_attempts").fetchone()[0]
    expected = [
        (
            envelope.probe_id,
            envelope.envelope_sha256,
            "built_disabled",
            0,
            0,
        )
    ]
    if plan != expected or attempts != 0:
        raise ValueError("R3.5 ledger contains activation or call evidence")
    return {
        "probe_plan_rows": len(plan),
        "probe_attempt_rows": attempts,
        "status": "built_disabled",
        "request_quota": 0,
        "attempt_count": 0,
    }


def build(
    output_dir: Path = DEFAULT_OUTPUT,
    report_path: Path = DEFAULT_REPORT,
) -> dict[str, Any]:
    config = validate_inputs()
    authorization = build_authorization(config)
    gate = activation_gate()
    envelope = build_probe_envelope(gate)
    controller = ProbeActivationController(gate)
    receipt = controller.dry_run(envelope)
    if digest_json(probe_payload(envelope)) != envelope.envelope_sha256:
        raise ValueError("locked probe envelope failed final verification")

    with tempfile.TemporaryDirectory() as temporary:
        ledger_path = Path(temporary) / "probe-ledger.sqlite"
        create_zero_call_ledger(ledger_path, envelope)
        ledger_audit = verify_zero_call_ledger(ledger_path, envelope)
        target = output_dir / "private/probe-ledger.sqlite"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ledger_path, target)

    write_json(
        output_dir / "layer-build-authorization.json",
        authorization.model_dump(mode="json"),
    )
    write_json(output_dir / "activation-gate.json", gate.model_dump(mode="json"))
    write_json(
        output_dir / "private/probe-envelope.locked.json",
        envelope.model_dump(mode="json"),
    )
    write_json(
        output_dir / "private/probe-dry-run-receipt.json",
        receipt.model_dump(mode="json"),
    )
    write_json(output_dir / "probe-ledger-audit.json", ledger_audit)
    runbook = {
        "schema_version": "1.2-r3.5-activation-runbook",
        "current_state": "layer_built_probe_disabled",
        "current_request_quota": 0,
        "current_provider_client_creation_allowed": False,
        "current_network_access_allowed": False,
        "future_probe_call_requires": [
            "independent R3.5 static audit passes",
            "new versioned probe-call authorization layer",
            "new explicit user authorization for exactly one capability probe",
            "request quota changes from 0 to 1 only in that new version",
            "real generation and fiction generation remain disabled",
        ],
        "automatic_transition_allowed": False,
        "silent_retry_allowed": False,
        "provider_transport_retries": 0,
    }
    write_json(output_dir / "activation-runbook.json", runbook)
    manifest = {
        "schema_version": config["schema_version"],
        "experiment_id": config["experiment_id"],
        "source_pins_valid": True,
        "activation_layer_built": True,
        "activation_layer_build_authorized": True,
        "capability_probe_status": "built_disabled_not_run",
        "capability_probe_call_authorized": False,
        "probe_execution_enabled": False,
        "probe_request_quota": 0,
        "provider_client_created": False,
        "provider_requests_sent": 0,
        "probe_attempts": 0,
        "probe_responses": 0,
        "real_generation_authorized": False,
        "real_generation_enabled": False,
        "model_call_authorized": False,
        "model_calls": 0,
        "fiction_texts": 0,
        "next_stage_authorized": "independent_r3_5_activation_layer_static_audit",
    }
    write_json(output_dir / "manifest.json", manifest)
    audit = {
        "schema_version": "1.2-r3.5-static-audit",
        "source_pins_valid": True,
        "authorization_quote_locked": True,
        "gate_fail_closed": True,
        "probe_envelope_hash_valid": True,
        "dry_run_receipt_valid": True,
        "ledger_zero_attempts": ledger_audit["probe_attempt_rows"] == 0,
        "provider_client_created": False,
        "provider_requests_sent": 0,
        "probe_responses": 0,
        "model_calls": 0,
        "fiction_texts": 0,
        "r3_5_static_pass": (
            receipt.provider_request_sent is False
            and receipt.provider_client_created is False
            and ledger_audit["probe_attempt_rows"] == 0
            and gate.probe_request_quota == 0
        ),
    }
    write_json(output_dir / "r3-5-static-audit.json", audit)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        "# Writer Boundary V1.2 R3.5 Capability-Probe Activation Layer\n\n"
        "R3.5 已按用户明确授权构建版本化 activation layer，但 probe 调用、"
        "provider client 创建、网络访问、真实生成和模型调用全部保持关闭。\n\n"
        "当前状态为 `layer_built_probe_disabled`。锁定 probe envelope 只完成"
        " dry-run；request quota=0，attempt=0，response=0。\n\n"
        "下一步只允许对 R3.5 做独立静态审计。任何真实 capability probe 都必须"
        "在新版本中取得新的、明确的单次调用授权。\n",
        encoding="utf-8",
        newline="\n",
    )
    return audit


if __name__ == "__main__":
    print(json.dumps(build(), ensure_ascii=False, indent=2))
