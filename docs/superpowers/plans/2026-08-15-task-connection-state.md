# Task Connection State Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give restored and active writing sessions deterministic online, reconnecting, manual-retry, and offline behavior without sacrificing SQLite-backed editing during Redis degradation.

**Architecture:** A pure ES module owns connection-health transitions and a small session wrapper owns cancellation and stale-response isolation. `main.js` adapts the existing status/stream polling loops to these modules, while `api.js` accepts caller-provided abort signals. The footer exposes one compact retry action after the controller's ten-second threshold.

**Tech Stack:** Vue 3 ES modules, browser Fetch/AbortController, Node.js built-in test runner, pytest static-template contract tests

## Global Constraints

- The connection indicator states are exactly `online`, `reconnecting`, and `offline`.
- Three consecutive failures on either transport channel enter `reconnecting`.
- Manual retry becomes available exactly 10,000 ms after transport degradation begins.
- Transport state becomes `offline` exactly 30,000 ms after degradation begins.
- `runtime_available=false` is durable Redis degradation: keep project reads/writes available, pause high-frequency stream polling, and never promote to transport `offline` solely because of that flag.
- Retiring a task session aborts in-flight status and stream requests; stale callbacks must not mutate the active Vue session.
- Do not replace polling with WebSocket/SSE or introduce a frontend build dependency.
- Preserve all pre-existing user changes. `main.js` and `api.js` are already dirty, so do not commit overlapping files; record commands, results, and exact changed paths in the SDD task report instead.

---

### Task 1: Pure connection controller, session guard, and API cancellation

**Files:**
- Create: `app/static/js/task-connection.mjs`
- Create: `app/static/js/task-polling-session.mjs`
- Create: `tests/frontend/task-connection.test.mjs`
- Create: `tests/frontend/api-signal.test.mjs`
- Modify: `app/static/js/api.js`

**Interfaces:**
- Produces: `createTaskConnectionController(options?) -> TaskConnectionController`
- Produces: controller methods `snapshot()`, `failureCount(channel)`, `recordFailure(channel)`, `recordSuccess(channel)`, `setRuntimeAvailable(value)`, `beginManualRetry(channels?)`, and `retire()`
- Produces: snapshot fields `state`, `canManualRetry`, `shouldPollStream`, `nextTransitionAt`, `runtimeAvailable`, and `retired`
- Produces: `createTaskPollingSession(taskId, connectionController, AbortControllerClass?) -> TaskPollingSession`
- Produces: session fields/methods `taskId`, `signal`, `isActive()`, `runIfActive(callback)`, and `retire()`
- Modifies: `API.getStatus(id, options?)` and `API.getStream(id, lastId, options?)` to consume `options.signal`

- [ ] **Step 1: Write failing controller transition tests**

Create `tests/frontend/task-connection.test.mjs` with literal, fake-clock expectations:

```javascript
import test from 'node:test';
import assert from 'node:assert/strict';
import { createTaskConnectionController } from '../../app/static/js/task-connection.mjs';
import { createTaskPollingSession } from '../../app/static/js/task-polling-session.mjs';

test('third transport failure exposes retry at 10s and offline protection at 30s', () => {
  let now = 1_000;
  const controller = createTaskConnectionController({ now: () => now });

  controller.recordFailure('status');
  controller.recordFailure('status');
  assert.equal(controller.snapshot().state, 'online');

  controller.recordFailure('status');
  assert.deepEqual(controller.snapshot(), {
    state: 'reconnecting',
    canManualRetry: false,
    shouldPollStream: true,
    nextTransitionAt: 11_000,
    runtimeAvailable: true,
    retired: false,
  });

  now = 11_000;
  assert.equal(controller.snapshot().canManualRetry, true);
  assert.equal(controller.snapshot().nextTransitionAt, 31_000);

  now = 31_000;
  assert.equal(controller.snapshot().state, 'offline');
  assert.equal(controller.snapshot().shouldPollStream, false);
  assert.equal(controller.snapshot().nextTransitionAt, null);
});

test('runtime degradation pauses stream without becoming transport offline', () => {
  let now = 0;
  const controller = createTaskConnectionController({ now: () => now });

  controller.setRuntimeAvailable(false);
  now = 60_000;
  assert.equal(controller.snapshot().state, 'reconnecting');
  assert.equal(controller.snapshot().canManualRetry, false);
  assert.equal(controller.snapshot().shouldPollStream, false);

  controller.recordFailure('stream');
  controller.setRuntimeAvailable(true);
  assert.equal(controller.snapshot().state, 'online');
});

test('manual retry stays reconnecting until both requested channels recover', () => {
  let now = 0;
  const controller = createTaskConnectionController({ now: () => now });
  controller.recordFailure('status');
  controller.recordFailure('status');
  controller.recordFailure('status');
  now = 10_000;

  controller.beginManualRetry();
  assert.equal(controller.snapshot().state, 'reconnecting');
  assert.equal(controller.snapshot().canManualRetry, false);
  controller.recordSuccess('status');
  assert.equal(controller.snapshot().state, 'reconnecting');
  controller.recordSuccess('stream');
  assert.equal(controller.snapshot().state, 'online');
});

test('retired polling session aborts requests and ignores stale callbacks', () => {
  const controller = createTaskConnectionController();
  const session = createTaskPollingSession('task-old', controller);
  let mutations = 0;

  session.retire();
  session.runIfActive(() => { mutations += 1; });

  assert.equal(session.signal.aborted, true);
  assert.equal(session.isActive(), false);
  assert.equal(controller.snapshot().retired, true);
  assert.equal(mutations, 0);
});
```

