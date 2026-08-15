# Phase 0 Writing Flow E2E Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a deterministic Phase 0 acceptance suite that proves automatic and interactive writing, cursor resume, completion ordering, durable browser recovery, exports, and API error semantics without a paid model, external Redis, or Celery worker.

**Architecture:** A test-only Celery-compatible writer drives the real FastAPI routes and real `Blackboard` API over fakeredis. Pytest advances the writer one state transition at a time; a test-only Uvicorn wrapper advances the same state machine on a bounded background thread for visible browser acceptance. The only production behavior change is the focused correction that appends `done` before persisting `completed`.

**Tech Stack:** Python 3.11+, FastAPI `TestClient`, pytest, fakeredis, SQLite `TaskStore`, Node.js built-in test runner, Uvicorn, in-app browser.

## Global Constraints

- Keep all harness code under `tests/e2e`; do not add a production startup flag or production-only test route.
- Require `WRITER_TESTING=1` and an explicit temporary `TASK_DB_PATH` for the browser support server.
- Use one fakeredis instance through the production `Blackboard` class; do not implement a parallel blackboard.
- Reuse `WriteRequest`, `WriteResponse`, `TaskStatus`, and `TaskState`; the production code has no task-status enum, so do not create a test-only public-status enum.
- Construct public stream events with the production field names consumed by `app/static/js/main.js`: `event`, `section`, `subsection`, `token`, `text`, `draft`, and `review`.
- Append `done` before persisting `completed` in both production and deterministic paths, with an observable assertion between those two writes.
- Include a resume from a cursor captured after the first token; resumed reads must not replay the prefix.
- Browser recovery must clear both `localStorage` and `sessionStorage`, navigate normally, and recover the same workspace, active task, and draft from server state.
- Never contact a real LLM, broker, Redis server, or developer database.
- Preserve unrelated working-tree changes. Stage only the exact files named in each commit command.

## File Map

- Modify `app/coordinator.py`: reorder the final `done` event and `completed` state writes.
- Create `tests/unit/test_completion_event_order.py`: focused production ordering regression.
- Create `tests/e2e/__init__.py` and `tests/e2e/support/__init__.py`: importable test packages.
- Create `tests/e2e/support/deterministic_writer.py`: Celery adapter, explicit state machine, checkpoint construction, fakeredis binding, and clean background runner.
- Create `tests/e2e/conftest.py`: isolated production-app wiring and reusable public HTTP helpers.
- Create `tests/e2e/test_phase0_writing_flow.py`: automatic, interactive, cursor, durable recovery, and export contracts.
- Create `tests/frontend/api-error.test.mjs`: API error detail, retryability, and abort contracts.
- Create `tests/e2e/support/server.py`: isolated same-origin Uvicorn wrapper and visible storage-clear page.
- Create `.superpowers/sdd/2026-08-15-phase0-writing-e2e/browser-acceptance.md`: browser observations, identities, and cleanup evidence; keep this report out of product commits.

---

### Task 1: Production completion ordering

**Files:**
- Modify: `app/coordinator.py:1673-1682`
- Create: `tests/unit/test_completion_event_order.py`

**Interfaces:**
- Consumes: `app.coordinator._phase_complete(bb, task_id: str, state: dict) -> None`.
- Produces: the ordering contract `xadd_event(done)` before `set(status, completed)`.

- [ ] **Step 1: Write the failing production-order test**

```python
from app import coordinator


class RecordingBlackboard:
    def __init__(self):
        self.calls = []

    def set(self, task_id, field, value):
        self.calls.append(("set", task_id, field, value))

    def xadd_event(self, task_id, event):
        self.calls.append(("event", task_id, event))
        return "1-0"


def test_phase_complete_appends_done_before_completed(monkeypatch):
    board = RecordingBlackboard()
    monkeypatch.setattr(coordinator, "_export_draft", lambda *args: "test.md")
    monkeypatch.setattr(coordinator, "_save_task_history", lambda *args, **kwargs: None)

    coordinator._phase_complete(
        board,
        "task-1",
        {"section_texts": {1: "确定性正文"}, "review_result": {"global_score": 8}},
    )

    done_index = next(
        index for index, call in enumerate(board.calls)
        if call[0] == "event" and call[2]["event"] == "done"
    )
    completed_index = next(
        index for index, call in enumerate(board.calls)
        if call[:3] == ("set", "task-1", "status") and call[3] == "completed"
    )
    assert done_index < completed_index
```

