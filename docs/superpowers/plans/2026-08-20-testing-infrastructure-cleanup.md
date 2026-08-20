# Testing Infrastructure Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make plain `pytest` select only the CI-safe regression suite while preserving an explicit 124-test historical/artifact suite and a read-only stale freeze-lock audit.

**Architecture:** A checked-in node-ID manifest classifies the 124 verified baseline failures without editing their test bodies. A pytest plugin marks those items before marker filtering, while a separate registry-driven audit computes freeze-lock status without mutating locks or generating artifacts.

**Tech Stack:** Python 3.11, pytest 8, pytester, JSON, SHA-256, PowerShell verification commands.

## Global Constraints

- Do not repair, skip, regenerate, or reclassify the 124 historical failures.
- Plain `pytest` must exclude `historical_artifact`; the explicit historical entry point must preserve real failure semantics.
- Do not add a provider-specific CI workflow.
- Do not run repository-wide pytest during implementation; use focused tests and full `--collect-only` selection verification.
- Preserve unrelated working-tree files and stage only files named by each task.
- Every behavioral change follows RED → GREEN TDD.

---

## File Structure

- `tests/test_suite_manifest.json`: authoritative versioned classification of the 124 historical pytest node IDs.
- `tests/support/__init__.py`: package boundary for test infrastructure.
- `tests/support/test_suite_selection.py`: pure manifest parsing and validation.
- `tests/support/pytest_suite_plugin.py`: pytest collection hook that marks and verifies historical items.
- `tests/unit/test_test_suite_selection.py`: loader and checked-in-manifest contracts.
- `tests/infrastructure/test_pytest_suite_plugin.py`: real pytester selection behavior.
- `tests/freeze_lock_registry.json`: explicit lock JSON pointer/source bindings.
- `scripts/audit_freeze_locks.py`: read-only freeze-lock audit engine and CLI.
- `tests/unit/test_audit_freeze_locks.py`: synthetic audit behavior.
- `docs/testing.md`: operator commands and failure semantics.
- `.superpowers/sdd/2026-08-15-phase0-writing-e2e/backlog.md`: mark the cleanup backlog item complete without reopening Phase 0.

---

### Task 1: Versioned historical suite manifest and loader

**Files:**
- Create: `tests/test_suite_manifest.json`
- Create: `tests/support/__init__.py`
- Create: `tests/support/test_suite_selection.py`
- Create: `tests/unit/test_test_suite_selection.py`

**Interfaces:**
- Produces: `ManifestError`, `HistoricalTestEntry`, `HistoricalTestManifest`, `load_manifest(path: Path) -> HistoricalTestManifest`, and `missing_node_ids(manifest, collected_node_ids) -> tuple[str, ...]`.
- Consumes: the completed 124-row `.superpowers/sdd/2026-08-15-phase0-writing-e2e/baseline-differential-proof.csv` only as one-time source data; runtime code must not depend on `.superpowers` evidence.

- [ ] **Step 1: Write loader RED tests**

Create `tests/unit/test_test_suite_selection.py` with literal fixtures that prove the following breaks are caught: accepting duplicate IDs, unknown categories, unsupported schema versions, and silently losing a checked-in historical node ID.

```python
import json
from pathlib import Path

import pytest

from tests.support.test_suite_selection import (
    ManifestError,
    load_manifest,
    missing_node_ids,
)


def _write(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_manifest_rejects_duplicate_node_ids(tmp_path):
    entry = {
        "node_id": "tests/unit/test_sample.py::test_case",
        "category": "missing_generated_artifact",
        "reason": "missing sealed output",
    }
    path = _write(tmp_path / "manifest.json", {"schema_version": 1, "entries": [entry, entry]})

    with pytest.raises(ManifestError, match="duplicate node_id"):
        load_manifest(path)


def test_manifest_rejects_unknown_category(tmp_path):
    path = _write(tmp_path / "manifest.json", {
        "schema_version": 1,
        "entries": [{
            "node_id": "tests/unit/test_sample.py::test_case",
            "category": "legacy",
            "reason": "ambiguous category",
        }],
    })

    with pytest.raises(ManifestError, match="unknown category"):
        load_manifest(path)


def test_missing_node_ids_returns_sorted_manifest_drift(tmp_path):
    path = _write(tmp_path / "manifest.json", {
        "schema_version": 1,
        "entries": [
            {"node_id": "tests/unit/test_b.py::test_b", "category": "frozen_hash_drift", "reason": "stale lock"},
            {"node_id": "tests/unit/test_a.py::test_a", "category": "missing_generated_artifact", "reason": "missing output"},
        ],
    })
    manifest = load_manifest(path)

    assert missing_node_ids(manifest, {"tests/unit/test_b.py::test_b"}) == (
        "tests/unit/test_a.py::test_a",
    )
```