- [ ] **Step 2: Run the controller tests and verify the missing-module failure**

Run:

```powershell
& 'C:\Users\yangjieba\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe' --test tests\frontend\task-connection.test.mjs
```

Expected: FAIL with `ERR_MODULE_NOT_FOUND` for `task-connection.mjs`.

- [ ] **Step 3: Implement the minimal pure connection controller**

Create `app/static/js/task-connection.mjs`. Validate channel names against `status` and `stream`; ignore calls after retirement. Start the outage clock only when a channel reaches the failure threshold. `snapshot()` derives thresholds from the injected clock and returns only the six documented fields.

Use these exact defaults:

```javascript
export function createTaskConnectionController({
  now = () => Date.now(),
  failureThreshold = 3,
  retryAfterMs = 10_000,
  offlineAfterMs = 30_000,
} = {}) {
  // state and methods described by the tests above
}
```

When `runtimeAvailable` is false, ignore stream failures and return `shouldPollStream=false`. `beginManualRetry()` resets transport failures, starts a fresh outage clock, and waits for both `status` and `stream` successes before returning online.

- [ ] **Step 4: Implement the minimal polling-session guard**

Create `app/static/js/task-polling-session.mjs`:

```javascript
export function createTaskPollingSession(
  taskId,
  connectionController,
  AbortControllerClass = AbortController,
) {
  const abortController = new AbortControllerClass();
  let active = true;
  return {
    taskId,
    signal: abortController.signal,
    isActive: () => active,
    runIfActive(callback) {
      if (active) return callback();
    },
    retire() {
      if (!active) return;
      active = false;
      connectionController.retire();
      abortController.abort();
    },
  };
}
```

- [ ] **Step 5: Run controller tests and verify they pass**

Run the Step 2 command.

Expected: 4 tests pass, 0 fail.

- [ ] **Step 6: Write failing API wrapper signal tests**

Create `tests/frontend/api-signal.test.mjs`:

```javascript
import test from 'node:test';
import assert from 'node:assert/strict';

globalThis.window = { location: { origin: 'http://writer.test' } };
const requests = [];
globalThis.fetch = async (url, options) => {
  requests.push({ url, options });
  return {
    ok: true,
    status: 200,
    headers: { get: () => 'application/json' },
    json: async () => ({ status: 'running', events: [], last_id: '0-0' }),
  };
};

const API = await import(`../../app/static/js/api.js?signal-test=${Date.now()}`);

test('status wrapper connects the caller abort signal to fetch', async () => {
  const statusAbort = new AbortController();
  statusAbort.abort();
  await API.getStatus('task-1', { signal: statusAbort.signal });
  assert.equal(requests.at(-1).options.signal.aborted, true);
});

test('stream wrapper connects the caller abort signal to fetch', async () => {
  const streamAbort = new AbortController();
  streamAbort.abort();
  await API.getStream('task-1', '4-2', { signal: streamAbort.signal });
  assert.equal(requests.at(-1).options.signal.aborted, true);
});
```

