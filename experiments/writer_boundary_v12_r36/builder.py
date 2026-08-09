from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from typing import Any

from experiments.writer_boundary_v12_r3.kernel import digest_bytes, digest_json
from experiments.writer_boundary_v12_r35.controller import probe_payload
from experiments.writer_boundary_v12_r35.models import ProbeEnvelope

from .models import SingleProbeAuthorization, SingleProbeCallGate


ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "experiments/writer_boundary_v12_r36/fixtures/v1_2_r36_single_probe_call.json"
R35_MANIFEST = ROOT / "outputs/writer-boundary-v1-2-r3-5-activation-layer/manifest.json"
R35_ACTIVATION_GATE = ROOT / "outputs/writer-boundary-v1-2-r3-5-activation-layer/activation-gate.json"
R35_AGGREGATE = ROOT / "outputs/writer-boundary-v1-2-r3-5-activation-layer/review/r3-5-independent-review-aggregate.json"
R35_ENVELOPE = ROOT / "outputs/writer-boundary-v1-2-r3-5-activation-layer/private/probe-envelope.locked.json"
LLM_CLIENT_SOURCE = ROOT / "app/utils/llm_client.py"
DEFAULT_OUTPUT = ROOT / "outputs/writer-boundary-v1-2-r3-6-single-probe-call"
DEFAULT_REPORT = ROOT / "reports/writer-boundary-v1-2-r3-6-single-probe-call-2026-07-31.md"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    )


def validate_inputs() -> tuple[dict[str, Any], ProbeEnvelope]:
    config = load_json(CONFIG)
    for path, key in (
        (R35_MANIFEST, "r35_manifest_sha256"),
        (R35_ACTIVATION_GATE, "r35_activation_gate_sha256"),
        (R35_AGGREGATE, "r35_review_aggregate_sha256"),
        (R35_ENVELOPE, "r35_probe_envelope_file_sha256"),
        (LLM_CLIENT_SOURCE, "llm_client_source_sha256"),
    ):
        if digest_bytes(path.read_bytes()) != config[key]:
            raise ValueError(f"pinned R3.6 input drift: {path.name}")
    aggregate = load_json(R35_AGGREGATE)
    if (
        aggregate["aggregate_verdict"]
        != "recommend_user_authorize_exactly_one_capability_probe_only"
        or aggregate["recommendation_tally"][
            "recommend_user_authorize_one_capability_probe"
        ]
        != 3
        or aggregate["authorization"]["capability_probe_call_authorized"] is not False
        or aggregate["authorization"]["provider_request_authorized"] is not False
        or aggregate["authorization"]["real_generation_authorized"] is not False
        or aggregate["authorization"]["model_call_authorized"] is not False
    ):
        raise ValueError("R3.5 aggregate is not the pinned unanimous advisory result")
    if (
        config["exactly_one_capability_probe_authorized"] is not True
        or config["provider_request_quota"] != 1
        or config["transport_max_retries"] != 0
        or config["general_model_calls_authorized"] is not False
        or config["real_generation_authorized"] is not False
        or config["fiction_generation_authorized"] is not False
    ):
        raise ValueError("R3.6 fixture exceeds the explicit user authorization")
    envelope = ProbeEnvelope.model_validate(load_json(R35_ENVELOPE))
    if digest_json(probe_payload(envelope)) != envelope.envelope_sha256:
        raise ValueError("R3.5 probe envelope internal hash mismatch")
    return config, envelope


def build_authorization(
    config: dict[str, Any],
    envelope: ProbeEnvelope,
) -> SingleProbeAuthorization:
    return SingleProbeAuthorization(
        schema_version="1.2-r3.6-single-probe-authorization",
        authorization_date="2026-07-31",
        authorization_quote=(
            "构建新的版本化单次 probe-call 层，并仅发送一次锁定的非小说 "
            "capability probe；request quota 从 0 变为 1，transport retry "
            "保持 0，真实生成与小说生成继续关闭。"
        ),
        source_review_aggregate_sha256=config["r35_review_aggregate_sha256"],
        source_probe_envelope_file_sha256=config["r35_probe_envelope_file_sha256"],
        probe_id=envelope.probe_id,
        probe_call_layer_build_authorized=True,
        exactly_one_capability_probe_authorized=True,
        provider_request_quota=1,
        transport_max_retries=0,
        general_model_calls_authorized=False,
        real_generation_authorized=False,
        fiction_generation_authorized=False,
    )