- [ ] **Step 2: Run RED**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\unit\test_test_suite_selection.py -q
```

Expected: collection fails because `tests.support.test_suite_selection` does not exist.

- [ ] **Step 3: Implement the minimal loader**

Implement immutable dataclasses, require `schema_version == 1`, require a non-empty list of objects, validate non-empty node ID/reason strings, allow only `missing_generated_artifact` and `frozen_hash_drift`, reject duplicates, and return missing IDs in sorted order.

- [ ] **Step 4: Verify loader GREEN**

Run the focused test again. Expected: all loader tests pass.

- [ ] **Step 5: Build and add the authoritative manifest**

Mechanically convert each baseline CSV `test_id` from dotted classname form to pytest path form:

```text
tests.unit.test_writer_boundary_v12_r2::test_name
→ tests/unit/test_writer_boundary_v12_r2.py::test_name
```

Set `reason` to the CSV `proof_group`, sort entries by `node_id`, and write schema version 1. Add a checked-in-manifest test with hand-derived literal counts:

```python
def test_checked_in_manifest_preserves_verified_baseline_partition():
    manifest = load_manifest(Path("tests/test_suite_manifest.json"))
    counts = {category: 0 for category in (
        "missing_generated_artifact",
        "frozen_hash_drift",
    )}
    for entry in manifest.entries:
        counts[entry.category] += 1

    assert len(manifest.entries) == 124
    assert counts == {
        "missing_generated_artifact": 98,
        "frozen_hash_drift": 26,
    }
```

- [ ] **Step 6: Verify manifest GREEN and commit**

Run the focused unit test. Then stage only the four Task 1 files and commit:

```powershell
git add tests/test_suite_manifest.json tests/support/__init__.py tests/support/test_suite_selection.py tests/unit/test_test_suite_selection.py
git commit -m "test: classify historical artifact verification"
```

---

### Task 2: Pytest default and explicit suite selection

**Files:**
- Create: `tests/support/pytest_suite_plugin.py`
- Create: `tests/infrastructure/test_pytest_suite_plugin.py`
- Modify: `tests/conftest.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Consumes: `load_manifest()` and `missing_node_ids()` from Task 1.
- Produces: pytest marker `historical_artifact` and a `tryfirst=True` `pytest_collection_modifyitems(config, items)` hook.

- [ ] **Step 1: Write pytester RED tests**

Enable the built-in pytester plugin in `tests/infrastructure/test_pytest_suite_plugin.py`. Build a temporary project with one historical node and one safe node, a schema-version-1 manifest, and the real plugin. Prove these observable behaviors:

```python
pytest_plugins = ["pytester"]


def test_default_selection_deselects_only_manifest_item(pytester):
    # Temporary pyproject sets addopts = '-m "not historical_artifact"'.
    # Manifest contains test_sample.py::test_historical only.
    result = pytester.runpytest("-q")
    result.assert_outcomes(passed=1, deselected=1)


def test_explicit_historical_selection_runs_manifest_item(pytester):
    result = pytester.runpytest("-o", "addopts=", "-m", "historical_artifact", "-q")
    result.assert_outcomes(passed=1, deselected=1)


def test_full_collection_fails_when_manifest_node_is_missing(pytester):
    result = pytester.runpytest("-q")
    result.stderr.fnmatch_lines(["*historical manifest drift*test_removed*"])
    assert result.ret != 0


def test_targeted_selection_does_not_require_unselected_manifest_nodes(pytester):
    result = pytester.runpytest("test_sample.py::test_safe", "-q")
    result.assert_outcomes(passed=1)
```

The temporary conftest must load `tests.support.pytest_suite_plugin`, and `pytester.syspathinsert()` must make the repository implementation importable. These tests exercise real pytest collection and deselection rather than mocks.