- [ ] **Step 2: Run the focused test and verify RED**

Run: `& .\.venv\Scripts\python.exe -m pytest tests/unit/test_completion_event_order.py -q`

Expected: one failure at `assert done_index < completed_index`.

- [ ] **Step 3: Make the minimal production reorder**

Change the final writes in `_phase_complete` to this exact order:

```python
    bb.xadd_event(
        task_id,
        {"event": "done", "draft": assembled, "review": state.get("review_result")},
    )
    bb.set(task_id, "status", "completed")
    bb.set(task_id, "progress", f"完成 — 共 {count_chinese_chars(assembled)} 字")
```

- [ ] **Step 4: Run the focused and coordinator suites**

Run: `& .\.venv\Scripts\python.exe -m pytest tests/unit/test_completion_event_order.py tests/unit/test_coordinator.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit the isolated production correction**

```powershell
git add -- app/coordinator.py tests/unit/test_completion_event_order.py
git commit -m "fix: publish done before completed status"
```

---

### Task 2: Deterministic writer state machine

**Files:**
- Create: `tests/e2e/__init__.py`
- Create: `tests/e2e/support/__init__.py`
- Create: `tests/e2e/support/deterministic_writer.py`
- Create: `tests/e2e/test_deterministic_writer.py`

**Interfaces:**
- Consumes: `Blackboard`, `TaskStore`, `TaskStatus`, and `TaskState` from production.
- Produces: `DeterministicWriter(board: Blackboard, auto_run: bool = False, step_delay_ms: int = 250)`, `advance(task_id: str) -> str`, `complete(task_id: str) -> None`, `start() -> None`, and `shutdown() -> None`.
- Produces Celery-compatible `apply_async(kwargs: dict, task_id: str)`, `delay(**kwargs)`, and `AsyncResult(task_id: str)`.

- [ ] **Step 1: Write contract tests for submission, schema reuse, event sequence, and cleanup**

The tests must bind fakeredis through the real class and validate public state with production models:

```python
import fakeredis
import pytest

from app.blackboard import Blackboard
from app.config import settings
from app.models import TaskState, TaskStatus
from tests.e2e.support.deterministic_writer import DeterministicWriter


@pytest.fixture(autouse=True)
def isolated_task_db(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "TASK_DB_PATH", str(tmp_path / "tasks.db"))


def make_board():
    board = Blackboard()
    board._redis = fakeredis.FakeRedis(decode_responses=False)
    return board


def test_submission_does_not_advance_and_public_state_uses_production_models():
    board = make_board()
    writer = DeterministicWriter(board)
    writer.apply_async(
        kwargs={"topic": "雨夜", "reference_text": "参考", "interactive": False},
        task_id="task-1",
    )
    assert board._redis.xlen(board.stream_key("task-1")) == 0
    assert writer.advance("task-1") == "section_start"
    payload = {"task_id": "task-1", **board.get_all("task-1")}
    assert TaskStatus.model_validate(payload).status == "running"


def test_automatic_done_is_a_separate_advance_before_completed():
    board = make_board()
    writer = DeterministicWriter(board)
    writer.apply_async(kwargs={"topic": "雨夜", "reference_text": "参考"}, task_id="task-1")
    labels = [writer.advance("task-1") for _ in range(5)]
    assert labels == ["section_start", "token_1", "token_2", "section_end", "done"]
    events = [event for _, event in board.xread_events("task-1", "0-0")]
    assert [event["event"] for event in events] == [
        "section_start", "token", "token", "section_end", "done"
    ]
    assert board.get("task-1", "status") == "running"
    assert writer.advance("task-1") == "completed"
    assert board.get("task-1", "status") == "completed"


def test_interactive_checkpoint_is_a_production_task_state():
    board = make_board()
    writer = DeterministicWriter(board)
    writer.apply_async(
        kwargs={"topic": "雨夜", "reference_text": "参考", "interactive": True},
        task_id="task-1",
    )
    assert writer.advance("task-1") == "awaiting_outline_approval"
    checkpoint = board.load_checkpoint("task-1")
    assert TaskState.model_validate(checkpoint).status == "awaiting_outline_approval"
