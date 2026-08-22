from __future__ import annotations

from typing import Any

from experiments.writer_boundary_v12_r3.kernel import digest_json

from .models import DryRunReceipt, GenerationGate, GenerationQueueItem, ProviderCallSpec


class GenerationDisabledError(RuntimeError):
    pass


def provider_call_spec() -> ProviderCallSpec:
    return ProviderCallSpec(
        provider="deepseek",
        model="deepseek-v4-pro",
        base_url="https://api.deepseek.com/v1",
        temperature=0.7,
        max_tokens=3000,
        json_mode=False,
        thinking="disabled",
        transport_max_retries=0,
        seed=None,
        seed_required=False,
        seed_capability="unverified_dependency",
    )


def execution_payload(item: GenerationQueueItem) -> dict[str, Any]:
    return {
        "generation_id": item.generation_id,
        "messages": item.messages,
        "call_spec": item.call_spec.model_dump(mode="json"),
        "gate_sha256": item.gate_sha256,
    }


class DisabledDeepSeekAdapter:
    """Build-only adapter. A later versioned activation layer must implement sending."""

    def __init__(self, gate: GenerationGate):
        self.gate = gate

    def dry_run(self, item: GenerationQueueItem) -> DryRunReceipt:
        consumed = digest_json(execution_payload(item))
        if consumed != item.execution_envelope_sha256:
            raise ValueError("execution envelope changed after queue lock")
        return DryRunReceipt(
            schema_version="1.2-r3.4-dry-run-receipt",
            generation_id=item.generation_id,
            expected_envelope_sha256=item.execution_envelope_sha256,
            consumed_envelope_sha256=consumed,
            status="dry_run_validated",
            attempt_count=0,
            provider_request_sent=False,
            response_content_present=False,
        )

    def execute(self, item: GenerationQueueItem) -> str:
        raise GenerationDisabledError(
            "R3.4 is build-only: capability probe, model calls, and real generation are not authorized"
        )


def assert_no_api_key_material(value: Any) -> None:
    text = str(value).lower()
    forbidden = ("api_key", "api-key", "authorization: bearer", "sk-")
    if any(item in text for item in forbidden):
        raise ValueError("generation package contains API key material")