- [ ] **Step 2: Run RED**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest -o addopts= tests\infrastructure\test_pytest_suite_plugin.py -q
```

Expected: tests fail because the plugin is absent and historical items are not marked.

- [ ] **Step 3: Implement minimal plugin and default config**

The plugin loads `<config.rootpath>/tests/test_suite_manifest.json`, maps entries by exact node ID, adds `pytest.mark.historical_artifact` to matching items, and converts `ManifestError` to `pytest.UsageError`. If `config.option.file_or_dir` is empty, compare all pre-deselection item node IDs to the manifest and raise `pytest.UsageError` with the sorted missing IDs.

Register the plugin in `tests/conftest.py`:

```python
pytest_plugins = ["tests.support.pytest_suite_plugin"]
```

Update `pyproject.toml`:

```toml
addopts = '-m "not historical_artifact"'
markers = [
    "historical_artifact: requires historical/generated artifacts or verifies frozen experimental hashes; excluded from the default CI-safe suite",
    "postgres: requires TEST_CANONICAL_DATABASE_URL and a real PostgreSQL test database",
]
```

- [ ] **Step 4: Verify plugin GREEN**

Run the pytester file and Task 1 unit tests with `-o addopts=`. Expected: all pass.

- [ ] **Step 5: Verify real repository selection without executing the suites**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest --collect-only -q
& .\.venv\Scripts\python.exe -m pytest -o addopts= -m historical_artifact --collect-only -q
```

Expected:

- default collection reports 124 deselected and selects no manifest node;
- explicit historical collection selects exactly 124 items;
- neither command executes test bodies.

- [ ] **Step 6: Commit**

Stage only Task 2 files and commit:

```powershell
git add pyproject.toml tests/conftest.py tests/support/pytest_suite_plugin.py tests/infrastructure/test_pytest_suite_plugin.py
git commit -m "test: make default pytest CI safe"
```

---

### Task 3: Read-only freeze-lock audit

**Files:**
- Create: `tests/freeze_lock_registry.json`
- Create: `scripts/audit_freeze_locks.py`
- Create: `tests/unit/test_audit_freeze_locks.py`

**Interfaces:**
- Produces: `AuditConfigurationError`, `FreezeBinding`, `BindingResult`, `AuditReport`, `load_registry(path: Path)`, `audit_registry(root: Path, registry_path: Path) -> AuditReport`, and `main(argv: Sequence[str] | None = None) -> int`.
- Exit codes: 0 all fresh, 1 any stale/missing, 2 invalid registry/lock structure.

- [ ] **Step 1: Write audit RED tests**

Use real temporary JSON and source files, not mocks. Each expected hash is a hand-derived SHA-256 literal.

```python
import hashlib
import json
from pathlib import Path

from scripts.audit_freeze_locks import audit_registry, main


def _write_registry(root: Path, *, expected_pointer: str) -> Path:
    registry = root / "registry.json"
    registry.write_text(json.dumps({
        "schema_version": 1,
        "bindings": [{
            "audit_id": "sample",
            "lock_path": "lock.json",
            "expected_sha256_pointer": expected_pointer,
            "source_path": "source.txt",
        }],
    }), encoding="utf-8")
    return registry


def test_audit_reports_fresh_binding(tmp_path):
    source = tmp_path / "source.txt"
    source.write_bytes(b"stable")
    expected = hashlib.sha256(b"stable").hexdigest()
    (tmp_path / "lock.json").write_text(json.dumps({"sha256": expected}), encoding="utf-8")
    registry = _write_registry(tmp_path, expected_pointer="/sha256")

    report = audit_registry(tmp_path, registry)

    assert report.exit_code == 0
    assert report.results[0].status == "fresh"


def test_audit_reports_stale_without_rewriting_lock(tmp_path):
    source = tmp_path / "source.txt"
    source.write_bytes(b"changed")
    lock = tmp_path / "lock.json"
    original = b'{"sha256":"0000"}'
    lock.write_bytes(original)
    registry = _write_registry(tmp_path, expected_pointer="/sha256")

    report = audit_registry(tmp_path, registry)

    assert report.exit_code == 1
    assert report.results[0].status == "stale"
    assert lock.read_bytes() == original


def test_audit_reports_missing_as_exit_one(tmp_path):
    registry = _write_registry(tmp_path, expected_pointer="/sha256")
    report = audit_registry(tmp_path, registry)
    assert report.exit_code == 1
    assert report.results[0].status == "missing"


def test_invalid_json_pointer_returns_configuration_exit_two(tmp_path):
    (tmp_path / "source.txt").write_bytes(b"stable")
    (tmp_path / "lock.json").write_text('{"sha256":"0000"}', encoding="utf-8")
    registry = _write_registry(tmp_path, expected_pointer="/unknown")

    assert main(["--root", str(tmp_path), "--registry", str(registry)]) == 2
```

