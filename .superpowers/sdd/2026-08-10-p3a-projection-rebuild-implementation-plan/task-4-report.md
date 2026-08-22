# Task 4 Report: Reconstruct stable Projection messages directly from Canon

## Status

Task 4 is complete. Canon is now sufficient to build incremental, rebuild, and bootstrap projection messages without requiring historical Outbox Envelopes. The compatibility projector consumes Canon-backed messages and returns deterministic receipts.

## RED evidence

### Initial replay API RED

After adding the parity, no-Envelope bootstrap, deterministic ledger, hash validation, as-of materialization, and legacy receipt tests, the exact focused command was run before production implementation:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\unit\canonical\test_projection_replay.py tests\unit\test_legacy_subsection_projection.py -q
```

Result:

```text
ERROR tests/unit/canonical/test_projection_replay.py
ERROR tests/unit/test_legacy_subsection_projection.py
ModuleNotFoundError: No module named 'app.canonical.projection_replay'
2 errors in 0.94s
```

This failed for the intended reason: the new Canon replay boundary did not exist.

### Compatibility facade RED

After the initial Green, a focused mutation check exposed that the synchronous Dispatcher still sends the historical compact payload. A test was added requiring the legacy facade to reconstruct trusted content from Canon references rather than demanding the new rich payload byte-for-byte:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\unit\test_legacy_subsection_projection.py::test_compatibility_message_payload_is_reconstructed_from_canon -q
```

Result:

```text
FAILED test_compatibility_message_payload_is_reconstructed_from_canon
LegacyProjectionError: analytics: message semantic content does not match Canon
1 failed in 1.97s
```

The minimal fix retained validation of semantic identity and Canon IDs while accepting the existing compact compatibility payload.

## Implemented behavior

- Added `CanonicalProjectionReplay.iter_messages(scope, projector_id, after_position, through_position)`.
  - Reads only tenant/project-scoped committed Commits, accepted Revisions, and matching commit-origin State Versions.
  - Orders messages by project stream position and Ledger entries by `(ordinal, id)`.
  - Recomputes Revision content hashes and State JSON hashes before yielding.
  - Builds a deterministic rich payload from Revision, State, and ordered Ledger content.
  - Uses `projection_event_id(projector_id, commit_id)` and leaves Envelope/Delivery IDs absent.
- Added `message_for_delivery(delivery_id)`.
  - Requires a scope-consistent Delivery/Envelope/Commit/Revision/State join.
  - Validates Envelope revision/state references and reconstructs the semantic payload from Canon.
  - Adds only the real `outbox_event_id` and `delivery_id`, preserving semantic parity with pure replay.
- Added `materialize_document_at(scope, stream_position)`.
  - Selects the latest accepted Revision per subsection whose committed stream position is at or before the requested point.
  - Orders subsections by canonical ordinal and never reads current subsection Heads.
- Moved `LegacyProjectionEnvelope` construction into the Canon replay reader while retaining the historical import from `legacy_subsection_projection` through re-export.
- Refactored `LegacySubsectionProjection.project()` to:
  - require projector scope and Canon-backed message identity;
  - avoid any Outbox query as proof of Canon;
  - preserve the current sink facade;
  - return deterministic `ProjectionReceipt` values, including stable Chroma record counts and content digests.
- No worker, lease, retry, dead-letter, attempt, partition-maintenance, or rebuild-run behavior was added.

## GREEN evidence

### Exact Task 4 focused suite

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\unit\canonical\test_projection_replay.py tests\unit\test_legacy_subsection_projection.py -q
```

```text
14 passed, 1 warning in 5.93s
```

The warning is the existing `jieba` dependency's `pkg_resources` deprecation warning.

### Broader Canon regression suite excluding known migration incompatibility

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\unit\canonical --ignore=tests\unit\canonical\test_schema_v0.py -q
```

```text
90 passed in 13.36s
```

