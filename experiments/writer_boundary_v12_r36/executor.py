from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from experiments.writer_boundary_v12_r35.models import ProbeEnvelope

from .builder import DEFAULT_OUTPUT, load_json, validate_inputs, write_json
from .models import ProbeExecutionReceipt, SingleProbeCallGate


class ProbeQuotaConsumedError(RuntimeError):
    pass


ProviderCall = Callable[[ProbeEnvelope], tuple[str, dict[str, Any]]]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def validate_runtime_configuration(envelope: ProbeEnvelope) -> None:
    from app.config import settings

    if not settings.LLM_API_KEY:
        raise ValueError("LLM credential is unavailable")
    if settings.LLM_BASE_URL != envelope.call_spec.base_url:
        raise ValueError("runtime base URL differs from locked envelope")
    if settings.LLM_MODEL != envelope.call_spec.model:
        raise ValueError("runtime model differs from locked envelope")


def reserve_exactly_once(ledger_path: Path, envelope: ProbeEnvelope) -> None:
    with closing(sqlite3.connect(ledger_path, isolation_level=None)) as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT envelope_sha256,status,quota_remaining,attempt_count "
            "FROM probe_state WHERE probe_id=?",
            (envelope.probe_id,),
        ).fetchone()
        if row != (
            envelope.envelope_sha256,
            "authorized_not_used",
            1,
            0,
        ):
            db.execute("ROLLBACK")
            raise ProbeQuotaConsumedError(
                "single capability-probe quota is unavailable or already consumed"
            )
        db.execute(
            "UPDATE probe_state SET status='reserved',quota_remaining=0,"
            "attempt_count=1 WHERE probe_id=?",
            (envelope.probe_id,),
        )
        db.execute(
            "INSERT INTO probe_attempts("
            "attempt_id,probe_id,attempt_number,reserved_at,request_attempted"
            ") VALUES(1,?,?,?,0)",
            (envelope.probe_id, 1, utc_now()),
        )
        db.execute("COMMIT")


def mark_request_attempted(ledger_path: Path) -> None:
    with closing(sqlite3.connect(ledger_path)) as db, db:
        changed = db.execute(
            "UPDATE probe_attempts SET request_attempted=1 "
            "WHERE attempt_id=1 AND request_attempted=0"
        ).rowcount
        if changed != 1:
            raise ProbeQuotaConsumedError("probe request attempt was already recorded")


def finalize_ledger(
    ledger_path: Path,
    outcome: str,
    response_sha256: str | None = None,
    error_type: str | None = None,
    error_message_sha256: str | None = None,
) -> None:
    with closing(sqlite3.connect(ledger_path, isolation_level=None)) as db:
        db.execute("BEGIN IMMEDIATE")
        state = db.execute(
            "SELECT status,quota_remaining,attempt_count FROM probe_state"
        ).fetchone()
        attempt = db.execute(
            "SELECT request_attempted,outcome FROM probe_attempts WHERE attempt_id=1"
        ).fetchone()
        if state != ("reserved", 0, 1) or attempt != (1, None):
            db.execute("ROLLBACK")
            raise ProbeQuotaConsumedError("probe ledger cannot be finalized twice")
        db.execute(
            "UPDATE probe_state SET status=?",
            (outcome,),
        )
        db.execute(
            "UPDATE probe_attempts SET completed_at=?,outcome=?,response_sha256=?,"
            "error_type=?,error_message_sha256=? WHERE attempt_id=1",
            (
                utc_now(),
                outcome,
                response_sha256,
                error_type,
                error_message_sha256,
            ),
        )
        db.execute("COMMIT")


def perform_provider_call(envelope: ProbeEnvelope) -> tuple[str, dict[str, Any]]:
    from app.utils.llm_client import get_llm_client

    metadata: dict[str, Any] = {}
    content = get_llm_client().chat_completion(
        messages=envelope.messages,
        temperature=envelope.call_spec.temperature,
        max_tokens=envelope.call_spec.max_tokens,
        max_retries=0,
        json_mode=envelope.call_spec.json_mode,
        prompt_name="writer_boundary_r36_capability_probe",
        completion_metadata_sink=metadata.update,
    )
    return content, metadata