```

- [ ] **Step 2: Run the writer tests and verify RED**

Run: `& .\.venv\Scripts\python.exe -m pytest tests/e2e/test_deterministic_writer.py -q`

Expected: collection fails because `tests.e2e.support.deterministic_writer` does not exist.

- [ ] **Step 3: Implement the Celery adapter and explicit transitions**

Use internal dataclasses only for scenario control; serialize public checkpoint/state through production models:

```python
from dataclasses import dataclass, field
from threading import Event, Lock, Thread
from types import SimpleNamespace
from uuid import uuid4

from app.models import TaskState, TaskStatus
from app.task_store import TaskStore

TOKENS = ("雨落在旧站台。", "她终于等到回信。")
DRAFT = "".join(TOKENS)


@dataclass
class _Run:
    task_id: str
    kwargs: dict
    step: int = 0
    result: dict | None = None
    error: Exception | None = None


class _AsyncResult:
    def __init__(self, run: _Run | None):
        self._run = run

    def ready(self):
        return bool(self._run and (self._run.result is not None or self._run.error))

    def successful(self):
        return bool(self._run and self._run.result is not None)

    @property
    def result(self):
        return self._run.result if self._run else None

    @property
    def info(self):
        return self._run.error if self._run else None
```

`apply_async` and `delay` register only; neither writes runtime status or stream events. `delay` must preserve `workspace_task_id` and generate a new UUID:

```python
    def apply_async(self, kwargs, task_id):
        self._register(task_id, dict(kwargs))
        return SimpleNamespace(id=task_id)

    def delay(self, **kwargs):
        task_id = str(uuid4())
        self._register(task_id, dict(kwargs))
        return SimpleNamespace(id=task_id)

    def AsyncResult(self, task_id):
        return _AsyncResult(self._runs.get(task_id))
```

For an interactive first advance, build the checkpoint using `TaskState(...).model_dump(mode="json")`, save it, and set `awaiting_outline_approval`. For a resumed task (`resume=True`), use the replacement task's first advance as `section_start`. Automatic/resumed transitions must be exactly:

```python
("section_start", "token_1", "token_2", "section_end", "done", "completed")
```

Event payloads must be exactly:

```python
{"event": "section_start", "section": 1, "subsection": 1}
{"event": "token", "section": 1, "subsection": 1, "token": TOKENS[0]}
{"event": "token", "section": 1, "subsection": 1, "token": TOKENS[1]}
{"event": "section_end", "section": 1, "subsection": 1, "text": DRAFT}
{"event": "done", "draft": DRAFT, "review": {"global_score": 8}}
```

On the `done` step, keep runtime status `running`. On the separate `completed` step, validate `TaskStatus(task_id=..., status="completed", workspace_task_id=..., active_task_id=...)`, write task and workspace runtime fields, save `draft_backup` and `status` in `TaskStore.save_workspace`, and set `_Run.result` to a production-shaped result dict.

The background runner owns one daemon thread, waits with `Event.wait(step_delay_ms / 1000)`, advances only registered unfinished tasks, records exceptions on each run, and `shutdown()` sets the event and joins the thread with a two-second timeout.

- [ ] **Step 4: Run deterministic writer tests**

Run: `& .\.venv\Scripts\python.exe -m pytest tests/e2e/test_deterministic_writer.py -q`

Expected: all tests pass, with no thread left alive after the shutdown test.

- [ ] **Step 5: Commit the isolated test state machine**

```powershell
git add -- tests/e2e/__init__.py tests/e2e/support/__init__.py tests/e2e/support/deterministic_writer.py tests/e2e/test_deterministic_writer.py
git commit -m "test: add deterministic writing worker"
```

---

### Task 3: Automatic public-HTTP flow and mid-stream cursor resume

**Files:**
- Create: `tests/e2e/conftest.py`
- Create: `tests/e2e/test_phase0_writing_flow.py`

**Interfaces:**
- Consumes: `DeterministicWriter`, production `app.main.app`, `app.dependencies.bb`, and `app.routers.tasks.writing_task`.
- Produces: `e2e_client` fixture returning `(TestClient, DeterministicWriter, Blackboard)` and automatic route acceptance.

- [ ] **Step 1: Add the isolated app-wiring fixture**

Construct the production app only after pytest has established `WRITER_TESTING=1`. Bind one board everywhere routes resolve it:

```python
import fakeredis
import pytest
from fastapi.testclient import TestClient

