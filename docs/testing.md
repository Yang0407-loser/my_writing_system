# Testing

## Default CI-safe regression suite

The repository's default pytest configuration excludes tests classified as
`historical_artifact`:

```powershell
& .\.venv\Scripts\python.exe -m pytest
```

This is the command a future CI provider should use. The repository does not
currently prescribe GitHub Actions or another provider-specific workflow.

The historical classification is exact, not directory-based. Healthy tests
that share a module with historical verification remain in the default suite.

## Historical/artifact verification

Run the historical suite only through the explicit marker entry point:

```powershell
& .\.venv\Scripts\python.exe -m pytest -o addopts= -m historical_artifact
```

This suite contains the 124 tests from the verified pre-existing baseline:

- 98 require missing local/generated artifacts;
- 26 verify committed freeze hashes or locks that are currently stale.

Failures remain real. The historical entry point does not turn missing
artifacts or stale locks into skips or expected failures. Feature work must not
regenerate artifacts, rewrite locks, or change production sources merely to
make this suite green.

To inspect selection without executing test bodies:

```powershell
& .\.venv\Scripts\python.exe -m pytest --collect-only -q
& .\.venv\Scripts\python.exe -m pytest -o addopts= -m historical_artifact --collect-only -q
```

The first command must deselect all 124 historical items. The second must
select exactly the 124 node IDs in `tests/test_suite_manifest.json`.

## Freeze-lock audit

Run the read-only audit explicitly:

```powershell
& .\.venv\Scripts\python.exe scripts\audit_freeze_locks.py
```

Use `--json` for machine-readable output. The registry is
`tests/freeze_lock_registry.json`; each entry binds one lock JSON field to one
source file. The audit reads and hashes bytes but never updates locks or
generates artifacts.

Exit codes:

- `0`: all registered bindings are fresh;
- `1`: one or more bindings are stale or missing;
- `2`: the registry or a lock is structurally invalid.

An exit code of `1` from the current historical audit is an explicit finding,
not a default CI regression. Resolve those findings only in a dedicated
historical experiment maintenance task.

## Focused development tests

The default marker still applies when a test path is supplied. To run test
infrastructure contracts themselves without inheriting future default addopts,
use a focused override:

```powershell
& .\.venv\Scripts\python.exe -m pytest -o addopts= `
  tests\unit\test_test_suite_selection.py `
  tests\infrastructure\test_pytest_suite_plugin.py `
  tests\unit\test_audit_freeze_locks.py -q
```