- [ ] **Step 2: Run RED**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\unit\test_audit_freeze_locks.py -q
```

Expected: collection fails because `scripts.audit_freeze_locks` does not exist.

- [ ] **Step 3: Implement audit engine and CLI**

Implement JSON Pointer traversal for object keys and numeric list indexes, SHA-256 over source bytes, immutable result models, deterministic audit-ID ordering, text output by default, and `--json` output without file contents. Catch only structural configuration errors in `main()` and return 2; stale/missing are normal report results and return 1.

- [ ] **Step 4: Add the ten real bindings**

Create schema-version-1 registry entries for:

1. R1 protocol design lock;
2. WR1E evaluator source freeze manifest;
3. style baseline registry;
4. WR2B ontology validator;
5. WR2B layered extractor;
6. WR2B development runner;
7. WR2A adversarial fixture;
8. WR2A adversarial extractor;
9. WR2A adversarial validator;
10. WR2A holdout source fixture.

Use the exact lock/source paths and JSON fields documented in the approved design evidence. Do not encode the current actual hashes in the registry and do not alter expected hashes in existing locks.

- [ ] **Step 5: Verify GREEN and current read-only finding**

Run focused unit tests. Then capture hashes of all registered lock files, run the real audit, require exit code 1, and recalculate the lock hashes to prove byte identity:

```powershell
& .\.venv\Scripts\python.exe scripts\audit_freeze_locks.py
if ($LASTEXITCODE -ne 1) { exit 1 }
```

Expected: current stale findings are reported; no lock changes appear in `git diff`.

- [ ] **Step 6: Commit**

Stage only the three Task 3 files and commit:

```powershell
git add tests/freeze_lock_registry.json scripts/audit_freeze_locks.py tests/unit/test_audit_freeze_locks.py
git commit -m "test: audit historical freeze locks"
```

---

### Task 4: Operator documentation and closure verification

**Files:**
- Create: `docs/testing.md`
- Modify: `.superpowers/sdd/2026-08-15-phase0-writing-e2e/backlog.md`
- Modify: `.superpowers/sdd/2026-08-15-phase0-writing-e2e/progress.md`

**Interfaces:**
- Consumes: default marker configuration, historical command, and audit CLI from Tasks 2-3.
- Produces: a single operator-facing testing guide and closure evidence.

- [ ] **Step 1: Write operator documentation**

Document exactly these commands and semantics:

```powershell
# Default CI-safe regression suite
& .\.venv\Scripts\python.exe -m pytest

# Historical/artifact verification; failures remain real
& .\.venv\Scripts\python.exe -m pytest -o addopts= -m historical_artifact

# Read-only freeze-lock audit
& .\.venv\Scripts\python.exe scripts\audit_freeze_locks.py
```

State that feature work must not regenerate artifacts or rewrite locks merely to make the historical suite green.

- [ ] **Step 2: Run focused regression verification**

Run only infrastructure tests and a representative healthy test from a mixed module:

```powershell
& .\.venv\Scripts\python.exe -m pytest -o addopts= tests\unit\test_test_suite_selection.py tests\infrastructure\test_pytest_suite_plugin.py tests\unit\test_audit_freeze_locks.py -q
& .\.venv\Scripts\python.exe -m pytest tests\unit\test_writer_boundary_v12_r2.py::test_r2_has_no_model_client_or_generation_call -q
```

- [ ] **Step 3: Repeat collect-only acceptance**

Run both full collect-only commands from Task 2. Do not execute repository-wide pytest or the historical suite.

- [ ] **Step 4: Verify scope**

Confirm:

- manifest count is 124 with 98/26 partition;
- default selected set has zero overlap with the manifest;
- historical collect-only selected set equals the manifest;
- audit exits 1 for current stale findings and changes no lock bytes;
- no production file, historical lock, or historical artifact is modified;
- unrelated pre-existing working-tree files remain unstaged.

- [ ] **Step 5: Close backlog and commit docs**

Mark `Testing infrastructure cleanup` complete and record verification commands/results in the ledger. Stage only `docs/testing.md`; the `.superpowers` ledger/backlog remain local SDD evidence if ignored by repository policy.

```powershell
git add docs/testing.md
git commit -m "docs: document CI-safe and historical test suites"
```

- [ ] **Step 6: Final review**

Review the exact implementation diff against `docs/superpowers/specs/2026-08-20-testing-infrastructure-cleanup-design.md`. Run `git diff --check` and the focused verification commands again before claiming completion.
