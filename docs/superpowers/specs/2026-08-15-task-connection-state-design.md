# Task Connection State Design

**Date:** 2026-08-15

**Scope:** Phase 0 / P0-5 of the frontend API and interaction upgrade

**Status:** Approved direction; awaiting written-spec review

## Goal

Keep an active or restored writing session understandable and recoverable when the browser cannot reach the API or when Redis runtime data is temporarily unavailable. The durable project must remain editable whenever SQLite fallback is available.

## User-visible behavior

The footer connection indicator has three states:

- `online` — status and stream requests are healthy.
- `reconnecting` — either request channel has failed three consecutive times, or `/status` reports `runtime_available=false` while durable data remains available.
- `offline` — browser/API transport failure has lasted at least 30 seconds.

After 10 seconds of continuous transport failure, a compact **重试连接** button appears beside the connection indicator. Clicking it immediately starts a fresh status and stream attempt for the current task.

Redis runtime degradation is not the same as browser/API disconnection. When `/status` returns HTTP 200 with `runtime_available=false`, the page remains readable and saveable from durable storage, shows `reconnecting`, and does not enter transport-offline protection solely because Redis is unavailable.

## Architecture

Add a small ES module dedicated to connection health. It owns no Vue state and performs no network requests. Given an injectable clock, it records status/stream successes and failures and returns a snapshot:

```text
state: online | reconnecting | offline
canManualRetry: boolean
transportOutageStartedAt: number | null
runtimeAvailable: boolean
```

`main.js` owns one controller per active polling session. A session is bound to the captured `pollingTaskId`; task transitions, stop actions, component unmount, and explicit retries retire the old session before creating another one.

The existing API client remains the network boundary. Status and stream calls accept an `AbortSignal`; retiring a session aborts both in-flight requests so a delayed response from an old task cannot overwrite the current task.

## State transitions

1. A successful transport response resets that channel's consecutive failure count.
2. Three consecutive failures on either channel enter `reconnecting` and start the transport-outage clock.
3. Ten seconds after the outage clock starts, `canManualRetry` becomes true.
4. Thirty seconds after the outage clock starts, state becomes `offline`.
5. While offline, high-frequency stream polling pauses. Status uses the existing capped backoff as a low-frequency recovery probe.
6. A successful status and stream response restores `online`, clears the outage clock, hides manual retry, and emits the existing one-time recovery toast.
7. Explicit retry retires the current polling session and starts a fresh session immediately without clearing draft or workspace content.
8. An HTTP 200 status with `runtime_available=false` records runtime degradation and displays `reconnecting`, but does not advance the transport-outage clock. While that flag is active, stream failures are treated as part of the known Redis degradation and high-frequency stream polling pauses; they do not independently promote the browser to `offline`.

## Refresh and recovery

`initTaskSession()` must not silently stop when its first status request fails. If a task/workspace ID is available, it starts the controlled polling recovery path and exposes the same footer state and retry action used during an active run.

Durable project loading and autosave remain independent from live stream health. The feature does not make SQLite a replacement for Redis streams or Celery task control.

## UI design

The status bar keeps its current compact visual language. The new action is a small outline button placed immediately after the connection pill:

```text
[已保存] [重连中] [重试连接]  ───────── progress ─────────
```

No modal, toast loop, countdown, or new color token is introduced. Existing gold reconnecting and red offline states remain the primary signal. The button has a visible keyboard focus state and uses the same wording before and after invocation.

## Error handling

- Manual retry is disabled while its replacement session is being created.
- Abort errors caused by retiring a session do not increment failure counts or show an error toast.
- API authentication, rate-limit, and server failures use the same transport state machine, while their existing parsed error text remains available for the first actionable toast.
- A stale session may finish internally, but its session token prevents it from mutating Vue state.

## Testing

Pure Node tests use a fake clock and real controller behavior to cover:

- third consecutive failure enters reconnecting;
- 10 seconds exposes manual retry;
- 30 seconds enters offline and requests stream pause;
- explicit retry and subsequent success restore online state;
- `runtime_available=false` remains durable-runtime degradation, not transport offline;
- a retired task session cannot update the active session.

Python/frontend contract tests cover the status-bar action and API signal plumbing. Browser verification covers initial-load failure recovery, Redis-offline durable editing, manual retry, and recovery to online.

## Non-goals

- Replacing polling with WebSocket or Server-Sent Events.
- Building the full task-session store planned for later phases.
- Changing Redis, Celery, or backend task-state semantics.
- Refactoring unrelated silent catches or all long-running actions in this slice.