### Adjacent Dispatcher compatibility regression

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\unit\canonical\test_outbox_dispatcher.py tests\unit\test_legacy_subsection_projection.py -q
```

```text
11 passed, 1 warning in 4.60s
```

### Ruff

```powershell
& .\.venv\Scripts\python.exe -m ruff check app\canonical\projection_replay.py app\writing\legacy_subsection_projection.py tests\unit\canonical\test_projection_replay.py tests\unit\test_legacy_subsection_projection.py
```

```text
All checks passed!
```

`git diff --check` also passed.

## Broader-suite concerns

The complete Canon unit suite reported `1 failed, 90 passed, 3 errors`. All four non-passing cases are confined to `tests/unit/canonical/test_schema_v0.py`: SQLite attempts to execute the PostgreSQL-only `DO $$` validation SQL in migration `0003_p3a_projection_backfill.py`. This is the previously documented Task 2 migration test debt and is unrelated to Task 4 files.

The adjacent golden projection integration module reported `9 passed, 1 failed`. Its sole failure occurs before projection execution because `test_deleted_derived_chunks_rebuild_identically_from_canon` still constructs `ProjectionMessage` with removed P2 fields `event_id` and `projection_name`, instead of using `CanonicalProjectionReplay.message_for_delivery()` or `iter_messages()`. The test is outside the Task 4 authorized file list and was not modified.

## Files changed

- `app/canonical/projection_replay.py`
- `app/writing/legacy_subsection_projection.py`
- `tests/unit/canonical/test_projection_replay.py`
- `tests/unit/test_legacy_subsection_projection.py`
- `.superpowers/sdd/2026-08-10-p3a-projection-rebuild-implementation-plan/task-4-report.md`

## Self-review

- **Canon authority:** replay and legacy envelope construction both require committed Commit, accepted Revision, matching State Version, tenant/project alignment, and recomputable Revision/State hashes.
- **No historical Envelope prerequisite:** pure replay never joins Outbox or Delivery and succeeds for an unenrolled `search_index` projector with no historical Envelope.
- **Stable semantic identity:** incremental and replay messages share `projection_event_id`, Commit ID, Revision/State refs, stream position, event type, and payload; only incremental messages carry Envelope/Delivery IDs.
- **Deterministic ordering:** Commits are ordered by `(stream_position, id)` and Ledger rows by `(ordinal, id)`; document subsections are ordered by `(ordinal, id)`.
- **Historical rendering:** materialization finds the newest Revision per subsection within the requested stream boundary; a three-commit test proves later current Heads do not leak into earlier output.
- **Compatibility:** the legacy facade accepts the synchronous Dispatcher's compact Canon-reference payload, reloads authoritative content from Canon, and returns identical receipts on replay.
- **Scope:** implementation remains within Task 4's replay and compatibility boundary; no scheduling or rebuild orchestration was introduced.

## Fix Round 1

### Findings addressed

1. `OutboxDispatcher` no longer duplicates compact `ProjectionMessage` construction. Its actual incremental production path delegates to `CanonicalProjectionReplay.message_for_delivery(delivery.id)`, so incremental and pure replay messages differ only in `outbox_event_id` and `delivery_id`.
2. `materialize_document_at()` now accepts optional keyword-only `document_id`. The two-positional-argument form remains valid for a single-document project. A multi-document project without `document_id` fails closed; an explicit ID must belong to tenant/project scope and restricts all subsection reads to that document.
3. The golden Chroma rebuild integration call site now obtains its message through `message_for_delivery()` and no longer constructs removed P2 aliases.

### RED evidence

Initial Fix Round command:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\unit\canonical\test_outbox_dispatcher.py::test_dispatcher_message_matches_pure_canon_replay_except_delivery_ids tests\unit\canonical\test_projection_replay.py::test_materialize_document_at_isolates_documents_and_rejects_ambiguity -q
```

Result:

```text
FF
```

- The real Dispatcher emitted the compact Envelope payload, so its complete message differed from pure Canon replay after excluding only the two transport IDs.
- The initial multi-document fixture had a duplicate keyword and was corrected without touching production code. The corrected test then produced the intended production RED:

```text
FAILED test_materialize_document_at_isolates_documents_and_rejects_ambiguity
Failed: DID NOT RAISE <class 'ValueError'>
```

The project-only materializer silently merged both documents instead of requiring an explicit document boundary.

### GREEN evidence

Focused Task 4, Dispatcher, and Legacy suite:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\unit\canonical\test_projection_replay.py tests\unit\canonical\test_outbox_dispatcher.py tests\unit\test_legacy_subsection_projection.py -q
```

```text
20 passed, 1 warning in 10.92s
```

Complete golden projection integration module:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\integration\canonical\test_golden_projection_failures.py -q
```

```text
10 passed, 1 warning in 4.23s
```

Broader Canon suite excluding the known Task 2 SQLite/PostgreSQL migration incompatibility:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\unit\canonical --ignore=tests\unit\canonical\test_schema_v0.py -q
```

```text
92 passed in 13.31s
```

Ruff across all Fix Round production and test files:

```powershell
& .\.venv\Scripts\python.exe -m ruff check app\canonical\projection_replay.py app\canonical\outbox.py app\writing\legacy_subsection_projection.py tests\unit\canonical\test_projection_replay.py tests\unit\canonical\test_outbox_dispatcher.py tests\unit\test_legacy_subsection_projection.py tests\integration\canonical\test_golden_projection_failures.py
```

```text
All checks passed!
```

`git diff --check` also passed.

### Files changed in Fix Round 1

- `app/canonical/outbox.py`
- `app/canonical/projection_replay.py`
- `tests/unit/canonical/test_outbox_dispatcher.py`
- `tests/unit/canonical/test_projection_replay.py`
- `tests/integration/canonical/test_golden_projection_failures.py`
- `.superpowers/sdd/2026-08-10-p3a-projection-rebuild-implementation-plan/task-4-report.md`

### Remaining concerns

- The Task 2 SQLite schema test debt remains unchanged and excluded from the passing broader gate.
- The existing `jieba` dependency continues to emit a `pkg_resources` deprecation warning in suites importing the legacy projector.
- No worker, lease, retry, dead-letter, attempt, or rebuild-run behavior was added.