from app.blackboard import Blackboard
from app.config import settings
from app.main import app
import app.dependencies as dependencies
import app.routers.tasks as task_routes
import app.routers.outline as outline_routes
from tests.e2e.support.deterministic_writer import DeterministicWriter


@pytest.fixture
def e2e_client(monkeypatch, tmp_path):
    board = Blackboard()
    board._redis = fakeredis.FakeRedis(decode_responses=False)
    writer = DeterministicWriter(board)
    test_db = tmp_path / "tasks.db"
    export_root = tmp_path / "exports"
    export_root.mkdir()
    monkeypatch.setattr(settings, "TASK_DB_PATH", str(test_db))
    monkeypatch.setattr(dependencies, "bb", board)
    monkeypatch.setattr(task_routes, "bb", board)
    monkeypatch.setattr(task_routes, "writing_task", writer)
    monkeypatch.setattr(task_routes, "_export_root", lambda: export_root)
    monkeypatch.setattr(outline_routes, "_get_redis", lambda: board._redis)
    with TestClient(app) as client:
        yield client, writer, board
    writer.shutdown()
```

- [ ] **Step 2: Write the failing automatic-flow test through public endpoints**

Use one root outline node with one leaf child. The flow must call these endpoints in order:

```python
created = client.post("/tasks").json()
workspace_id = created["workspace_task_id"]
client.patch(f"/tasks/{workspace_id}/workspace", json={
    "topic": "雨夜来信",
    "world_setting": "近未来海港城",
    "story_synopsis": "失联记者寄回最后一封信",
    "reference_text": "冷静、克制的叙述参考",
    "target_words_per_section": 500,
}).raise_for_status()
client.post(f"/tasks/{workspace_id}/outline", json={"nodes": [
    {"id": "root-1", "parentId": None, "title": "第一章", "description": "雨夜开场"},
    {"id": "leaf-1", "parentId": "root-1", "title": "旧站台", "description": "收到回信"},
]}).raise_for_status()
started = client.post("/write?mode=celery", json={
    "task_id": workspace_id,
    "topic": "雨夜来信",
    "reference_text": "冷静、克制的叙述参考",
    "target_words_per_section": 500,
}).json()
assert started["task_id"] == workspace_id
assert started["workspace_task_id"] == workspace_id
```

Advance through `section_start` and first token. Read from `0-0`, capture `mid_cursor`, and retain the event IDs:

```python
assert writer.advance(workspace_id) == "section_start"
assert writer.advance(workspace_id) == "token_1"
prefix = client.get(f"/stream/{workspace_id}?last_id=0-0&count=50").json()
assert [event[1]["event"] for event in prefix["events"]] == ["section_start", "token"]
mid_cursor = prefix["last_id"]
prefix_ids = {event[0] for event in prefix["events"]}
```

Advance through `done`, resume from the mid-stream cursor, and assert no prefix ID or token is replayed:

```python
assert [writer.advance(workspace_id) for _ in range(3)] == ["token_2", "section_end", "done"]
suffix = client.get(f"/stream/{workspace_id}?last_id={mid_cursor}&count=50").json()
assert [event[1]["event"] for event in suffix["events"]] == ["token", "section_end", "done"]
assert prefix_ids.isdisjoint({event[0] for event in suffix["events"]})
assert suffix["events"][0][1]["token"] == "她终于等到回信。"
```

Assert ordering by observing `done` while production status is nonterminal, then perform the distinct completion advance:

```python
assert suffix["events"][-1][1]["event"] == "done"
before_completed = client.get(f"/status/{workspace_id}").json()
assert before_completed["status"] == "running"
assert writer.advance(workspace_id) == "completed"
assert client.get(f"/status/{workspace_id}").json()["status"] == "completed"
final_cursor = suffix["last_id"]
assert client.get(f"/stream/{workspace_id}?last_id={final_cursor}&count=50").json()["events"] == []
```

- [ ] **Step 3: Run the automatic-flow test and verify RED**

Run: `& .\.venv\Scripts\python.exe -m pytest tests/e2e/test_phase0_writing_flow.py::test_automatic_writing_cursor_order_recovery_and_exports -q`

Expected: failure at the first missing state-machine persistence or route-wiring assertion.

- [ ] **Step 4: Complete deterministic workspace persistence and export assertions**

After completion, assert `/tasks/{id}/workspace` and `/projects` return the same durable identity. Then create and download all formats:

```python
workspace = client.get(f"/tasks/{workspace_id}/workspace").json()
assert workspace["workspace_task_id"] == workspace_id
assert workspace["active_task_id"] == workspace_id
assert workspace["draft_backup"] == DRAFT
projects = client.get("/projects").json()["projects"]
assert any(p["workspace_task_id"] == workspace_id and p["draft_backup"] == DRAFT for p in projects)