- [ ] **Step 7: Run the signal test and verify it fails for dropped options**

Run:

```powershell
& 'C:\Users\yangjieba\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe' --test tests\frontend\api-signal.test.mjs
```

Expected: FAIL because `getStatus` and `getStream` ignore the second/third options argument, leaving the Fetch signal un-aborted.

- [ ] **Step 8: Pass caller signals through the API wrappers**

Modify `app/static/js/api.js`:

```javascript
export const getStatus=(id,options={})=>req('/status/'+id,{timeoutMs:15000,signal:options.signal});
export const getStream=(id,lastId,options={})=>req('/stream/'+id+'?last_id='+encodeURIComponent(lastId)+'&count=50',{timeoutMs:15000,signal:options.signal});
```

- [ ] **Step 9: Run all Task 1 tests and syntax checks**

Run:

```powershell
& 'C:\Users\yangjieba\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe' --test tests\frontend\task-connection.test.mjs tests\frontend\api-signal.test.mjs tests\frontend\runtime-status.test.mjs
& 'C:\Users\yangjieba\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe' --check app\static\js\task-connection.mjs
& 'C:\Users\yangjieba\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe' --check app\static\js\task-polling-session.mjs
& 'C:\Users\yangjieba\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe' --check app\static\js\api.js
```

Expected: 8 tests pass and every syntax check exits 0.

- [ ] **Step 10: Record Task 1 instead of committing overlapping dirty files**

Write the exact paths, red/green commands, outputs, and self-review findings to the SDD Task 1 report. Do not stage or commit `app/static/js/api.js`.

---

### Task 2: Integrate connection lifecycle into Vue polling and the footer

**Files:**
- Modify: `app/static/js/main.js`
- Modify: `app/static/index.html`
- Modify: `app/static/styles/base.css`
- Modify: `tests/unit/test_frontend_upgrade_contract.py`
- Test: `tests/frontend/task-connection.test.mjs`

**Interfaces:**
- Consumes: `createTaskConnectionController()` from Task 1
- Consumes: `createTaskPollingSession(taskId, controller)` from Task 1
- Consumes: signal-aware `API.getStatus(id, {signal})` and `API.getStream(id, lastId, {signal})`
- Produces: Vue refs `connectionRetryAvailable` and `connectionRetryBusy`
- Produces: Vue action `retryConnection()`
- Produces: `beginPolling(flatOutline, keepExisting=false, options={manualRetry:false})`

- [ ] **Step 1: Add failing frontend template contract assertions**

Extend `tests/unit/test_frontend_upgrade_contract.py`:

```python
def test_loaded_frontend_exposes_connection_retry_control():
    template = (ROOT / "app/static/index.html").read_text(encoding="utf-8")
    main_js = (ROOT / "app/static/js/main.js").read_text(encoding="utf-8")

    assert 'v-if="connectionRetryAvailable"' in template
    assert '@click="retryConnection"' in template
    assert 'createTaskConnectionController' in main_js
    assert 'createTaskPollingSession' in main_js
    assert 'beginPolling(null,true,{manualRetry:true})' in main_js
```

This test catches removal of the only user-visible recovery action and failure to wire it to the session controller.

