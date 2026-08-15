# Phase 0 Writing Flow E2E Design

## Objective

Create a deterministic, CI-safe end-to-end acceptance layer for the Phase 0 writing flow. The suite must prove the HTTP task lifecycle and its browser wiring without contacting a paid LLM, requiring an external Redis service, or starting a real Celery worker.

The accepted scope is the current `/write-ui-v2` application and the existing FastAPI task routes. This work validates and fixes Phase 0 behavior; it does not begin the Phase 1 visual or component migration.

## Success Criteria

The work is complete when all of the following are true:

1. Automatic mode is exercised from durable workspace creation through writing completion and export.
2. Interactive mode proves that an approval returns a new task ID while the workspace ID remains stable and the new task becomes active.
3. Status and stream polling use the same public HTTP contracts as the browser.
4. Stream cursors do not replay already consumed text.
5. A completed or in-progress workspace can be reconstructed from server state without relying on browser local storage.
6. API-key rejection, HTTP 429, and HTTP 500 responses retain structured, user-actionable error information.
7. A real browser smoke run confirms that the production page consumes the deterministic server correctly for automatic and interactive scenarios.
8. No real LLM request, external Redis instance, Celery worker, or production database is used.

## Selected Architecture

### Deterministic writer

`tests/e2e/support/deterministic_writer.py` will implement a small Celery-compatible test double for the existing `writing_task` interface. It will expose the production-facing methods used by task routes:

- `apply_async(kwargs, task_id)`
- `delay(**kwargs)` returning an object with `id`
- `AsyncResult(task_id)` for result-route compatibility

The writer will write state and stream events through the real `Blackboard` API backed by one `fakeredis.FakeRedis` instance. It will not call coordinator or agent code. The purpose is to make task transitions deterministic while keeping route, persistence, response-model, stream cursor, and frontend API contracts real.

The test double will reuse production request/response models, status literals, event field names, and existing enums wherever they already exist. Test-only dictionaries may contain scenario-control metadata, but public task state and stream payloads must be constructed from production schema fields rather than copied parallel contracts. Contract tests will import the production model or enum and fail if the deterministic writer emits an unsupported public value.

The adapter will separate **task submission** from **task execution**. Route handlers set their own initial `pending` fields after `apply_async()` or `delay()` returns, so the adapter must not complete a task inside those submission calls. In pytest, the scenario explicitly invokes the adapter's `advance(task_id)` or `complete(task_id)` method after the HTTP submission response. This models an external worker without timing sleeps. In the browser support server, a tracked background runner performs the same ordered advances using `WRITER_E2E_STEP_DELAY_MS` (default 250 ms) so the page can visibly observe running, streaming, confirmation, and completion states. The runner exposes a shutdown method and every timer/thread is joined before cleanup.

Automatic mode will produce a minimal one-section/one-subsection event sequence:

1. task status `pending`
2. `section_start`
3. two token events with stable text
4. `section_end`
5. `done`
6. task and workspace status `completed`

The pytest scenario advances these steps explicitly. The browser server advances one step per configured delay and keeps the task `running` until after the final stream event, preventing a completed status from stopping frontend polling before the deterministic text is consumed.

The ordering is normative: the writer must append the `done` event first and only then persist task/workspace status `completed`. The E2E test will observe the state between these two advances, assert that `done` is readable while status is still nonterminal, then advance once more and assert `completed`.

Interactive mode will first persist a resumable checkpoint and expose an awaiting-confirmation status. When `/tasks/{task_id}/decide` creates a replacement task, the same deterministic writer will complete that replacement task and preserve the original `workspace_task_id`.

### In-process HTTP E2E

`tests/e2e/test_phase0_writing_flow.py` will use FastAPI `TestClient` against the real application. A fixture will replace only route-level external boundaries:

- `app.routers.tasks.bb` with a `Blackboard` using fakeredis
- the shared route `writing_task` with the deterministic writer
- Celery revoke control with a harmless spy
- task/workspace database paths with pytest's existing isolated runtime

All requests will use public endpoints. Tests will not call route functions directly.

### Browser support server

`tests/e2e/support/server.py` will be an executable test server. It will import the application, install the same deterministic writer and fakeredis-backed Blackboard in-process, and then start Uvicorn. It exists only under `tests/e2e`; production imports and startup behavior remain unchanged.

The server will support deterministic scenario selection through test-only environment variables:

- `WRITER_E2E_SCENARIO=automatic`
- `WRITER_E2E_SCENARIO=interactive`

`WRITER_TESTING=1` and an explicit temporary `TASK_DB_PATH` remain mandatory. The support server will refuse to start without these isolation settings.

For the storage-reset acceptance, the support server will expose a same-origin test-only page at `/__e2e__/clear-storage`. Its visible button will call `localStorage.clear()` and `sessionStorage.clear()` and then navigate to `/write-ui-v2`. This route exists only in the support server wrapper, is unavailable from the production application, and lets browser acceptance clear storage through an explicit UI action without inspecting stored values.

## Scenario Contracts

### Automatic writing flow

The test will:

