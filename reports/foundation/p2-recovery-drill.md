# P2 Foundation Recovery Drill

Date: 2026-08-09  
Scope: deterministic Golden subsection, no real LLM, no credentials or full prompt in evidence.

## Initial implementation result

The SQLite implementation rehearsal completed the full Candidate → State Transition → Canonical Commit → dual Heads → Outbox → critical Barrier path. The fixture, accepted Revision and materialized Document all produced SHA-256 `3999516e6727d1548889478f0c666b1c31fb63870155cc5e19e6d8c0e100f9c5`. All seven fixed outbox rows reached `published` and the critical Barrier reached `ready`.

This is implementation evidence, not the final Gate: the verifier correctly rejects it because `backend=sqlite`. A fresh real PostgreSQL run remains mandatory under FND-008.

## Fault matrix exercised

| Injection / replay | Observed invariant | Evidence |
| --- | --- | --- |
| Failure after Revision, State, Ledger, Outbox, or before SQL commit | Zero Commit, Revision, Ledger and Outbox partial rows | `test_sql_crash_points_leave_zero_partial_canon` (5 cases) |
| Critical Chroma unavailable after SQL commit | Canon remains committed; phase pauses at `awaiting_critical_projection` | `test_critical_outage_keeps_canon_and_retry_preflight_skips_llm` |
| Worker loses commit result and retries | Original idempotent result is recovered; LLM is not called | Same retry test and runtime preflight tests |
| Redis Stream and Markdown unavailable | Barrier remains ready; failures are independently retryable | `test_nonblocking_outage_does_not_close_critical_barrier` |
| Dispatcher terminates during critical projection | Restart scans failed/pending rows and does not republish successful critical rows | `test_dispatcher_restart_continues_failed_rows_without_republishing_successes` |
| Same message delivered 100 times | One Commit, one Revision, one fixed seven-row manifest | `test_same_message_100_times_is_one_commit_revision_and_manifest` |
| Derived Chroma data deleted | Replaying accepted Canon produces identical deterministic chunk IDs/content metadata | `test_deleted_derived_chunks_rebuild_identically_from_canon` |
| Two writers share a State Head | PostgreSQL concurrency test exists and is fail-closed skipped without the daemon | `test_postgres_atomic_commit.py`; FND-008 |

## Recovery rule

Once SQL commit succeeds, Canon is never rolled back because a projection is unavailable. Critical lag pauses the next subsection before any LLM call. Non-blocking lag is visible but does not pause generation. Mode rollback affects only work that has not started; it cannot make an accepted Canonical Revision cease to exist.