- [ ] **Step 2: Run the contract test and verify it fails**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\unit\test_frontend_upgrade_contract.py::test_loaded_frontend_exposes_connection_retry_control -q
```

Expected: FAIL because the retry control and controller imports are absent.

- [ ] **Step 3: Add imports and session-level Vue state**

At the top of `main.js`, import the two Task 1 modules with cache version `v=20260815b`. Near the existing connection state, add:

```javascript
const connectionRetryAvailable = ref(false);
const connectionRetryBusy = ref(false);
```

Keep `let activePollingSession = null` beside `pollTimers`.

- [ ] **Step 4: Retire sessions in `stopPolling()`**

Change `stopPolling()` so it clears both timers, retires `activePollingSession`, sets it to `null`, and hides the retry action. Aborts caused by retirement must not be passed to `noteFailure` because callbacks first check `session.isActive()`.

- [ ] **Step 5: Replace local counters with the connection controller**

Change the signature to:

```javascript
function beginPolling(flatOutline, keepExisting=false, options={manualRetry:false})
```

After capturing `pollingTaskId`, create a controller and polling session. If `options.manualRetry` is true, call `controller.beginManualRetry()` before the first request.

Add a `syncConnectionState()` helper that copies `snapshot().state` and `snapshot().canManualRetry` into the Vue refs and schedules one local health timer for `snapshot().nextTransitionAt`. Store that timer on the active session so retirement clears it.

`noteSuccess` and `noteFailure` call the controller, synchronize state, and preserve the existing one-time reconnect/recovered toasts. Retry delay uses the controller's public `failureCount(channel)` method; do not inspect private fields.

- [ ] **Step 6: Wire status and stream calls to the active session**

Call:

```javascript
await API.getStatus(pollingTaskId, {signal: session.signal});
await API.getStream(pollingTaskId, lastId, {signal: session.signal});
```

Immediately after each `await` and inside each `catch`, return if `!session.isActive()` or `activePollingSession !== session`.

After status success, call `controller.setRuntimeAvailable(d.runtime_available !== false)`. When `snapshot().shouldPollStream` is false, do not request the stream; schedule the next stream check after 15,000 ms. A later status response with runtime available allows the next stream check to resume.

- [ ] **Step 7: Recover when the initial status load fails**

Make `initTaskSession()` return a discriminated result:

```javascript
{ status, loadFailed }
```

On first status failure, keep the workspace ID, set a user-readable reconnecting status, and return `{status:null, loadFailed:true}`. In `onMounted`, when a restored task exists and `loadFailed` is true, call `beginPolling(null,true)` so controlled recovery continues instead of silently stopping.

- [ ] **Step 8: Add the manual retry action**

Expose this action from Vue setup:

```javascript
function retryConnection(){
  if(!taskId.value || connectionRetryBusy.value) return;
  connectionRetryBusy.value = true;
  beginPolling(null,true,{manualRetry:true});
  connectionRetryBusy.value = false;
}
```

Return `connectionRetryAvailable`, `connectionRetryBusy`, and `retryConnection` from setup.

- [ ] **Step 9: Add the compact footer control and focus styling**

Place immediately after the connection pill in `index.html`:

```html
<button v-if="connectionRetryAvailable" class="connection-retry-btn" @click="retryConnection" :disabled="connectionRetryBusy">重试连接</button>
```

Add CSS that reuses existing border, gold, panel, and focus tokens; use no new palette values:

```css
.connection-retry-btn {
  min-height: 22px;
  padding: 1px 7px;
  border: 1px solid var(--gold-dim);
  border-radius: 3px;
  color: var(--gold);
  background: var(--surface-1);
  font: inherit;
  cursor: pointer;
}
.connection-retry-btn:hover { color: var(--text); border-color: var(--gold); }
.connection-retry-btn:focus-visible { outline: 2px solid var(--gold); outline-offset: 2px; }
.connection-retry-btn:disabled { cursor: wait; opacity: .55; }
```

- [ ] **Step 10: Run targeted tests and syntax checks**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\unit\test_frontend_upgrade_contract.py tests\unit\test_frontend_api_upgrade_routes.py tests\unit\test_project_workspace_store.py -q
& 'C:\Users\yangjieba\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe' --test tests\frontend\task-connection.test.mjs tests\frontend\api-signal.test.mjs tests\frontend\runtime-status.test.mjs
& 'C:\Users\yangjieba\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe' --check app\static\js\main.js
```

Expected: all targeted Python and Node tests pass, and syntax check exits 0.

- [ ] **Step 11: Verify browser behavior**

With the local server running and a durable project available:

1. Stop Redis and reload `/write-ui-v2`: project content remains available; footer shows `重连中`; no manual retry appears solely because `runtime_available=false`.
2. Make the API transport unreachable: after three failures footer shows `重连中`; at 10 seconds `重试连接` appears; at 30 seconds footer shows `连接中断` and stream requests pause.
3. Restore transport and click `重试连接`: the current task reconnects, the button disappears, the footer returns to `已连接`, and draft content remains intact.
4. Switch tasks while a request is pending: no late response from the old task changes the new workspace.

- [ ] **Step 12: Record Task 2 instead of committing overlapping dirty files**

Write changed paths, red/green evidence, browser observations, and self-review findings to the SDD Task 2 report. Do not stage or commit the dirty frontend files.
