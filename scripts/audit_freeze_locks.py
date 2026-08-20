"""Read-only audit for historical SHA-256 freeze bindings."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Sequence


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REGISTRY = REPOSITORY_ROOT / "tests/freeze_lock_registry.json"
SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")


class AuditConfigurationError(ValueError):
    """Raised when the registry or one of its locks is structurally invalid."""


@dataclass(frozen=True, slots=True)
class FreezeBinding:
    audit_id: str
    lock_path: str
    expected_sha256_pointer: str
    source_path: str


@dataclass(frozen=True, slots=True)
class BindingResult:
    audit_id: str
    status: str
    lock_path: str
    source_path: str
    expected_sha256: str | None
    actual_sha256: str | None
    detail: str


@dataclass(frozen=True, slots=True)
class AuditReport:
    results: tuple[BindingResult, ...]

    @property
    def exit_code(self) -> int:
        return 0 if all(result.status == "fresh" for result in self.results) else 1

    def to_dict(self) -> dict[str, Any]:
        counts = {"fresh": 0, "stale": 0, "missing": 0}
        for result in self.results:
            counts[result.status] += 1
        return {
            "schema_version": 1,
            "exit_code": self.exit_code,
            "counts": counts,
            "results": [asdict(result) for result in self.results],
        }


def _required_string(raw: dict[str, object], field: str, index: int) -> str:
    value = raw.get(field)
    if not isinstance(value, str) or not value.strip():
        raise AuditConfigurationError(
            f"bindings[{index}].{field} must be a non-empty string"
        )
    return value


def load_registry(path: Path) -> tuple[FreezeBinding, ...]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AuditConfigurationError(f"cannot load registry {path}: {exc}") from exc

    if not isinstance(payload, dict):
        raise AuditConfigurationError("registry root must be an object")
    if payload.get("schema_version") != 1:
        raise AuditConfigurationError(
            f"unsupported schema_version: {payload.get('schema_version')!r}"
        )
    raw_bindings = payload.get("bindings")
    if not isinstance(raw_bindings, list) or not raw_bindings:
        raise AuditConfigurationError("bindings must be a non-empty list")

    bindings: list[FreezeBinding] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_bindings):
        if not isinstance(raw, dict):
            raise AuditConfigurationError(f"bindings[{index}] must be an object")
        binding = FreezeBinding(
            audit_id=_required_string(raw, "audit_id", index),
            lock_path=_required_string(raw, "lock_path", index),
            expected_sha256_pointer=_required_string(
                raw, "expected_sha256_pointer", index
            ),
            source_path=_required_string(raw, "source_path", index),
        )
        if binding.audit_id in seen:
            raise AuditConfigurationError(
                f"duplicate audit_id: {binding.audit_id}"
            )
        seen.add(binding.audit_id)
        bindings.append(binding)

    return tuple(sorted(bindings, key=lambda binding: binding.audit_id))


def _resolve_under_root(root: Path, relative_path: str, audit_id: str) -> Path:
    candidate = Path(relative_path)
    if candidate.is_absolute():
        raise AuditConfigurationError(
            f"{audit_id}: path must be repository-relative: {relative_path}"
        )
    resolved_root = root.resolve()
    resolved = (resolved_root / candidate).resolve()
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise AuditConfigurationError(
            f"{audit_id}: path escapes audit root: {relative_path}"
        ) from exc
    return resolved


def _resolve_json_pointer(value: object, pointer: str, audit_id: str) -> object:
    if not pointer.startswith("/"):
        raise AuditConfigurationError(
            f"{audit_id}: JSON pointer must start with '/': {pointer}"
        )
    current = value
    for encoded_token in pointer[1:].split("/"):
        token = encoded_token.replace("~1", "/").replace("~0", "~")
        try:
            if isinstance(current, dict):
                current = current[token]
            elif isinstance(current, list):
                current = current[int(token)]
            else:
                raise KeyError(token)
        except (KeyError, IndexError, ValueError) as exc:
            raise AuditConfigurationError(
                f"{audit_id}: cannot resolve JSON pointer {pointer}"
            ) from exc
    return current


def _load_expected_sha256(lock_path: Path, binding: FreezeBinding) -> str:
    try:
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AuditConfigurationError(
            f"{binding.audit_id}: cannot load lock {binding.lock_path}: {exc}"
        ) from exc
    expected = _resolve_json_pointer(
        payload,
        binding.expected_sha256_pointer,
        binding.audit_id,
    )
    if not isinstance(expected, str) or not SHA256_PATTERN.fullmatch(expected):
        raise AuditConfigurationError(
            f"{binding.audit_id}: expected SHA-256 is not 64 hexadecimal characters"
        )
    return expected.lower()


def _audit_binding(root: Path, binding: FreezeBinding) -> BindingResult:
    lock_path = _resolve_under_root(root, binding.lock_path, binding.audit_id)
    source_path = _resolve_under_root(root, binding.source_path, binding.audit_id)
    if not lock_path.is_file():
        return BindingResult(
            audit_id=binding.audit_id,
            status="missing",
            lock_path=binding.lock_path,
            source_path=binding.source_path,
            expected_sha256=None,
            actual_sha256=None,
            detail="lock file is missing",
        )

    expected = _load_expected_sha256(lock_path, binding)
    if not source_path.is_file():
        return BindingResult(
            audit_id=binding.audit_id,
            status="missing",
            lock_path=binding.lock_path,
            source_path=binding.source_path,
            expected_sha256=expected,
            actual_sha256=None,
            detail="source file is missing",
        )

    actual = hashlib.sha256(source_path.read_bytes()).hexdigest()
    status = "fresh" if actual == expected else "stale"
    return BindingResult(
        audit_id=binding.audit_id,
        status=status,
        lock_path=binding.lock_path,
        source_path=binding.source_path,
        expected_sha256=expected,
        actual_sha256=actual,
        detail="hashes match" if status == "fresh" else "hashes differ",
    )


def audit_registry(root: Path, registry_path: Path) -> AuditReport:
    bindings = load_registry(registry_path)
    return AuditReport(
        results=tuple(_audit_binding(root, binding) for binding in bindings)
    )


def _render_text(report: AuditReport) -> str:
    lines = []
    for result in report.results:
        line = f"{result.status.upper():7} {result.audit_id}: {result.detail}"
        if result.expected_sha256 is not None:
            line += f" expected={result.expected_sha256}"
        if result.actual_sha256 is not None:
            line += f" actual={result.actual_sha256}"
        lines.append(line)
    counts = report.to_dict()["counts"]
    lines.append(
        "SUMMARY "
        f"fresh={counts['fresh']} stale={counts['stale']} "
        f"missing={counts['missing']} exit={report.exit_code}"
    )
    return "\n".join(lines)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=REPOSITORY_ROOT)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--json", action="store_true", dest="as_json")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        report = audit_registry(args.root, args.registry)
    except AuditConfigurationError as exc:
        import sys

        print(f"freeze-lock audit configuration error: {exc}", file=sys.stderr)
        return 2

    if args.as_json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(_render_text(report))
    return report.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
