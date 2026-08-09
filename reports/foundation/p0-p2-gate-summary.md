# P0–P2 Foundation Gate Summary

Date: 2026-08-09  
Branch: `feat/p0-p2-foundation`  
Decision: **Not approved for `internal_required` dogfood yet**

## Initial Task 20 verification

| Command | Exit | Result |
| --- | ---: | --- |
| `uv sync --locked --extra dev` | 0 | 148 packages resolved; isolated worktree `.venv` created |
| `pytest tests/unit tests/integration tests/contract -q` | 1 | Collection stopped after 66.30s: missing `experiments.world_runtime_writer_canary.wr310_reviewer_real_task_side_by_side`; no passed count available |
| `pytest tests/integration/canonical -q` | 0 | 13 passed, 10 skipped; every skip is a missing real PostgreSQL gate and therefore counts as Gate incomplete |
| `verify_foundation_gate.py --evidence reports/foundation/p2-golden-slice-evidence.json` | 1 | Golden data path is internally consistent, but evidence backend is SQLite; real PostgreSQL is required |
| rollback drill | 0 | 1 passed; Canon remained authoritative across mode rollback and an unready critical Barrier |

## Directed implementation evidence

- Candidate/State/Commit/Outbox unit regression: 62 passed.
- Writer Candidate seam and legacy facade: 25 passed.
- Legacy projector, vector and handover groups: 5 + 7 + 20 passed in isolated processes.
- Canonical runtime, rollout, TaskStore and API contract group: 39 passed.
- Golden vertical slice and failure/recovery matrix: 11 passed.
- Canonical integration group after Task 19: 13 passed, 10 PostgreSQL tests skipped.
- Golden content hash equality: fixture = Revision = materialized Document = `3999516e6727d1548889478f0c666b1c31fb63870155cc5e19e6d8c0e100f9c5`.
- Seven fixed outbox rows published in the SQLite rehearsal; critical Barrier ready; secret scan clean.

## Open Gate blockers entering centralized remediation

1. FND-001/FND-002: default full suite has a missing historical experiment module and legacy failures after bypassing it.
2. FND-003/FND-008: Docker Desktop Linux daemon is unavailable, so the mandatory PostgreSQL schema/concurrency/crash/replay suite and PostgreSQL Golden evidence cannot run.
3. FND-004: local evidence is Python 3.14 while the target runtime is Python 3.11.
4. FND-005/FND-006/FND-007/FND-010: resume diagnostics, Windows test cleanup, duplicate OpenAPI operation ID and Alembic logger pollution require centralized correction.
5. FND-009: Ruff was not part of the locked dev environment during Task 15 verification.

`docs/PROGRESS.md` is intentionally unchanged. It may be updated only after a fresh all-green, no-PostgreSQL-skip final Gate.
