"""Credential hygiene for durable writing-task checkpoints.

Checkpoint state is application data, never a credential transport. Matching
is deliberately exact so similarly named business fields remain intact.
"""

from __future__ import annotations

from typing import Any


CREDENTIAL_FIELD_NAMES = frozenset(
    {
        "access_token",
        "api_key",
        "authorization",
        "client_secret",
        "id_token",
        "llm_api_key",
        "passwd",
        "password",
        "proxy_authorization",
        "refresh_token",
        "x_api_key",
    }
)


def normalize_field_name(field: object) -> str:
    """Normalize a key for exact credential-name comparison."""
    return str(field).strip().casefold().replace("-", "_")


def is_credential_field(field: object) -> bool:
    """Return whether *field* is an explicitly recognized credential key."""
    return normalize_field_name(field) in CREDENTIAL_FIELD_NAMES


def sanitize_checkpoint(value: Any) -> Any:
    """Return a recursively credential-free copy of checkpoint-compatible data."""
    if isinstance(value, dict):
        return {
            key: sanitize_checkpoint(nested)
            for key, nested in value.items()
            if not is_credential_field(key)
        }
    if isinstance(value, list):
        return [sanitize_checkpoint(nested) for nested in value]
    if isinstance(value, tuple):
        return tuple(sanitize_checkpoint(nested) for nested in value)
    return value
