# Testing Infrastructure Cleanup Design

## Status

Approved for implementation planning on 2026-08-20.

## Goal

Make the repository's default `pytest` invocation a CI-safe regression suite while preserving an explicit historical/artifact experiment verification entry point that continues to expose missing artifacts and stale freeze locks as real failures.

This cleanup changes test selection and audit infrastructure only. It does not regenerate historical artifacts, rewrite freeze locks, skip historical failures, or change production behavior.

## Current State

The repository currently has one pytest test path, `tests`, and no default marker filter. A repository-wide JUnit capture contains 124 verified pre-existing baseline failures:

- 98 tests depend on missing local or generated runtime/output/report artifacts;
- 26 tests expose committed freeze-lock/hash drift.

Those failures are distributed across 27 modules. Twenty-five of the modules also contain healthy tests, so directory-level or module-level exclusion would remove valid regression coverage.

The repository has no checked-in CI-provider workflow. CI safety therefore needs to be defined at the repository's default pytest entry point rather than in GitHub Actions or another provider-specific configuration.

## Decisions

### Default command

Plain `pytest` is the default CI-safe regression entry point. It excludes tests marked `historical_artifact`.

The explicit historical/artifact entry point is:

```powershell
& .\.venv\Scripts\python.exe -m pytest -o addopts= -m historical_artifact
```

Historical tests retain their natural behavior. Missing artifacts, stale hashes, and invalid locks remain failures; none are converted to skips or expected failures.

### Classification mechanism

A checked-in, versioned JSON manifest records exactly the 124 historical test node IDs. Each entry contains:

- `node_id`: canonical pytest node ID;
- `category`: either `missing_generated_artifact` or `frozen_hash_drift`;
- `reason`: concise human-readable classification evidence.

A pytest collection hook loads the manifest and adds the `historical_artifact` marker to matching collected items before marker selection runs. The hook is registered with `tryfirst=True` so the marker exists before pytest applies `-m` filtering.

This manifest-based design avoids edits to 124 historical tests and preserves healthy tests that share their modules.

### Manifest integrity

Manifest loading is fail-closed. Test collection fails when the manifest is missing, has an unsupported schema version, contains duplicate node IDs, uses an unknown category, or contains malformed entries.

For a default full collection with no explicit file or node selection, the hook verifies that all manifest node IDs were collected. A renamed or removed historical test therefore cannot silently escape classification. Targeted pytest invocations do not require all 124 entries to be present, but still classify any selected matching node IDs.

### Freeze-lock audit

A separate checked-in registry describes each audited freeze binding with:

- a stable audit ID;
- the lock JSON path;
- the JSON field path containing the expected SHA-256;
- the source artifact path whose bytes are hashed.

The audit command is read-only. It computes source hashes and reports each binding as `fresh`, `stale`, or `missing`. It never updates a lock or generates an artifact.

Exit codes are:

- `0`: every registered binding is fresh;
- `1`: at least one registered source or lock is stale or missing;
- `2`: registry or lock data is structurally invalid.

The real audit is explicit and is not part of default pytest, because current stale locks are known historical findings. Unit tests for the audit engine use synthetic temporary files and are part of the CI-safe suite.

## Components

### Suite manifest

`tests/test_suite_manifest.json` owns historical test classification. The initial contents are derived from the completed 124-row baseline differential proof, not from directory naming heuristics.

### Selection library

`tests/support/test_suite_selection.py` owns manifest parsing, validation, node-ID classification, and full-collection completeness checks. It contains no pytest-global mutation, allowing the behavior to be tested with ordinary unit tests.

### Pytest hook and configuration

`tests/conftest.py` calls the selection library during collection. `pyproject.toml` registers the `historical_artifact` marker and sets the default marker expression to `not historical_artifact`.

### Freeze audit registry and engine

`tests/freeze_lock_registry.json` stores explicit lock/source bindings. `scripts/audit_freeze_locks.py` loads the registry, calculates hashes, renders a stable summary, and returns the documented exit code.

### Documentation

`docs/testing.md` documents the default suite, the explicit historical suite, the freeze-lock audit, expected failure semantics, and the rule that historical findings must not be repaired as part of unrelated feature work.

## Data Flow

For default pytest:

1. pytest discovers all tests under `tests`;
2. the collection hook validates the suite manifest;
3. matching items receive `historical_artifact`;
4. full default collection verifies all 124 manifest entries were observed;
5. pytest applies `-m "not historical_artifact"` and runs the CI-safe remainder.

For historical verification:

1. the operator clears the default addopts and selects `historical_artifact`;
2. the same hook and manifest validation run;
3. pytest executes only the historical items;
4. missing artifacts and stale hashes remain visible as failures.

For freeze-lock audit:

1. the audit engine validates the registry;
2. each lock JSON is loaded and its expected hash field resolved;
3. the corresponding source bytes are hashed;
4. the engine emits deterministic per-binding status and an aggregate result;
5. no repository file is modified.

## Error Handling

- Manifest structural errors stop collection with a concise pytest usage error identifying the manifest problem.
- Full-collection drift reports missing manifest node IDs and stops before executing tests.
- A targeted invocation never fails merely because unrelated manifest items were not selected.
- Audit registry structural errors return `2` and identify the invalid audit ID or field.
- Missing lock/source paths return `1` with `missing` status.
- Hash mismatches return `1` with both expected and actual SHA-256 values.
- Audit output must not include file contents or secrets.

## Verification Strategy

Infrastructure tests must prove:

- the checked-in manifest has schema version 1, exactly 124 unique node IDs, 98 missing-artifact entries, and 26 hash-drift entries;
- malformed, duplicate, or unknown-category entries fail closed;
- matching items receive `historical_artifact` and unmatched items do not;
- default selection excludes historical items while a cleared-addopts historical selection includes them;
- full collection detects stale manifest node IDs, while targeted collection permits unselected entries;
- synthetic fresh, stale, missing, and malformed freeze-lock cases produce exit codes `0`, `1`, `1`, and `2` respectively;
- the real audit is read-only and reports the current findings without rewriting any lock.

Implementation follows test-driven development: each selection or audit behavior receives a failing focused test before its minimal implementation.

## Acceptance Criteria

- Plain `pytest` selects the CI-safe regression suite and does not select any of the 124 historical items.
- The explicit historical command selects exactly the 124 manifest items and preserves their failure semantics.
- Healthy tests in the 25 mixed modules remain in the default suite.
- Manifest drift cannot silently move a historical test into the default suite during full collection.
- The freeze-lock audit reports current stale/missing findings without modifying repository files.
- No historical artifact is generated and no historical lock or production source is changed.
- No provider-specific CI workflow is introduced.

## Non-Goals

- Making the historical/artifact suite green.
- Reconstructing missing runtime, output, sealed, reviewer, or report artifacts.
- Regenerating, approving, or deleting stale freeze locks.
- Fixing unrelated deferred test hygiene issues.
- Adding GitHub Actions, another CI provider, or deployment automation.
