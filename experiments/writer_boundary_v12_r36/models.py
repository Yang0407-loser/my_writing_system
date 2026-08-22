from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


SHA256 = r"^[0-9a-f]{64}$"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SingleProbeAuthorization(Strict):
    schema_version: Literal["1.2-r3.6-single-probe-authorization"]
    authorization_date: Literal["2026-07-31"]
    authorization_quote: Literal[
        "构建新的版本化单次 probe-call 层，并仅发送一次锁定的非小说 capability probe；request quota 从 0 变为 1，transport retry 保持 0，真实生成与小说生成继续关闭。"
    ]
    source_review_aggregate_sha256: str = Field(pattern=SHA256)
    source_probe_envelope_file_sha256: str = Field(pattern=SHA256)
    probe_id: Literal["CAPABILITY-PROBE-R35-001"]
    probe_call_layer_build_authorized: Literal[True]
    exactly_one_capability_probe_authorized: Literal[True]
    provider_request_quota: Literal[1]
    transport_max_retries: Literal[0]
    general_model_calls_authorized: Literal[False]
    real_generation_authorized: Literal[False]
    fiction_generation_authorized: Literal[False]


class SingleProbeCallGate(Strict):
    schema_version: Literal["1.2-r3.6-single-probe-call-gate"]
    probe_id: Literal["CAPABILITY-PROBE-R35-001"]
    capability_probe_call_authorized: Literal[True]
    provider_request_authorized: Literal[True]
    capability_probe_model_call_authorized: Literal[True]
    provider_request_quota: Literal[1]
    transport_max_retries: Literal[0]
    silent_retry_authorized: Literal[False]
    reserve_run_authorized: Literal[False]
    general_model_calls_authorized: Literal[False]
    real_generation_authorized: Literal[False]
    real_generation_enabled: Literal[False]
    fiction_generation_authorized: Literal[False]
    production_integration_authorized: Literal[False]


class ProbeExecutionReceipt(Strict):
    schema_version: Literal["1.2-r3.6-probe-execution-receipt"]
    probe_id: Literal["CAPABILITY-PROBE-R35-001"]
    attempt_number: Literal[1]
    quota_before: Literal[1]
    quota_after: Literal[0]
    transport_max_retries: Literal[0]
    provider_request_attempted: Literal[True]
    provider_request_attempt_count: Literal[1]
    outcome: Literal["succeeded", "failed"]
    response_content_present: bool
    response_sha256: str | None = Field(default=None, pattern=SHA256)
    response_exactly_expected: bool
    finish_reason: str | None
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    latency_seconds: float | None = Field(default=None, ge=0)
    error_type: str | None
    error_message_sha256: str | None = Field(default=None, pattern=SHA256)

    @model_validator(mode="after")
    def outcome_fields_match(self) -> "ProbeExecutionReceipt":
        if self.outcome == "succeeded":
            if not self.response_content_present or self.response_sha256 is None:
                raise ValueError("successful probe must contain a response hash")
            if self.error_type is not None or self.error_message_sha256 is not None:
                raise ValueError("successful probe cannot contain error fields")
        else:
            if self.response_content_present or self.response_sha256 is not None:
                raise ValueError("failed probe cannot contain response fields")
            if self.error_type is None or self.error_message_sha256 is None:
                raise ValueError("failed probe must contain sanitized error evidence")
        return self