def execute_once(
    output_dir: Path = DEFAULT_OUTPUT,
    provider_call: ProviderCall = perform_provider_call,
) -> ProbeExecutionReceipt:
    _, source_envelope = validate_inputs()
    pinned_envelope = ProbeEnvelope.model_validate(
        load_json(output_dir / "private/probe-envelope.pinned.json")
    )
    if pinned_envelope != source_envelope:
        raise ValueError("R3.6 pinned envelope differs from R3.5 source")
    gate = SingleProbeCallGate.model_validate(
        load_json(output_dir / "single-probe-call-gate.json")
    )
    if gate.probe_id != pinned_envelope.probe_id:
        raise ValueError("R3.6 gate probe ID mismatch")
    validate_runtime_configuration(pinned_envelope)
    ledger = output_dir / "private/single-probe-ledger.sqlite"
    reserve_exactly_once(ledger, pinned_envelope)
    mark_request_attempted(ledger)
    try:
        content, metadata = provider_call(pinned_envelope)
        response_hash = sha256_text(content)
        finalize_ledger(ledger, "succeeded", response_sha256=response_hash)
        write_json(
            output_dir / "private/probe-response.json",
            {
                "schema_version": "1.2-r3.6-probe-response",
                "probe_id": pinned_envelope.probe_id,
                "content": content,
                "content_sha256": response_hash,
            },
        )
        receipt = ProbeExecutionReceipt(
            schema_version="1.2-r3.6-probe-execution-receipt",
            probe_id=pinned_envelope.probe_id,
            attempt_number=1,
            quota_before=1,
            quota_after=0,
            transport_max_retries=0,
            provider_request_attempted=True,
            provider_request_attempt_count=1,
            outcome="succeeded",
            response_content_present=True,
            response_sha256=response_hash,
            response_exactly_expected=content.strip() == "CAPABILITY_OK",
            finish_reason=metadata.get("finish_reason"),
            input_tokens=metadata.get("input_tokens"),
            output_tokens=metadata.get("output_tokens"),
            latency_seconds=metadata.get("latency_seconds"),
            error_type=None,
            error_message_sha256=None,
        )
    except Exception as error:
        error_hash = sha256_text(str(error))
        finalize_ledger(
            ledger,
            "failed",
            error_type=type(error).__name__,
            error_message_sha256=error_hash,
        )
        receipt = ProbeExecutionReceipt(
            schema_version="1.2-r3.6-probe-execution-receipt",
            probe_id=pinned_envelope.probe_id,
            attempt_number=1,
            quota_before=1,
            quota_after=0,
            transport_max_retries=0,
            provider_request_attempted=True,
            provider_request_attempt_count=1,
            outcome="failed",
            response_content_present=False,
            response_sha256=None,
            response_exactly_expected=False,
            finish_reason=None,
            input_tokens=None,
            output_tokens=None,
            latency_seconds=None,
            error_type=type(error).__name__,
            error_message_sha256=error_hash,
        )
    write_json(
        output_dir / "private/probe-execution-receipt.json",
        receipt.model_dump(mode="json"),
    )
    post_audit = {
        "schema_version": "1.2-r3.6-post-execution-audit",
        "probe_id": pinned_envelope.probe_id,
        "outcome": receipt.outcome,
        "quota_remaining": 0,
        "provider_request_attempt_count": 1,
        "transport_retries": 0,
        "silent_retries": 0,
        "probe_response_count": 1 if receipt.response_content_present else 0,
        "response_exactly_expected": receipt.response_exactly_expected,
        "general_model_calls": 0,
        "real_generations": 0,
        "fiction_texts": 0,
        "second_call_authorized": False,
    }
    write_json(output_dir / "r3-6-post-execution-audit.json", post_audit)
    return receipt


if __name__ == "__main__":
    result = execute_once()
    print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))
