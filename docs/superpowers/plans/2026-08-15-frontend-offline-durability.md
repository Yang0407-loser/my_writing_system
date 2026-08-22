# Frontend Offline Durability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep the upgraded writing workspace readable and saveable from SQLite when Redis is temporarily unavailable, while showing the runtime connection as degraded.

**Architecture:** Redis remains the live task/stream store, while `TaskStore.project_workspaces` is the durable project source. Project reads and writes try Redis when available but must not let a Redis connection error prevent SQLite recovery or persistence. Status responses identify durable fallback explicitly so the existing Vue page can show reconnection state and keep polling.

**Tech Stack:** FastAPI, Pydantic, Redis, SQLite, Vue 3 ES modules, pytest

## Global Constraints

- Preserve all existing response fields and routes.
- Do not make SQLite a replacement for live stream events or Celery task control.
- Do not delete or rewrite unrelated frozen experiment artifacts.
- Use existing `TaskStore` workspace rows; add no new dependency.
- Every production change follows a failing-test-first cycle.

---

### Task 1: Durable workspace fallback when Redis is unavailable

**Files:**
- Modify: `tests/unit/test_frontend_api_upgrade_routes.py`
- Modify: `app/models.py`
- Modify: `app/routers/tasks.py`
- Modify: `app/routers/outline.py`

**Interfaces:**
- Consumes: `TaskStore.find_workspace_for_task(task_id) -> dict | None`
- Produces: `TaskStatus.runtime_available: bool`, `TaskStatus.data_source: str | None`
- Produces: Redis-tolerant status, workspace, outline, draft, and export route behavior

- [x] **Step 1: Write failing Redis-outage route tests**

```python
class UnavailableBoard:
    def get_all(self, _task_id):
        raise RedisConnectionError("redis unavailable")

    def get(self, _task_id, _key):
        raise RedisConnectionError("redis unavailable")

    def set(self, _task_id, _key, _value):
        raise RedisConnectionError("redis unavailable")


def test_status_falls_back_to_durable_workspace_when_redis_is_unavailable(...):
    ...
    assert result.runtime_available is False
    assert result.data_source == "durable_workspace"
    assert result.draft == "已保存正文"
```

- [x] **Step 2: Run the outage tests and verify the expected Redis exception failure**

Run: `python -m pytest tests/unit/test_frontend_api_upgrade_routes.py -q`

Expected: FAIL because the current routes call Blackboard before their SQLite fallback.

- [x] **Step 3: Add minimal durable fallback helpers and route branches**

```python
def _runtime_data(task_id: str) -> tuple[dict, bool]:
    try:
        return bb.get_all(task_id), True
    except RedisError:
        logger.warning("Redis unavailable for task %s", task_id)
        return {}, False
```

Status and workspace routes load `TaskStore.find_workspace_for_task()` when the helper reports an outage. Outline and draft routes persist SQLite independently from best-effort Redis mirroring. Export source/history/download routes use the same durable fallback.

- [x] **Step 4: Run route and store regressions**

Run: `python -m pytest tests/unit/test_frontend_api_upgrade_routes.py tests/unit/test_project_workspace_store.py tests/unit/test_task_routes.py tests/unit/test_task_routes_canonical_fields.py tests/unit/test_task_store.py tests/unit/test_task_store_canonical_refs.py -q`

Expected: PASS.

### Task 2: Surface degraded runtime state in the Vue workspace

**Files:**
- Add: `tests/frontend/runtime-status.test.mjs`
- Modify: `app/static/js/main.js`
- Add: `app/static/js/runtime-status.mjs`
- Modify: `app/static/index.html`

**Interfaces:**
- Consumes: `GET /status/{task_id}` fields `runtime_available` and `data_source`
- Produces: `connectionState === "reconnecting"` while durable fallback is active

- [x] **Step 1: Add a failing frontend unit test for durable fallback handling**

```javascript
assert.equal(connectionStateFromStatus({ runtime_available: false }), 'reconnecting');
assert.equal(connectionStateFromStatus({ runtime_available: true }), 'online');
```

- [x] **Step 2: Run the frontend unit test and verify it fails**

Run: `node --test tests/frontend/runtime-status.test.mjs`

Expected: FAIL because the runtime-status module does not exist yet.

- [x] **Step 3: Handle durable fallback before marking a poll successful**

```javascript
if (status.runtime_available === false) {
  connectionState.value = 'reconnecting';
} else {
  noteSuccess('status');
}
```

- [x] **Step 4: Run targeted tests and local browser verification**

Run: `python -m pytest tests/unit/test_frontend_upgrade_contract.py tests/unit/test_frontend_api_upgrade_routes.py tests/unit/test_project_workspace_store.py -q`

Expected: PASS. With Redis stopped and a durable project present, the browser shows the saved project and a reconnecting runtime state; workspace autosave returns success.
