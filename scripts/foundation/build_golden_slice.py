"""Build the authorized, secret-free Foundation Golden Vertical Slice."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any


EXPECTED_SOURCE_PATH = "output/周六面包店与凌晨三点半_20260715_143857.md"
EXPECTED_SOURCE_SHA256 = "0B1E3153D81E1CE1A1BAA8D23BDB6A8629BABD29EF29CA381D4922EBB7B42F96"
_SECTION_RE = re.compile(r"(?m)^(第(?P<ordinal>\d+)节[：:].+?)\s*$")
_CREDENTIAL_KEYS = {
    "access_token",
    "api_key",
    "client_secret",
    "llm_api_key",
    "password",
    "refresh_token",
    "x_api_key",
}
_SECRET_VALUE_PATTERNS = (
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]+=*", re.IGNORECASE),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)


def canonical_json_bytes(payload: dict[str, Any]) -> bytes:
    """Serialize a fixture with stable ordering and one trailing newline."""
    return (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _extract_first_subsection(source_text: str) -> tuple[int, str, str]:
    matches = list(_SECTION_RE.finditer(source_text))
    if not matches:
        raise ValueError("source contains no subsection heading")
    first = matches[0]
    end = matches[1].start() if len(matches) > 1 else len(source_text)
    body = source_text[first.end() : end].strip()
    if not body:
        raise ValueError("first subsection body is empty")
    return int(first.group("ordinal")), first.group(1), body


def _scan_for_secrets(value: Any, path: str = "$") -> list[str]:
    findings: list[str] = []
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized = str(key).strip().casefold().replace("-", "_")
            child_path = f"{path}.{key}"
            if normalized in _CREDENTIAL_KEYS:
                findings.append(f"credential field at {child_path}")
            findings.extend(_scan_for_secrets(nested, child_path))
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            findings.extend(_scan_for_secrets(nested, f"{path}[{index}]"))
    elif isinstance(value, str):
        for pattern in _SECRET_VALUE_PATTERNS:
            if pattern.search(value):
                findings.append(f"credential-like value at {path}")
                break
    return findings


def build_golden_slice(source_text: str, source_sha256: str) -> dict[str, Any]:
    """Build the pure fixture payload from an already verified source text."""
    ordinal, heading, body = _extract_first_subsection(source_text)
    payload: dict[str, Any] = {
        "schema_version": "foundation-golden-slice-v1",
        "ids": {
            "tenant_id": "tenant-foundation-golden",
            "project_id": "project-foundation-golden",
            "document_id": "document-foundation-golden",
            "subsection_id": "subsection-foundation-golden-0001",
            "task_id": "task-foundation-golden",
        },
        "source": {
            "authorization": "internal_generated_regression_artifact",
            "path": EXPECTED_SOURCE_PATH,
            "sha256": source_sha256.upper(),
        },
        "subsection": {
            "body": body,
            "body_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
            "character_count": len(body),
            "heading": heading,
            "ordinal": ordinal,
        },
        "outline": {
            "sections": [
                {
                    "ordinal": ordinal,
                    "title": heading.split("：", 1)[-1],
                    "summary": "林晚在凌晨观察野面包店，并第一次与面包师周野相遇。",
                }
            ]
        },
        "style_profile": {
            "language": "zh-CN",
            "narrative_person": "third_person_limited",
            "tense": "past_narrative",
            "tone": ["quiet", "observational", "sensory"],
        },
        "initial_canonical_state": {
            "schema_version": "canonical-state-v0",
            "version_id": "state-foundation-genesis-v1",
            "revision": 0,
            "foundation_state_v0": {
                "ledger_events": [],
                "source_candidate_hash": None,
                "world_mutations": [],
            },
        },
        "handover": {
            "summary": "林晚在凌晨三点半发现野面包店并记下第一次观察。",
            "new_facts": [
                {
                    "predicate": "location.bakery.name",
                    "subject": "野面包",
                    "value": "野面包",
                }
            ],
            "arc_progress": [
                {
                    "arc_id": "arc-linwan-observer-to-participant",
                    "evidence": "林晚写下第一条观察笔记。",
                    "status": "done",
                }
            ],
        },
        "handover_expected_shape": {
            "required_fields": ["summary", "new_facts", "arc_progress"],
            "arc_progress_statuses": ["done", "deviated"],
        },
    }
    findings = _scan_for_secrets(payload)
    payload["secret_scan"] = {
        "contains_secret": bool(findings),
        "findings": findings,
        "scanner_version": "foundation-secret-scan-v1",
    }
    return payload


def build_from_path(source: Path) -> dict[str, Any]:
    raw = source.read_bytes()
    actual_sha256 = hashlib.sha256(raw).hexdigest().upper()
    if actual_sha256 != EXPECTED_SOURCE_SHA256:
        raise ValueError(
            f"golden source SHA-256 mismatch: expected {EXPECTED_SOURCE_SHA256}, "
            f"got {actual_sha256}"
        )
    return build_golden_slice(raw.decode("utf-8"), actual_sha256)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = build_from_path(args.source)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical_json_bytes(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