for export_format in ("md", "txt", "json"):
    record = client.post(
        f"/tasks/{workspace_id}/exports", json={"format": export_format}
    ).json()
    download = client.get(
        f"/tasks/{workspace_id}/exports/{record['export_id']}/download"
    )
    assert download.status_code == 200
    assert DRAFT in download.text
```

If JSON escaping prevents the exact string check, decode `download.json()` and assert `download.json()["draft"] == DRAFT`; keep exact string assertions for Markdown and TXT.

- [ ] **Step 5: Run the automatic flow and deterministic writer suites**

Run: `& .\.venv\Scripts\python.exe -m pytest tests/e2e/test_deterministic_writer.py tests/e2e/test_phase0_writing_flow.py::test_automatic_writing_cursor_order_recovery_and_exports -q`

Expected: all tests pass.

- [ ] **Step 6: Commit the automatic HTTP slice**

```powershell
git add -- tests/e2e/conftest.py tests/e2e/test_phase0_writing_flow.py tests/e2e/support/deterministic_writer.py
git commit -m "test: cover automatic writing end to end"
```

---

### Task 4: Interactive replacement-task flow

**Files:**
- Modify: `tests/e2e/test_phase0_writing_flow.py`
- Modify: `tests/e2e/support/deterministic_writer.py`

**Interfaces:**
- Consumes: production `/tasks/{task_id}/decide` semantics and `TaskState` checkpoints.
- Produces: stable `workspace_task_id`, distinct `new_task_id`, original-to-active replacement linkage, and independent replacement cursor.

- [ ] **Step 1: Write the failing interactive test**

Create/configure a workspace as in the automatic test, submit with `mode=interactive`, and advance once:

```python
started = client.post("/write?mode=interactive", json={
    "task_id": workspace_id,
    "topic": "雨夜来信",
    "reference_text": "冷静、克制的叙述参考",
    "target_words_per_section": 500,
}).json()
old_task_id = started["task_id"]
assert writer.advance(old_task_id) == "awaiting_outline_approval"
old_status = client.get(f"/status/{old_task_id}").json()
assert old_status["status"] == "awaiting_outline_approval"
assert TaskState.model_validate(board.load_checkpoint(old_task_id))
```

Approve through the public route and assert identity semantics:

```python
decision = client.post(
    f"/tasks/{old_task_id}/decide?phase=outline&action=approve"
).json()
new_task_id = decision["new_task_id"]
assert new_task_id != old_task_id
assert decision["workspace_task_id"] == workspace_id
assert client.get(f"/status/{old_task_id}").json()["active_task_id"] == new_task_id
workspace = client.get(f"/tasks/{workspace_id}/workspace").json()
assert workspace["active_task_id"] == new_task_id
```

Complete the replacement and prove its stream starts independently:

```python
writer.complete(new_task_id)
replacement = client.get(f"/stream/{new_task_id}?last_id=0-0&count=50").json()
assert replacement["events"][0][1]["event"] == "section_start"
assert replacement["events"][-1][1]["event"] == "done"
assert replacement["last_id"] != "0-0"
assert client.get(f"/status/{new_task_id}").json()["status"] == "completed"
```

- [ ] **Step 2: Run the interactive test and verify RED**

Run: `& .\.venv\Scripts\python.exe -m pytest tests/e2e/test_phase0_writing_flow.py::test_interactive_approval_replaces_task_and_preserves_workspace -q`

Expected: failure until resume registration copies checkpoint/workspace linkage into the replacement run.

- [ ] **Step 3: Implement resume registration and original-task linkage**

When `delay` receives `resume=True` and `resume_from_task_id`, load that production checkpoint, validate it with `TaskState.model_validate`, copy only its production fields into the new run, and keep the supplied `workspace_task_id`. On replacement registration set its runtime status to `pending` only after the route returns control to route-owned state. On the first replacement advance, set the old task's `active_task_id` and workspace anchor only through the same board fields the production route reads.

`complete(task_id)` must call `advance` until the returned label is `completed`; it must raise the stored exception and stop after eight transitions to prevent a runaway harness.

- [ ] **Step 4: Run both Phase 0 flows**

Run: `& .\.venv\Scripts\python.exe -m pytest tests/e2e/test_phase0_writing_flow.py -q`

Expected: automatic and interactive tests pass.

- [ ] **Step 5: Commit the interactive slice**

```powershell
git add -- tests/e2e/support/deterministic_writer.py tests/e2e/test_phase0_writing_flow.py
git commit -m "test: cover interactive task replacement"
```

---

### Task 5: Frontend API error semantics

**Files:**
- Create: `tests/frontend/api-error.test.mjs`
- Modify: `app/static/js/api.js` only if a RED assertion proves current behavior is wrong.

**Interfaces:**
- Consumes: exported `req` and `ApiError` from `app/static/js/api.js`.
- Produces: stable `status`, `detail`, `retryable`, `url`, and abort message contracts.

- [ ] **Step 1: Write table-driven Node tests against the production API module**

```javascript
import test from 'node:test';
import assert from 'node:assert/strict';