1. `POST /tasks` and capture `workspace_task_id`.
2. `PATCH /tasks/{workspace_task_id}/workspace` with topic, world setting, synopsis, reference text, and target words.
3. Save a one-subsection outline using the public outline route.
4. `POST /write?mode=celery` with the existing workspace task ID.
5. Assert the response task ID equals the draft task ID and the workspace ID is unchanged.
6. Advance through `section_start` and the first token, poll `/status/{task_id}`, and assert the task is still nonterminal with stable workspace/active task IDs.
7. Read `/stream/{task_id}` from `0-0`, save the first-token cursor, and assert the observed prefix is ordered and contains no later event.
8. Append the remaining token, `section_end`, and `done` events; resume from the saved cursor and assert every remaining event appears once with no replay of earlier text.
9. Before the final state advance, assert `done` is readable while `/status/{task_id}` is still nonterminal. Then advance once more and assert task/workspace status `completed`.
10. Read the stream again from the final `last_id` and assert no duplicate events.
11. Read `/tasks/{task_id}/workspace` and `/projects` to prove server-side recovery.
12. Create Markdown, TXT, and JSON exports and verify each download contains the deterministic draft or its structured equivalent.

### Interactive writing flow

The test will:

1. Create and configure a durable workspace.
2. `POST /write?mode=interactive` using its ID.
3. Assert the original task enters `awaiting_outline_confirmation` and exposes a checkpoint.
4. `POST /tasks/{task_id}/decide?phase=outline&action=approve`.
5. Assert `new_task_id` differs from the original task ID while `workspace_task_id` remains unchanged.
6. Assert the workspace and original status response point to the new active task.
7. Read the new task status and stream through completion.
8. Confirm the new stream starts at `0-0` and does not reuse the old cursor.

### Error mapping

HTTP contract tests will exercise the actual API client semantics without a browser build:

- 401/403-like API-key rejection retains the server detail.
- 429 is classified as retryable.
- 500 is classified as retryable and retains structured detail.
- abort/timeout continues to produce the existing retryable cancellation message.

The route E2E layer will use deterministic failure modes from the test writer rather than network calls.

## Browser Acceptance

The in-app browser will open `/write-ui-v2` against the support server. The browser run will use visible application controls and public application endpoints; the only test-control surface is the visible same-origin storage-reset page defined above.

Automatic smoke acceptance:

- create or open the isolated test project;
- select automatic mode and start writing;
- observe stable project identity, streamed deterministic text, saved state, completion, and export access.
- after server state exists, clear both `localStorage` and `sessionStorage`, reload through a normal page navigation, and recover the project from the server-side project list with the same workspace/task identity and draft content.

Interactive smoke acceptance:

- start in interactive mode;
- observe the confirmation state;
- approve through the page;
- verify the page follows the replacement task ID and receives its stream without losing the workspace anchor.
- clear both browser storage areas, reload, reopen the same server project, and confirm the replacement task remains the active task.

Browser observations will be recorded in an SDD report. The deterministic HTTP tests are the CI gate; the browser run is the release-wiring gate until a Playwright dependency is introduced in Phase 5.

## Isolation and Cleanup

- Every E2E run uses a temporary SQLite task database and in-memory fakeredis instance.
- The support server validates that its database path is inside the explicit temporary test directory.
- Tests and browser acceptance use fixed small outlines and deterministic text.
- Server processes are tracked by exact PID and stopped after the run.
- Temporary databases are deleted only after their absolute paths are verified to remain inside the owned test directory.
- Existing developer projects, Redis data, `.env`, and user-authored working-tree changes are never read or modified by the scenarios.

## Test Boundaries

This suite proves route orchestration, task/workspace identity, polling contracts, stream cursor behavior, persistence recovery, export wiring, structured errors, and production-page integration.

It deliberately does not prove LLM output quality, real broker delivery, worker concurrency, or production Redis networking. Those remain covered by coordinator/unit tests, projection integration gates, and a future real-infrastructure release test.

## Files

- Create `tests/e2e/support/deterministic_writer.py`: deterministic task state machine and Celery-compatible adapter.
- Create `tests/e2e/support/server.py`: isolated Uvicorn support server for browser acceptance.
- Create `tests/e2e/test_phase0_writing_flow.py`: automatic, interactive, recovery, cursor, export, and error scenarios.
- Modify `tests/unit/test_frontend_api_upgrade_routes.py` or add a focused Node API test only if current error-mapping contracts are insufficient.
- Create `.superpowers/sdd/2026-08-15-phase0-writing-e2e/`: task briefs, reports, review packages, and ledger; ignored from product commits.

## Review Gates

1. Deterministic writer review: state transitions, checkpoint semantics, isolation, and no production imports changed.
2. HTTP scenario review: public endpoints only, meaningful assertions, mutation-sensitive tests, and no route-function shortcuts.
3. Browser review: automatic and interactive visible behavior, task-ID transition, workspace stability, and cleanup.
4. Final verification: new E2E suite, affected route/frontend suites, JavaScript syntax, Python compileall, and scoped diff checks.
