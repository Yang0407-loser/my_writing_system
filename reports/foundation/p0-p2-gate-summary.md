# P0–P2 Foundation Gate Summary

Date: 2026-08-10
Source branch: `feat/p0-p2-foundation`
Merged baseline: `foundation/baseline-2026-08-09`
Stable tag: `narrative-os-foundation-v1`
Decision: **Approved for controlled `internal_required` internal dogfood only**

## Final centralized-remediation verification

| Command | Exit | Result |
| --- | ---: | --- |
| `uv sync --python 3.11 --locked --extra dev` | 0 | CPython 3.11.5 environment created from the locked graph; Ruff included |
| `.venv311\Scripts\python.exe -m pytest -q -rs` | 0 | Python 3.11.5: `1903 passed, 2 skipped, 5 warnings` in 240.64s; both skips explicitly require an intentionally private secret-bearing fixture |
| Canonical PostgreSQL integration | 0 | `24 passed, 0 skipped` in 15.21s against isolated PostgreSQL 16; schema, concurrency, 100 replays and process-termination recovery all ran |
| Foundation Ruff scope | 0 | All checks passed under the explicitly frozen Python 3.11 rule set |
| PostgreSQL Golden Slice | 0 | `backend=postgresql`, `gate_eligible=true`, `phase=ready`; fixed seven-projection manifest published and hashes agree |
| `verify_foundation_gate.py --evidence reports/foundation/p2-golden-slice-evidence.json` | 0 | `passed=true`, no errors, and the evidence secret scan is clean |
| rollback drill (included in Canonical integration) | 0 | Canon remained authoritative across mode rollback and an unready critical Barrier |
| Post-merge Foundation smoke | 0 | `103 passed` on the merged baseline; PostgreSQL Golden Slice regenerated with `gate_eligible=true` and verifier `passed=true` |

## Directed implementation evidence

- Review-remediation directed regression (Candidate/Commit/config/lifecycle/runtime/Writer routing): 58 passed.
- Writer Candidate seam and legacy facade: 25 passed.
- Legacy projector, vector and handover groups: 5 + 7 + 20 passed in isolated processes.
- Canonical runtime, rollout, TaskStore and API contract group: 39 passed.
- Golden vertical slice and failure/recovery matrix: 11 passed.
- Final Canonical integration group: 24 passed, 0 skipped on PostgreSQL 16.
- Golden content hash equality: fixture = Revision = materialized Document = `3999516e6727d1548889478f0c666b1c31fb63870155cc5e19e6d8c0e100f9c5`.
- Seven fixed outbox rows published in the PostgreSQL Golden Slice; critical Barrier ready; secret scan clean.

## Central remediation outcome

FND-001 through FND-019 are resolved. FND-003/FND-008 were closed by the real
PostgreSQL 16 Gate: all formerly skipped schema/concurrency/crash/replay tests
ran, and the PostgreSQL Golden Slice passed the fail-closed verifier.

The production code now contains the real per-subsection route: Coordinator
owns the SQL session/runtime and critical projectors, Writer owns no database
session, and a canonical selection bypasses the legacy subsection commit. The
runtime fails closed on missing scope/document/subsection bindings or an
unready critical Projection Barrier.

`internal_required` is now allowed only for controlled internal new-task dogfood
under the cutover runbook. This does not approve external production users,
distributed projection operations, multi-tenant security, backup/restore SLOs,
or the P3A/P3B production gates.
