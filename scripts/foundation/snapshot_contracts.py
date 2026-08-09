"""Create deterministic pre-Foundation external contract snapshots."""

from __future__ import annotations

import argparse
import inspect
import json
from pathlib import Path
from typing import Any


_NON_SEMANTIC_KEYS = {"created_at", "generated_at", "snapshot_time", "timestamp"}
_TYPE_SCHEMA_KEYS = {"$ref", "additionalProperties", "anyOf", "items", "oneOf", "type"}


def canonical_json_bytes(payload: dict[str, Any]) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _normalize(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _normalize(nested)
            for key, nested in sorted(value.items())
            if key not in _NON_SEMANTIC_KEYS
        }
    if isinstance(value, list):
        return [_normalize(nested) for nested in value]
    return value


def build_openapi_snapshot(fastapi_app) -> dict[str, Any]:
    """Return normalized OpenAPI without runtime or environment values."""
    return {
        "schema_version": "openapi-pre-foundation-v0",
        "openapi": _normalize(fastapi_app.openapi()),
    }


def _default_contract(parameter: inspect.Parameter) -> dict[str, Any]:
    if parameter.default is inspect.Parameter.empty:
        return {"required": True}
    default = parameter.default
    if default is None or isinstance(default, (str, int, float, bool)):
        serialized = default
    else:
        serialized = repr(default)
    return {"default": serialized, "required": False}


def _signature_contract(callable_obj) -> dict[str, Any]:
    signature = inspect.signature(callable_obj)
    parameters = []
    for parameter in signature.parameters.values():
        if parameter.name == "self":
            continue
        parameters.append(
            {
                "annotation": inspect.formatannotation(parameter.annotation),
                "kind": parameter.kind.name,
                "name": parameter.name,
                **_default_contract(parameter),
            }
        )
    return {
        "parameters": parameters,
        "return_annotation": inspect.formatannotation(signature.return_annotation),
    }


def _type_contract(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _type_contract(nested)
            for key, nested in sorted(value.items())
            if key in _TYPE_SCHEMA_KEYS
        }
    if isinstance(value, list):
        return [_type_contract(nested) for nested in value]
    return value


def _model_contract(model) -> dict[str, Any]:
    schema = model.model_json_schema()
    required = set(schema.get("required", []))
    return {
        "fields": {
            name: {
                "required": name in required,
                "type": _type_contract(property_schema),
            }
            for name, property_schema in sorted(schema.get("properties", {}).items())
        }
    }


def build_writer_snapshot(writer_cls, task_status_model, final_result_model) -> dict[str, Any]:
    """Freeze Writer.run and the existing task response envelope contracts."""
    return {
        "schema_version": "writer-pre-foundation-v0",
        "writer_run": _signature_contract(writer_cls.run),
        "task_response_contracts": {
            "status": _model_contract(task_status_model),
            "completed_result": _model_contract(final_result_model),
            "pending_result": {
                "fields": {
                    "message": {"required": True, "type": {"type": "string"}},
                    "status": {"required": True, "type": {"type": "string"}},
                    "task_id": {"required": True, "type": {"type": "string"}},
                }
            },
            "history_list": {
                "fields": {
                    "tasks": {"required": True, "type": {"type": "array"}},
                    "total": {"required": True, "type": {"type": "integer"}},
                }
            },
            "history_detail": {
                "fields": {
                    "task": {"required": True, "type": {"type": "object"}}
                }
            },
        },
    }


def _assert_snapshot_safe(payload: dict[str, Any]) -> None:
    serialized = canonical_json_bytes(payload).decode("utf-8").casefold()
    forbidden = ('"api_key"', "bearer ", "e:\\writer", "c:\\users")
    matches = [needle for needle in forbidden if needle in serialized]
    if matches:
        raise ValueError(f"unsafe contract snapshot content: {matches}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--openapi-output", type=Path, required=True)
    parser.add_argument("--writer-output", type=Path, required=True)
    args = parser.parse_args()

    from app.agents.writer import Writer
    from app.main import app
    from app.models import FinalResult, TaskStatus

    openapi = build_openapi_snapshot(app)
    writer = build_writer_snapshot(Writer, TaskStatus, FinalResult)
    _assert_snapshot_safe(openapi)
    _assert_snapshot_safe(writer)
    args.openapi_output.parent.mkdir(parents=True, exist_ok=True)
    args.writer_output.parent.mkdir(parents=True, exist_ok=True)
    args.openapi_output.write_bytes(canonical_json_bytes(openapi))
    args.writer_output.write_bytes(canonical_json_bytes(writer))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
