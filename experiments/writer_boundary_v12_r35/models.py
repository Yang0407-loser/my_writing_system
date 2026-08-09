from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


SHA256 = r"^[0-9a-f]{64}$"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LayerBuildAuthorization(Strict):
    schema_version: Literal["1.2-r3.5-layer-build-authorization"]
    authorization_date: Literal["2026-07-31"]
    authorization_quote: Literal[
        "授权构建版本化 capability-probe activation layer，但保持 probe 调用、真实生成和模型调用全部关闭。"
    ]
    source_review_aggregate_sha256: str = Field(pattern=SHA256)
    capability_probe_layer_build_authorized: Literal[True]
    capability_probe_call_authorized: Literal[False]
    real_generation_authorized: Literal[False]
    model_call_authorized: Literal[False]


class ActivationGate(Strict):
    schema_version: Literal["1.2-r3.5-activation-gate"]
    activation_layer_built: Literal[True]
    layer_build_authorized: Literal[True]
    capability_probe_call_authorized: Literal[False]
    probe_execution_enabled: Literal[False]
    probe_request_quota: Literal[0]
    real_generation_authorized: Literal[False]
    real_generation_enabled: Literal[False]
    model_call_authorized: Literal[False]
    provider_client_creation_authorized: Literal[False]
    network_access_authorized: Literal[False]


class ProbeCallSpec(Strict):
    provider: Literal["deepseek"]
    model: Literal["deepseek-v4-pro"]
    base_url: Literal["https://api.deepseek.com/v1"]
    temperature: Literal[0.0]
    max_tokens: Literal[16]
    json_mode: Literal[False]
    thinking: Literal["disabled"]
    transport_max_retries: Literal[0]
    seed: None


class ProbeEnvelope(Strict):
    schema_version: Literal["1.2-r3.5-probe-envelope"]
    probe_id: Literal["CAPABILITY-PROBE-R35-001"]
    payload_kind: Literal["non-fiction capability text only"]
    messages: list[dict[str, str]]
    call_spec: ProbeCallSpec
    gate_sha256: str = Field(pattern=SHA256)
    envelope_sha256: str = Field(pattern=SHA256)

    @model_validator(mode="after")
    def validate_payload(self) -> "ProbeEnvelope":
        if len(self.messages) != 2:
            raise ValueError("capability probe must have exactly two messages")
        if [message.get("role") for message in self.messages] != ["system", "user"]:
            raise ValueError("capability probe message roles are frozen")
        text = " ".join(message.get("content", "") for message in self.messages).lower()
        forbidden = ("novel", "fiction scene", "character", "relationship", "故事", "小说")
        if any(token in text for token in forbidden):
            raise ValueError("capability probe must remain non-fiction")
        return self


class ProbeDryRunReceipt(Strict):
    schema_version: Literal["1.2-r3.5-probe-dry-run-receipt"]
    probe_id: Literal["CAPABILITY-PROBE-R35-001"]
    expected_envelope_sha256: str = Field(pattern=SHA256)
    consumed_envelope_sha256: str = Field(pattern=SHA256)
    status: Literal["dry_run_validated_but_execution_disabled"]
    provider_client_created: Literal[False]
    provider_request_sent: Literal[False]
    attempt_count: Literal[0]
    response_content_present: Literal[False]

    @model_validator(mode="after")
    def validate_hash(self) -> "ProbeDryRunReceipt":
        if self.expected_envelope_sha256 != self.consumed_envelope_sha256:
            raise ValueError("probe envelope hash mismatch")
        return self
