from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .models import SharedDecisionContract


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def load_contract(path: Path) -> SharedDecisionContract:
    return SharedDecisionContract.model_validate(
        json.loads(path.read_text(encoding="utf-8"))
    )


def contract_payload(contract: SharedDecisionContract) -> dict[str, Any]:
    return contract.model_dump()


def contract_hash(contract: SharedDecisionContract) -> str:
    return canonical_hash(contract_payload(contract))


def allowed_values(contract: SharedDecisionContract) -> list[str]:
    return [item.value for item in contract.allowed_values]


def validate_observed_solution(value: str, contract: SharedDecisionContract) -> str:
    if value not in {*allowed_values(contract), "unclear", "other"}:
        raise ValueError("observed solution must be allowed, unclear, or other")
    return value