def call_gate(envelope: ProbeEnvelope) -> SingleProbeCallGate:
    if envelope.call_spec.transport_max_retries != 0:
        raise ValueError("locked probe envelope enables transport retries")
    return SingleProbeCallGate(
        schema_version="1.2-r3.6-single-probe-call-gate",
        probe_id=envelope.probe_id,
        capability_probe_call_authorized=True,
        provider_request_authorized=True,
        capability_probe_model_call_authorized=True,
        provider_request_quota=1,
        transport_max_retries=0,
        silent_retry_authorized=False,
        reserve_run_authorized=False,
        general_model_calls_authorized=False,
        real_generation_authorized=False,
        real_generation_enabled=False,
        fiction_generation_authorized=False,
        production_integration_authorized=False,
    )


def create_single_shot_ledger(path: Path, envelope: ProbeEnvelope) -> None:
    with closing(sqlite3.connect(path)) as db, db:
        db.executescript(
            """
            PRAGMA journal_mode=DELETE;
            CREATE TABLE probe_state(
                probe_id TEXT PRIMARY KEY,
                envelope_sha256 TEXT UNIQUE NOT NULL,
                status TEXT NOT NULL CHECK(
                    status IN ('authorized_not_used','reserved','succeeded','failed')
                ),
                quota_remaining INTEGER NOT NULL CHECK(quota_remaining IN (0,1)),
                attempt_count INTEGER NOT NULL CHECK(attempt_count IN (0,1))
            );
            CREATE TABLE probe_attempts(
                attempt_id INTEGER PRIMARY KEY CHECK(attempt_id=1),
                probe_id TEXT NOT NULL,
                attempt_number INTEGER NOT NULL CHECK(attempt_number=1),
                reserved_at TEXT NOT NULL,
                request_attempted INTEGER NOT NULL CHECK(request_attempted IN (0,1)),
                completed_at TEXT,
                outcome TEXT CHECK(outcome IN ('succeeded','failed')),
                response_sha256 TEXT,
                error_type TEXT,
                error_message_sha256 TEXT,
                FOREIGN KEY(probe_id) REFERENCES probe_state(probe_id)
            );
            """
        )
        db.execute(
            "INSERT INTO probe_state VALUES(?,?,?,?,?)",
            (
                envelope.probe_id,
                envelope.envelope_sha256,
                "authorized_not_used",
                1,
                0,
            ),
        )


def build(
    output_dir: Path = DEFAULT_OUTPUT,
    report_path: Path = DEFAULT_REPORT,
) -> dict[str, Any]:
    config, envelope = validate_inputs()
    authorization = build_authorization(config, envelope)
    gate = call_gate(envelope)
    ledger_target = output_dir / "private/single-probe-ledger.sqlite"
    if ledger_target.exists():
        raise FileExistsError("R3.6 ledger already exists; refusing to reset quota")
    with tempfile.TemporaryDirectory() as temporary:
        ledger = Path(temporary) / "single-probe-ledger.sqlite"
        create_single_shot_ledger(ledger, envelope)
        ledger_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ledger, ledger_target)
    write_json(
        output_dir / "single-probe-authorization.json",
        authorization.model_dump(mode="json"),
    )
    write_json(output_dir / "single-probe-call-gate.json", gate.model_dump(mode="json"))
    write_json(
        output_dir / "private/probe-envelope.pinned.json",
        envelope.model_dump(mode="json"),
    )
    manifest = {
        "schema_version": config["schema_version"],
        "experiment_id": config["experiment_id"],
        "source_pins_valid": True,
        "probe_call_layer_built": True,
        "probe_id": envelope.probe_id,
        "probe_call_status": "authorized_not_used",
        "provider_request_quota": 1,
        "provider_requests_attempted": 0,
        "transport_max_retries": 0,
        "silent_retry_authorized": False,
        "general_model_calls_authorized": False,
        "real_generation_authorized": False,
        "real_generation_enabled": False,
        "fiction_generation_authorized": False,
        "fiction_texts": 0,
    }
    write_json(output_dir / "manifest.pre-execution.json", manifest)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        "# Writer Boundary V1.2 R3.6 Single Capability Probe\n\n"
        "R3.6 已构建单次 probe-call 层。锁定 probe 的初始 quota=1，transport "
        "retry=0。调用前必须原子消耗 quota；无论结果如何都不得再次调用。\n\n"
        "本层不授权一般模型调用、真实生成、小说生成或生产接入。\n",
        encoding="utf-8",
        newline="\n",
    )
    return manifest


if __name__ == "__main__":
    print(json.dumps(build(), ensure_ascii=False, indent=2))
