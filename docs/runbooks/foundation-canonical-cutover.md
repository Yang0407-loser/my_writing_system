# Foundation Canonical Cutover Runbook

## Scope

This runbook controls the P2 internal-dogfood boundary only. It does not approve external users, multi-tenant production, distributed projection workers, backups, or P3B security controls.

## Preconditions

Do not select `internal_required` until all Foundation Gate commands are freshly green on the target commit, the Golden evidence backend is PostgreSQL, no PostgreSQL test is skipped, the failure/recovery matrix passes, and the evidence secret scan is clean.

## Modes

- `legacy`: all not-yet-started subsections use the frozen Writer facade. Accepted Canonical revisions remain readable from Canon and are never overwritten from a checkpoint.
- `canary`: a subsection uses Canonical runtime only when both task ID and subsection ID are in their exact allowlists. There is no dual write.
- `internal_required`: internal new tasks must have tenant/project/document/subsection bindings, explicit Revision and State Heads, a Canonical database and critical projectors. Missing dependencies fail closed. A checkpoint created before Foundation resumes explicitly through `legacy`.

## Cutover

1. Freeze the deployment commit and run the complete Gate commands recorded in `reports/foundation/p0-p2-gate-summary.md`.
2. Back up the Canonical PostgreSQL database and record the schema revision.
3. Start in `canary` with one internal task and one subsection ID.
4. Confirm one Commit, one Revision, one State version, the fixed seven-row outbox manifest, and a `ready` critical Barrier.
5. Confirm the result API returns `document_ref`, `commit_status`, `state_version_id` and both projection statuses while preserving all legacy fields.
6. Expand allowlists only after the prior subsection is reconciled.
7. Select `internal_required` only after the final Gate decision is `approved`.

## Projection outage

If a critical projector fails after SQL commit, set the task phase to `awaiting_critical_projection`. Do not regenerate, move Heads backward, switch the accepted subsection to legacy, or start the next subsection. Restore the sink and retry the durable failed outbox row. A non-blocking failure is recorded as lagging and does not close the Barrier.

## Rollback

1. Stop admitting new canonical subsections by changing `canary` to `legacy`, or stop internal new tasks if `internal_required` is unhealthy.
2. Keep Canonical PostgreSQL and outbox data intact.
3. Continue pre-Foundation checkpoint resumes only with the explicit legacy compatibility path.
4. For every already committed subsection, materialize content from the Canonical Revision Head. Never write checkpoint draft text over it.
5. Drain/retry critical outbox rows until their Barrier is ready; mode rollback does not waive this rule.
6. Record commit IDs, failed projector names and retry counts; do not record prompts,正文, or credentials.

## Abort conditions

Abort the cutover if any SQL partial write is observed, an idempotency replay invokes the LLM, a critical Barrier is bypassed, a legacy/canonical dual write occurs, any Gate test is skipped without explicit approval, or evidence contains a credential-like value.