globalThis.window = { location: { origin: 'http://writer.test' } };
const API = await import(`../../app/static/js/api.js?error-test=${Date.now()}`);

function response(status, payload) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: () => 'application/json' },
    json: async () => payload,
    text: async () => JSON.stringify(payload),
  };
}

for (const sample of [
  { status: 401, detail: 'API Key 无效', retryable: false },
  { status: 403, detail: '无权执行', retryable: false },
  { status: 429, detail: { code: 'rate_limit', message: '稍后重试' }, retryable: true },
  { status: 500, detail: { code: 'writer_failed', message: '写作失败' }, retryable: true },
]) {
  test(`HTTP ${sample.status} preserves detail and retryability`, async () => {
    globalThis.fetch = async () => response(sample.status, { detail: sample.detail });
    await assert.rejects(
      API.req('/contract', { timeoutMs: 0 }),
      error => {
        assert.equal(error.status, sample.status);
        assert.deepEqual(error.detail, sample.detail);
        assert.equal(error.retryable, sample.retryable);
        assert.equal(error.url, '/contract');
        return true;
      },
    );
  });
}

test('AbortError remains retryable and actionable', async () => {
  globalThis.fetch = async () => { throw new DOMException('aborted', 'AbortError'); };
  await assert.rejects(
    API.req('/contract', { timeoutMs: 0 }),
    error => error.message === '请求超时或已取消' && error.retryable === true,
  );
});
```

- [ ] **Step 2: Run the Node error suite**

Run: `node --test tests/frontend/api-error.test.mjs`

Expected: all tests pass on the current production client. If a test fails, preserve the structured `detail` object and set retryability only for 429, 5xx, network errors, and aborts.

- [ ] **Step 3: Run all frontend Node contracts**

Run: `node --test tests/frontend/*.test.mjs`

Expected: all tests pass.

- [ ] **Step 4: Commit the error contracts**

```powershell
git add -- tests/frontend/api-error.test.mjs app/static/js/api.js
git commit -m "test: lock frontend API error semantics"
```

If `api.js` remains unchanged, stage only `tests/frontend/api-error.test.mjs`.

---

### Task 6: Isolated browser support server and visible storage reset

**Files:**
- Create: `tests/e2e/support/server.py`
- Create: `tests/e2e/test_support_server.py`

**Interfaces:**
- Consumes: `DeterministicWriter(..., auto_run=True)`, production FastAPI app, and exact environment variables `WRITER_TESTING`, `TASK_DB_PATH`, `WRITER_E2E_RUNTIME_DIR`, `WRITER_E2E_SCENARIO`, `WRITER_E2E_STEP_DELAY_MS`.
- Produces: `create_support_app() -> FastAPI`, `/__e2e__/clear-storage`, and module entry point `python -m tests.e2e.support.server`.

- [ ] **Step 1: Write isolation and storage-page tests**

```python
def test_server_refuses_non_test_mode(monkeypatch):
    monkeypatch.setenv("WRITER_TESTING", "0")
    with pytest.raises(RuntimeError, match="WRITER_TESTING=1"):
        validate_environment()


def test_server_refuses_database_outside_owned_runtime(monkeypatch, tmp_path):
    runtime = tmp_path / "owned"
    runtime.mkdir()
    monkeypatch.setenv("WRITER_TESTING", "1")
    monkeypatch.setenv("WRITER_E2E_RUNTIME_DIR", str(runtime))
    monkeypatch.setenv("TASK_DB_PATH", str(tmp_path / "outside.db"))
    with pytest.raises(RuntimeError, match="inside WRITER_E2E_RUNTIME_DIR"):
        validate_environment()


def test_storage_reset_page_clears_both_stores_and_navigates(client):
    response = client.get("/__e2e__/clear-storage")
    assert response.status_code == 200
    assert "localStorage.clear()" in response.text
    assert "sessionStorage.clear()" in response.text
    assert "window.location.assign('/write-ui-v2')" in response.text
```

- [ ] **Step 2: Run support-server tests and verify RED**

Run: `& .\.venv\Scripts\python.exe -m pytest tests/e2e/test_support_server.py -q`

Expected: import failure because `server.py` does not exist.

- [ ] **Step 3: Implement strict environment validation before production imports**

`validate_environment()` must resolve both paths and require the database's parent chain to contain the runtime root:

```python
def validate_environment():
    if os.environ.get("WRITER_TESTING") != "1":
        raise RuntimeError("browser E2E requires WRITER_TESTING=1")
    runtime = Path(os.environ["WRITER_E2E_RUNTIME_DIR"]).resolve()
    database = Path(os.environ["TASK_DB_PATH"]).resolve()
    if runtime != database.parent and runtime not in database.parents:
        raise RuntimeError("TASK_DB_PATH must be inside WRITER_E2E_RUNTIME_DIR")
    if os.environ.get("WRITER_E2E_SCENARIO") not in {"automatic", "interactive"}:
        raise RuntimeError("WRITER_E2E_SCENARIO must be automatic or interactive")
    return runtime, database
```

Call it before importing `app.main`, `app.dependencies`, route modules, or `TaskStore`.

- [ ] **Step 4: Implement the same-origin wrapper and lifespan cleanup**

Create a new wrapper `FastAPI` instance. Register the test route before mounting production at `/`:

```python
@support.get("/__e2e__/clear-storage", response_class=HTMLResponse)
def clear_storage():
    return """<!doctype html><html><body>
    <button id="clear-and-return" onclick="localStorage.clear();sessionStorage.clear();window.location.assign('/write-ui-v2')">
      清除浏览器缓存并返回写作页
    </button></body></html>"""

support.mount("/", production_app)
```

Install one fakeredis-backed production `Blackboard` into `app.dependencies.bb` and `app.routers.tasks.bb`, replace `writing_task`, and patch outline `_get_redis`. Replace `app.routers.tasks._export_root` with a function returning `WRITER_E2E_RUNTIME_DIR / "exports"`, creating that directory before serving; this keeps generated files out of `app/output`. Start the deterministic runner during wrapper lifespan and call `writer.shutdown()` in `finally`.

Run Uvicorn with `host="127.0.0.1"` and `port=int(os.environ.get("WRITER_E2E_PORT", "2947"))`. Do not enable reload or multiple workers.

- [ ] **Step 5: Run support-server and all E2E tests**

Run: `& .\.venv\Scripts\python.exe -m pytest tests/e2e -q`

Expected: all tests pass and the process exits without a live writer thread.

- [ ] **Step 6: Commit the browser harness**

```powershell
git add -- tests/e2e/support/server.py tests/e2e/test_support_server.py
git commit -m "test: add isolated browser E2E server"
```

---

### Task 7: Real-browser recovery acceptance and final verification

**Files:**
- Create (ignored report): `.superpowers/sdd/2026-08-15-phase0-writing-e2e/browser-acceptance.md`
- Modify only if browser acceptance exposes a production defect: the smallest affected file under `app/static/js/` plus its focused Node test.

**Interfaces:**
- Consumes: the support server on `http://127.0.0.1:2947` and visible production page controls.
- Produces: observed automatic/interactive acceptance evidence and a cleanly stopped exact server process.

- [ ] **Step 1: Start an isolated automatic support server**

Create an owned temporary directory with PowerShell `New-Item`, set:

```text
WRITER_TESTING=1
WRITER_E2E_SCENARIO=automatic
WRITER_E2E_STEP_DELAY_MS=600
WRITER_E2E_PORT=2947
WRITER_E2E_RUNTIME_DIR=<resolved owned temporary directory>
TASK_DB_PATH=<that directory>\tasks.db
```

Run: `& .\.venv\Scripts\python.exe -m tests.e2e.support.server`

Track the returned exact process/session. Do not use a wildcard process kill.

- [ ] **Step 2: Execute automatic browser acceptance**

Using the in-app browser skill, navigate to `http://127.0.0.1:2947/write-ui-v2`. Through visible controls:

1. Create/open the isolated project.
2. Fill topic/reference/outline fields required by the page.
3. Select automatic mode and start writing.
4. Observe the two deterministic text chunks and completion UI.
5. Record the visible project/workspace identity, active task identity if shown, and final draft text.
6. Navigate to `http://127.0.0.1:2947/__e2e__/clear-storage` and click `清除浏览器缓存并返回写作页`.
7. From the server-provided project list, reopen the same project.
8. Assert the same workspace, same active task, and exact final draft are restored without re-entering data.
9. Open export controls and verify at least one export is available/downloadable.

Do not inspect browser storage values directly; the visible support page performs the required clears.

- [ ] **Step 3: Execute interactive browser acceptance on a fresh isolated server**

Stop the exact automatic server process, verify it exited, create a second owned runtime directory, set `WRITER_E2E_SCENARIO=interactive`, and start the same module on port 2947.

Through visible controls:

1. Create/configure a project and select interactive mode.
2. Start writing and observe the outline confirmation state.
3. Approve through the production UI.
4. Observe that generation continues under a replacement task while the project/workspace anchor remains unchanged.
5. Wait for deterministic draft completion.
6. Use the visible storage-reset page to clear both stores and return.
7. Reopen the same server project and assert the replacement task remains active and its draft is restored.

- [ ] **Step 4: Record browser evidence and cleanup**

Write the report with these exact headings:

```markdown
# Phase 0 Browser Acceptance

## Automatic
- Server runtime directory:
- Workspace identity before reset:
- Active task identity before reset:
- Draft observed:
- Workspace identity after reset:
- Active task identity after reset:
- Export observed:

## Interactive
- Server runtime directory:
- Original task identity:
- Replacement task identity:
- Workspace identity before/after replacement:
- Active task after storage reset:
- Draft restored after storage reset:

## Cleanup
- Automatic server exit:
- Interactive server exit:
- Temporary paths verified inside owned runtime roots:
```

Stop the exact interactive server process. Remove only the two resolved owned runtime directories after verifying each path is inside the intended temporary parent. State whether removed files are recoverable.

- [ ] **Step 5: Run the full verification gate**

Run these commands independently and record exit codes:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests/e2e tests/unit/test_completion_event_order.py tests/unit/test_coordinator.py -q
node --test tests/frontend/*.test.mjs
& .\.venv\Scripts\python.exe -m compileall -q app tests/e2e
node --check app/static/js/api.js
node --check app/static/js/main.js
git diff --check
```

Expected: every command exits 0. `git diff --check` may report only pre-existing unrelated defects; if it does, rerun it scoped to the files changed by this plan and record both outputs without editing unrelated user work.

- [ ] **Step 6: Review and handoff**

Inspect the scoped diff for production schema reuse, exact done/completed ordering, mid-stream cursor assertion, storage-clear route isolation, server-state recovery, thread cleanup, and absence of real infrastructure calls. Commit only product/test files that remain uncommitted; do not commit `.superpowers/sdd` reports or unrelated dirty files.
