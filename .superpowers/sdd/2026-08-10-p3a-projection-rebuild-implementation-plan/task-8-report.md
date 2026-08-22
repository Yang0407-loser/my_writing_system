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
- `app/projections/legacy_scope.py`
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
- Legacy World/Handover records without Canon markers fail closed until an
  operator-approved durable binding proves the exact tenant/project/task
  owner. An exact binding permits clear-then-replay; missing, mismatched, or
  conflicting ownership never permits first-caller adoption or deletion.
- Chroma provider-level deprecation warnings are non-functional and confined
  to third-party API evolution.

## Fix Round 1 design — operator-approved legacy ownership

Review found that a legacy Redis task namespace proves only `task_id`; legacy
World facts and Handover records do not contain tenant/project ownership, and
`task_history` has no tenant/project columns. Adapter constructor arguments
therefore cannot authorize adoption or deletion.

The remediation uses a durable `LegacyScopeBinding` stored explicitly in the
legacy task namespace. It binds exactly one legacy `task_id` to one Canon
`tenant_id/project_id/task_id` and carries operator approval evidence
(`operator_id`, `reason`, `approved_at`). Binding creation is an operator API,
never an adapter side effect. Repeating the exact binding is idempotent;
conflicting rebinding fails closed.

New Canon World and Handover records use deterministic scope-specific Redis
field names. The existing logical Handover `record_id` remains unchanged. An
adapter that sees legacy unmarked data may enumerate/migrate/clear it only
when the durable binding exactly matches its full scope. Missing or mismatched
binding raises an ownership error, so reconciliation cannot report a false
empty/green sink. Exact binding authorizes target migration/clear while
unrelated task namespaces and already-scoped neighboring records remain
untouched.

Handover semantic upsert collapses one logical content-derived ID to the
highest `stream_position`. Older out-of-order writes are no-ops; the same
position with different Canon identity/payload fails closed. Expected records
use the identical collapse rule.

### Fix Round 1 RED/GREEN evidence

- RED collection initially failed because the binding module did not exist.
  After the minimal binding primitive, seven behavioral failures reproduced
  all review findings: spoofed adapter spec accepted; canonical Chroma ID
  swallowed by legacy hash dedupe; wrong returned Chroma ID signed; Handover
  first-write-wins on out-of-order delivery; same-position conflict not
  classified; unmarked Handover/World reconciled without binding.
- A production `VectorStore` path now proves explicit `document_id` always
  exact-upserts, while callers without `document_id` retain legacy content
  dedupe. The real persistent Chroma test covers two equal-content commits and
  two equal-text chunks in one commit, each retaining its deterministic ID
  across clear/replay/reopen.
- Every adapter independently rejects projector ID, version or barrier-kind
  mismatch before touching its sink.
- World and Handover canonical physical identities include tenant/project
  scope. Tests use the same logical ID in neighboring projects and prove both
  coexist and scoped clear preserves the neighbor.
- Bound legacy migration is intentionally clear-then-replay: actual/apply
  remain fail closed while unmarked records exist, even after approval. Exact
  binding authorizes clear of unmarked records; replay then writes only scoped
  canonical records. This prevents a mixed legacy/canonical consumer view.
- Independent Fix Round 1 review found that World initially reconstructed
  scope-safe facts under the logical ID after Redis reload. A focused reopen
  test failed with two live project records collapsing to one; `_load()` now
  recomputes the same scope-qualified physical key and the reopen test passes.

Fresh Fix Round 1 gates:

```text
Task 8 unit + real persistent Chroma reopen: 16 passed
Scoped legacy regressions: 46 passed
Ruff: All checks passed
git diff --check: exit 0
Independent self-review: one Important found, fixed and regression-covered
```

## Fix Round 2 — atomic scoped storage and runtime isolation

Re-review reproduced four related storage-boundary failures. World and
Handover Canon records shared legacy whole-envelope fields, so a manager or
recorder constructed before another project wrote could save a stale snapshot
and erase that project. The same shared fields also exposed neighboring Canon
records through legacy runtime/query/checkpoint APIs. Legacy scope approval
used a non-atomic get-then-set, and partially populated Canon identity markers
could be treated as harmless legacy data.

