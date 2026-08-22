# Narrative OS Architecture Status

Date: 2026-08-10

## Stable baseline

- Baseline branch: `foundation/baseline-2026-08-09`
- Foundation implementation tip: `10f4252`
- Stable tag: `narrative-os-foundation-v1`
- Gate evidence: `reports/foundation/p0-p2-gate-summary.md`
- Golden evidence: `reports/foundation/p2-golden-slice-evidence.json`

The tag identifies the first verified baseline where an accepted subsection is
defined by one Canonical transaction rather than by the order of Writer side
effects. It is the recovery point for later P3A, P3B and P4 work.

## Phase status

| Phase | Status | Authority unlocked | Remaining boundary |
| --- | --- | --- | --- |
| P0 | DONE | Reproducible contracts, isolated tests and secret-safe evidence | Continue enforcing hygiene in later phases |
| P1 | DONE | Versioned PostgreSQL Canonical schema, repositories and snapshot restore | Schema expansion remains migration-controlled |
| P2 | DONE | Candidate, dual-Head atomic commit, idempotency, ledger, outbox and critical Barrier | `internal_required` is internal dogfood only |
| P3A | DONE | PostgreSQL-authoritative projection consumption, deletion/rebuild, reconciliation and scanner recovery | P3B remains the external production safety gate |
| P3B | READY | Auth, tenant isolation, credentials, CI, observability and backup gates | Blocks external Alpha |
| P4 | UNBLOCKED BY P3A | World Runtime may interpret facts into the existing commit boundary | Must not redefine Canonical Commit |
| Client | DEFERRED | Writing Blocks and editing UX consume server contracts | Begins after server/runtime boundaries stabilize |

## Implemented write authority

```text
Writer
  -> immutable Candidate
  -> validation
  -> deterministic State Transition
  -> one Canonical PostgreSQL transaction
       -> Revision Head
       -> Project State Head
       -> Document Revision
       -> State Version
       -> Ledger
       -> Idempotency Result
       -> Outbox Manifest
  -> critical Projection Barrier
  -> next subsection allowed
```

Redis, Chroma, Handover/Recent context, Markdown and legacy world/event stores
are no longer allowed to decide whether a Canonical commit happened. P2 proves
the minimal projection path; P3A must prove that every projection is safely
consumed, discarded, rebuilt and reconciled from Canon.

## Permanent scope warning

`internal_required` means that controlled internal new tasks must use the
Canonical Commit path and fail closed when required bindings or critical
projections are unavailable. It does **not** mean external production ready.

External production remains blocked until P3B proves authentication,
authorization, tenant isolation, credential references, distributed rate
limits, CI gates, observability, backup/restore and operational runbooks.

## Deferred work

- P3A: complete. Evidence is recorded in `reports/p3a/p3a-gate-summary.md` and
  operations are documented in `docs/runbooks/p3a-projection-operations.md`.
- Deferred after P3A: online shadow rebuild, same-stream unordered parallelism,
  and Outbox fan-out normalization.
- P3B: identity and authorization boundaries, tenant enforcement, credential
  custody, distributed limits, deployment gates, telemetry and disaster recovery.
- P4: authoritative World Runtime and Context Broker migration only after P3A.
- Client: Writing Blocks, patches and human editing remain downstream consumers
  of Canonical Commit; they do not introduce an alternative write authority.
