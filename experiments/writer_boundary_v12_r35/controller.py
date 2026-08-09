from __future__ import annotations

from typing import Any

from experiments.writer_boundary_v12_r3.kernel import digest_json

from .models import ActivationGate, ProbeDryRunReceipt, ProbeEnvelope


class CapabilityProbeDisabledError(RuntimeError):
    pass


def probe_payload(envelope: ProbeEnvelope) -> dict[str, Any]:
    return {
        "schema_version": envelope.schema_version,
        "probe_id": envelope.probe_id,
        "payload_kind": envelope.payload_kind,
        "messages": envelope.messages,
        "call_spec": envelope.call_spec.model_dump(mode="json"),
        "gate_sha256": envelope.gate_sha256,
    }


class ProbeActivationController:
    """R3.5 validates an activation envelope but cannot create a provider client."""

    def __init__(self, gate: ActivationGate):
        self.gate = gate

    def dry_run(self, envelope: ProbeEnvelope) -> ProbeDryRunReceipt:
        consumed = digest_json(probe_payload(envelope))
        if consumed != envelope.envelope_sha256:
            raise ValueError("probe envelope changed after lock")
        return ProbeDryRunReceipt(
            schema_version="1.2-r3.5-probe-dry-run-receipt",
            probe_id=envelope.probe_id,
            expected_envelope_sha256=envelope.envelope_sha256,
            consumed_envelope_sha256=consumed,
            status="dry_run_validated_but_execution_disabled",
            provider_client_created=False,
            provider_request_sent=False,
            attempt_count=0,
            response_content_present=False,
        )

    def arm(self) -> None:
        raise CapabilityProbeDisabledError(
            "R3.5 layer is built but probe-call authorization is absent"
        )

    def execute_probe(self, envelope: ProbeEnvelope) -> str:
        raise CapabilityProbeDisabledError(
            "R3.5 forbids provider client creation, network access, and model calls"
        )