The minimal remediation keeps the legacy fields unchanged for legacy runtime
consumers and moves new Canon projection records to task-and-scope-qualified
Redis hashes, one deterministic record per hash field. World uses an exact
field upsert. Handover uses Redis WATCH/MULTI compare-upsert so the highest
`stream_position` wins atomically, stale writes are no-ops, and same-position
semantic conflicts fail closed. Scoped clear validates every record before
issuing HDEL and never rewrites a neighboring scope.

`Blackboard` now exposes the minimal production primitives used by these
adapters: HSETNX for binding creation, JSON hash field read/write/delete, and
the Handover position-aware transaction. Conflicting concurrent approvals
therefore have exactly one durable winner; the loser rereads and either returns
the identical binding or rejects a conflicting scope. Operator and reason are
trimmed and required non-empty. `approved_at` is generated when absent and
must parse as an aware UTC timestamp.
When `approved_at` is generated by the store, an otherwise identical retry
reuses the durable winner's timestamp and remains idempotent.

Legacy runtime `WorldStateManager.get_all_facts/query_relevant` and Handover
`history_for_checkpoint` expose only records with no Canon identity markers.
Adapters retain explicit scoped reads for Canon data. Complete Canon records
left in the Round 1 shared envelope are hidden from legacy consumers and can
only be removed by an exact approved binding during clear-then-replay.
Any partially populated identity, whether in the old envelope or new scoped
hash, blocks actual/apply/clear before deletion.
World runtime/checkpoint serialization is separate from its internal migration
storage serialization, so hiding old Canon records from consumers does not
cause a legacy clear to discard a neighboring project's old Canon record.

### Round 2 RED/GREEN and mutation evidence

- Initial focused RED: 9 failures / 15 passes, covering World stale adapter
  overwrite, World runtime ghost, Handover concurrent lost update, Handover
  checkpoint ghost, fake and production-Blackboard binding races, audit
  normalization, and World/Handover partial markers.
- Self-review added four further RED cases: malformed records inside the new
  scoped namespace must block clear, and complete Round 1 Canon records in the
  old World/Handover envelopes must not leak through legacy runtime/checkpoint.
- Real `fakeredis` concurrency tests run two conflicting binding approvals and
  prove one winner, then run World and Handover writes for two projects in the
  same task and prove both survive scoped clear.
- Mutation coverage includes removing HSETNX, removing the scoped hash,
  replacing Handover compare-upsert with unconditional HSET, omitting any
  identity-marker validation, or returning old Canon envelope records through
  legacy APIs; each mutation is caught by a focused test.

Fresh Fix Round 2 gates:

```text
Focused critical adapters (real fakeredis paths included): 29 passed
Expanded target + real persistent Chroma reopen + legacy/runtime/checkpoint: 91 passed
Ruff: All checks passed
git diff --check: exit 0
```

## Fix Round 3 — implicit approval timestamp race

Round 2 made a sequential `approved_at=None` retry idempotent, but two first
callers could both observe an empty binding, generate distinct UTC timestamps,
and race HSETNX. The loser then compared the entire proposed artifact to the
durable winner and incorrectly reported a conflict despite identical scope,
operator, and reason.

A controlled production-`Blackboard`/`fakeredis` test synchronizes both empty
reads and supplies distinct generated timestamps. RED produced one success and
one conflicting-rebind error. The store now remembers whether the caller
explicitly supplied `approved_at`: implicit-time comparisons exclude only that
field and return the durable winner, while explicit timestamps remain part of
strict equality. Scope, operator, reason, task, tenant, project, and schema
remain fail-closed in both paths.

Round 3 evidence:

```text
Controlled implicit-time concurrent first approval: RED 1 failed / 1 passed
Focused binding race/strictness/conflict gate: 5 passed
Fresh expanded Task 8 gate: 93 passed
Ruff: All checks passed
git diff --check: exit 0
```
