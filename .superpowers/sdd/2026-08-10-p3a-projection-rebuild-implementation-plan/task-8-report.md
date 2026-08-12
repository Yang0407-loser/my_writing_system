# Task 8 Report — Critical World, Handover and Chroma adapters

## Status

COMPLETE. Three `ProjectionAdapter` implementations now share the same
`apply` implementation for incremental delivery and future replay. No
generation/shadow rebuild path was added.

## RED evidence

1. Unit RED:

   ```powershell
   & .\.venv\Scripts\python.exe -m pytest tests\unit\projections\test_critical_projection_adapters.py -q
   ```

   Failed during collection with `ModuleNotFoundError: No module named
   'app.projections'`. An earlier sandbox invocation hit the uv trampoline
   permission boundary; that environment error was not counted as RED.

2. Real Chroma reopen RED:

   ```powershell
   & .\.venv\Scripts\python.exe -m pytest tests\integration\canonical\test_critical_projection_rebuild.py -q
   ```

   Failed during collection for the same missing adapter package.

3. Reopen convergence defect RED after initial implementation:

   The integration test reopened the persistent directory successfully but
   failed its final tuple equality because Chroma's volatile `created_at`
   differed after clear/replay. `actual_records` now emits only canonical
   projection metadata, excluding volatile storage metadata.

4. Legacy handover adoption RED:

   A content-derived record already written by the legacy recorder existed,
   but the adapter initially returned no canonical record because duplicate
   capture exited before attaching Canon scope. The recorder now claims only
   a completely unscoped matching content record and attaches tenant/project,
   stream position and revision identity without changing its record ID.

## Implementation

- `ProjectionAdapterBase` canonicalizes payloads, sorts records by
  `(stream_position, record_id)`, and hashes the normalized record list with
  `sha256_json`.
- World uses `WorldStateManager.upsert_fact` and IDs of the form
  `world-fact-<sha256(projection_event_id:fact_ordinal:normalized_text)>`.
  Existing `add_fact()` random UUID behavior is unchanged.
- World facts carry Canon tenant/project, commit/revision, stream position and
  projection event identity. Enumeration and clear filter those markers.
- Handover preserves the existing task-hash/section/content-hash record ID.
  New Canon markers are optional for legacy schema compatibility. A matching
  old unscoped content record is adopted rather than duplicated.
- Chroma retains deterministic chunk IDs derived from commit, ordinal and
  source content hash. New metadata includes tenant/project/task,
  stream_position, commit and revision. Enumeration queries the complete
  tenant/project/task intersection and deletion uses only returned IDs.

## Clear isolation evidence

- Unit tests retain an unrelated Redis task, and also retain an unrelated
  project stored inside the same target task's World/Handover hash.
- No adapter calls `Blackboard.delete(task_id)` or deletes a Redis key.
- The Chroma integration fixture writes a second project into the same
  collection and proves it is byte-for-byte unchanged after target clear.
- Test Chroma data is confined to pytest's unique `tmp_path`; pytest owns its
  cleanup.

## GREEN and reopen gates

Target gate:

```text
python -m pytest tests/unit/projections/test_critical_projection_adapters.py
  tests/integration/canonical/test_critical_projection_rebuild.py -q
6 passed, 4 warnings
```

The integration test performs: persistent apply, object release, reopen,
enumeration, scoped clear, replay, second release/reopen, and equality with the
pre-clear normalized records.

Scoped legacy regression gate:

```text
43 passed, 1 warning
```

Covered existing Handover history/payload/persistence, VectorStore, World
legacy projection and state-frame persistence tests.

Static verification:

```text
ruff check ...
All checks passed!
git diff --check
exit 0
```

Warnings are existing `pkg_resources` deprecation plus Chroma's future
`EmbeddingFunction.name()` deprecation in the tiny integration embedder.

## Files

- `app/projections/__init__.py`
- `app/projections/base.py`
- `app/projections/legacy_world.py`
- `app/projections/handover.py`
- `app/projections/chroma_story.py`
- `app/world_state.py`
- `app/vector_store.py`
- `app/writing/subsection_handover_history.py`
- `app/writing/subsection_handover_persistence.py`
- `tests/unit/projections/test_critical_projection_adapters.py`
- `tests/integration/canonical/test_critical_projection_rebuild.py`

## Concerns

- World intentionally projects `handover_candidate.new_facts`, matching the
  established legacy `StateCommitter.commit_handover_effects` behavior. It
  does not project arbitrary ledger payload fields as facts.
- Old legacy World/Handover records without Canon markers are never removed by
  scoped clear. Only an exact content-derived Handover duplicate can be
  adopted when all Canon markers are absent.
- Chroma provider-level deprecation warnings are non-functional and confined
  to third-party API evolution.
