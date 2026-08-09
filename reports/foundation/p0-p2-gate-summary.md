# P0–P2 Foundation Gate Summary

Date: 2026-08-10
Branch: `feat/p0-p2-foundation`
Decision: **Not approved for `internal_required` dogfood yet**

## Final centralized-remediation verification

| Command | Exit | Result |
| --- | ---: | --- |
| `uv sync --python 3.11 --locked --extra dev` | 0 | CPython 3.11.5 environment created from the locked graph; Ruff included |
| `pytest -q` | 0 | `1893 passed, 12 skipped, 6 warnings` in 156.33s; 10 skips are PostgreSQL and 2 require an intentionally private fixture |
| Canonical + Coordinator directed integration | 0 | `46 passed, 10 skipped`; every skip is a missing real PostgreSQL gate and therefore counts as Gate incomplete |
| Foundation Ruff scope | 0 | All checks passed under the explicitly frozen Python 3.11 rule set |
| Golden Slice rehearsal | 0 | SQLite backend reached `ready`; fixed seven-projection manifest published and hashes agree |
| `verify_foundation_gate.py --evidence reports/foundation/p2-golden-slice-evidence.json` | 1 | Correct fail-closed result: `real PostgreSQL evidence is required` |
| rollback drill (included in Canonical integration) | 0 | Canon remained authoritative across mode rollback and an unready critical Barrier |

## Directed implementation evidence

- Review-remediation directed regression (Candidate/Commit/config/lifecycle/runtime/Writer routing): 58 passed.
- Writer Candidate seam and legacy facade: 25 passed.
- Legacy projector, vector and handover groups: 5 + 7 + 20 passed in isolated processes.
- Canonical runtime, rollout, TaskStore and API contract group: 39 passed.
- Golden vertical slice and failure/recovery matrix: 11 passed.
- Canonical integration group after Task 19: 13 passed, 10 PostgreSQL tests skipped.
- Golden content hash equality: fixture = Revision = materialized Document = `3999516e6727d1548889478f0c666b1c31fb63870155cc5e19e6d8c0e100f9c5`.
- Seven fixed outbox rows published in the SQLite rehearsal; critical Barrier ready; secret scan clean.

## Central remediation outcome

FND-001/002/004/005/006/007/009/010/011/012/013/014/015 are resolved. The only approval
blocker is FND-003/FND-008: Docker Desktop is not running, so the ten real
PostgreSQL schema/concurrency/crash/replay tests and PostgreSQL Golden Slice
cannot be executed. SQLite evidence is deliberately insufficient for promotion.

The production code now contains the real per-subsection route: Coordinator
owns the SQL session/runtime and critical projectors, Writer owns no database
session, and a canonical selection bypasses the legacy subsection commit. The
runtime fails closed on missing scope/document/subsection bindings or an
unready critical Projection Barrier.

`docs/PROGRESS.md` is intentionally unchanged. It may be updated only after a fresh all-green, no-PostgreSQL-skip final Gate.
