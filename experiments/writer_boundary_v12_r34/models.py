from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


SHA256 = r"^[0-9a-f]{64}$"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GenerationGate(Strict):
    schema_version: Literal["1.2-r3.4-generation-gate"]
    package_build_authorized: Literal[True]
    capability_probe_authorized: Literal[False]
    real_generation_authorized: Literal[False]
    model_call_authorized: Literal[False]
    generation_enabled: Literal[False]


class ProviderCallSpec(Strict):
    provider: Literal["deepseek"]
    model: Literal["deepseek-v4-pro"]
    base_url: Literal["https://api.deepseek.com/v1"]
    temperature: Literal[0.7]
    max_tokens: Literal[3000]
    json_mode: Literal[False]
    thinking: Literal["disabled"]
    transport_max_retries: Literal[0]
    seed: None
    seed_required: Literal[False]
    seed_capability: Literal["unverified_dependency"]


class GenerationQueueItem(Strict):
    schema_version: Literal["1.2-r3.4-queue-item"]
    generation_id: str
    ordinal: int = Field(ge=1, le=36)
    block_id: str
    scene_id: str
    arm: Literal["A", "B", "C"]
    text_id: str
    source_request_sha256: str = Field(pattern=SHA256)
    execution_envelope_sha256: str = Field(pattern=SHA256)
    messages: list[dict[str, Any]]
    call_spec: ProviderCallSpec
    gate_sha256: str = Field(pattern=SHA256)


class DryRunReceipt(Strict):
    schema_version: Literal["1.2-r3.4-dry-run-receipt"]
    generation_id: str
    expected_envelope_sha256: str = Field(pattern=SHA256)
    consumed_envelope_sha256: str = Field(pattern=SHA256)
    status: Literal["dry_run_validated"]
    attempt_count: Literal[0]
    provider_request_sent: Literal[False]
    response_content_present: Literal[False]

    @model_validator(mode="after")
    def hashes_match(self) -> "DryRunReceipt":
        if self.expected_envelope_sha256 != self.consumed_envelope_sha256:
            raise ValueError("dry-run envelope mismatch")
        return self

