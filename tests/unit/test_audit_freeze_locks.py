import json
from pathlib import Path

from scripts.audit_freeze_locks import audit_registry, load_registry, main


STABLE_SHA256 = "f379ccb92b9116442dc65bdc35648a85d3786b34779db7f704a901fa07b00cb6"
CHANGED_SHA256 = "d67e2e944994496c8d8ec76eed0cf9f09679448d584b532bebf941852a37f5ed"


def _write_registry(
    root: Path,
    *,
    expected_pointer: str = "/sha256",
    bindings: list[dict[str, str]] | None = None,
) -> Path:
    if bindings is None:
        bindings = [
            {
                "audit_id": "sample",
                "lock_path": "lock.json",
                "expected_sha256_pointer": expected_pointer,
                "source_path": "source.txt",
            }
        ]
    registry = root / "registry.json"
    registry.write_text(
        json.dumps({"schema_version": 1, "bindings": bindings}),
        encoding="utf-8",
    )
    return registry


def test_audit_reports_fresh_binding(tmp_path):
    (tmp_path / "source.txt").write_bytes(b"stable")
    (tmp_path / "lock.json").write_text(
        json.dumps({"sha256": STABLE_SHA256}),
        encoding="utf-8",
    )
    registry = _write_registry(tmp_path)

    report = audit_registry(tmp_path, registry)

    assert report.exit_code == 0
    assert len(report.results) == 1
    result = report.results[0]
    assert result.audit_id == "sample"
    assert result.status == "fresh"
    assert result.expected_sha256 == STABLE_SHA256
    assert result.actual_sha256 == STABLE_SHA256


def test_audit_reports_stale_without_rewriting_lock(tmp_path):
    (tmp_path / "source.txt").write_bytes(b"changed")
    lock = tmp_path / "lock.json"
    original = b'{"sha256":"0000000000000000000000000000000000000000000000000000000000000000"}'
    lock.write_bytes(original)
    registry = _write_registry(tmp_path)

    report = audit_registry(tmp_path, registry)

    assert report.exit_code == 1
    result = report.results[0]
    assert result.status == "stale"
    assert result.actual_sha256 == CHANGED_SHA256
    assert lock.read_bytes() == original


def test_audit_reports_missing_source_as_exit_one(tmp_path):
    (tmp_path / "lock.json").write_text(
        json.dumps({"sha256": STABLE_SHA256}),
        encoding="utf-8",
    )
    registry = _write_registry(tmp_path)

    report = audit_registry(tmp_path, registry)

    assert report.exit_code == 1
    assert report.results[0].status == "missing"
    assert report.results[0].detail == "source file is missing"


def test_invalid_json_pointer_returns_configuration_exit_two(tmp_path, capsys):
    (tmp_path / "source.txt").write_bytes(b"stable")
    (tmp_path / "lock.json").write_text(
        json.dumps({"sha256": STABLE_SHA256}),
        encoding="utf-8",
    )
    registry = _write_registry(tmp_path, expected_pointer="/unknown")

    exit_code = main(
        ["--root", str(tmp_path), "--registry", str(registry)]
    )

    assert exit_code == 2
    assert "cannot resolve JSON pointer /unknown" in capsys.readouterr().err


def test_registry_rejects_duplicate_audit_ids(tmp_path, capsys):
    binding = {
        "audit_id": "duplicate",
        "lock_path": "lock.json",
        "expected_sha256_pointer": "/sha256",
        "source_path": "source.txt",
    }
    registry = _write_registry(tmp_path, bindings=[binding, binding])

    exit_code = main(
        ["--root", str(tmp_path), "--registry", str(registry)]
    )

    assert exit_code == 2
    assert "duplicate audit_id" in capsys.readouterr().err


def test_json_output_is_sorted_and_does_not_include_source_contents(
    tmp_path,
    capsys,
):
    (tmp_path / "a.txt").write_bytes(b"stable")
    (tmp_path / "b.txt").write_bytes(b"stable")
    (tmp_path / "lock.json").write_text(
        json.dumps({"a": STABLE_SHA256, "b": STABLE_SHA256}),
        encoding="utf-8",
    )
    registry = _write_registry(
        tmp_path,
        bindings=[
            {
                "audit_id": "z-last",
                "lock_path": "lock.json",
                "expected_sha256_pointer": "/b",
                "source_path": "b.txt",
            },
            {
                "audit_id": "a-first",
                "lock_path": "lock.json",
                "expected_sha256_pointer": "/a",
                "source_path": "a.txt",
            },
        ],
    )

    exit_code = main(
        [
            "--root",
            str(tmp_path),
            "--registry",
            str(registry),
            "--json",
        ]
    )

    output = capsys.readouterr().out
    payload = json.loads(output)
    assert exit_code == 0
    assert [item["audit_id"] for item in payload["results"]] == [
        "a-first",
        "z-last",
    ]
    assert "stable" not in output


def test_checked_in_registry_covers_known_freeze_bindings():
    bindings = load_registry(Path("tests/freeze_lock_registry.json"))

    assert [binding.audit_id for binding in bindings] == [
        "r1-protocol-design",
        "style-baseline",
        "wr1e-evaluator-source",
        "wr2a-adversarial-extractor",
        "wr2a-adversarial-fixture",
        "wr2a-adversarial-validator",
        "wr2a-holdout-source",
        "wr2b-development-runner",
        "wr2b-layered-extractor",
        "wr2b-ontology-validator",
    ]
