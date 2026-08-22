import test from 'node:test';
import assert from 'node:assert/strict';
import { createTaskConnectionController } from '../../app/static/js/task-connection.mjs';
import { createTaskPollingSession } from '../../app/static/js/task-polling-session.mjs';
import {
  buildRestoredDraftBlocks,
  createTaskRestorationStreamGate,
  runDurableHydration,
} from '../../app/static/js/task-restoration-stream.mjs';

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

test('offline transport outage remains manually recoverable until both channels succeed', () => {
  let now = 0;
  const controller = createTaskConnectionController({ now: () => now });

  for (let failure = 0; failure < 3; failure += 1) {
    controller.recordFailure('status');
    controller.recordFailure('stream');
  }

  now = 30_000;
  assert.equal(controller.snapshot().state, 'offline');
  assert.equal(controller.snapshot().canManualRetry, true);

  controller.beginManualRetry();
  assert.equal(controller.snapshot().state, 'reconnecting');
  controller.recordSuccess('status');
  assert.equal(controller.snapshot().state, 'reconnecting');
  controller.recordSuccess('stream');
  assert.equal(controller.snapshot().state, 'online');
});

test('transport failures recover even when the last runtime status was unavailable', () => {
  let now = 0;
  const controller = createTaskConnectionController({ now: () => now });

  controller.setRuntimeAvailable(false);
  controller.recordFailure('status');
  controller.recordFailure('status');
  controller.recordFailure('status');

  now = 10_000;
  assert.equal(controller.snapshot().canManualRetry, true);

  now = 30_000;
  assert.equal(controller.snapshot().state, 'offline');
  assert.equal(controller.snapshot().shouldPollStream, false);
});

test('manual retry preserves initial runtime degradation until status proves recovery', () => {
  const controller = createTaskConnectionController({ initialRuntimeAvailable: false });

  assert.equal(controller.snapshot().state, 'reconnecting');
  assert.equal(controller.snapshot().shouldPollStream, false);

  controller.beginManualRetry();
  assert.equal(controller.snapshot().state, 'reconnecting');
  assert.equal(controller.snapshot().shouldPollStream, false);

  controller.setRuntimeAvailable(true);
  assert.equal(controller.snapshot().shouldPollStream, true);
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

test('manual retry starts a fresh retry and offline timeline after three new failures', () => {
  let now = 0;
  const controller = createTaskConnectionController({ now: () => now });

  controller.beginManualRetry();
  controller.recordFailure('status');
  controller.recordFailure('status');
  controller.recordFailure('status');

  assert.equal(controller.snapshot().state, 'reconnecting');
  assert.equal(controller.snapshot().canManualRetry, false);

  now = 10_000;
  assert.equal(controller.snapshot().canManualRetry, true);
  assert.equal(controller.snapshot().nextTransitionAt, 30_000);

  now = 30_000;
  assert.equal(controller.snapshot().state, 'offline');
  assert.equal(controller.snapshot().canManualRetry, true);
  assert.equal(controller.snapshot().shouldPollStream, false);
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

test('durable restore defers stream replay until hydration and then starts at zero', () => {
  const gate = createTaskRestorationStreamGate({ deferred: true });

  assert.equal(gate.isReady(), false);
  assert.equal(gate.cursor(), '0-0');
  gate.recordCursor('stale-while-deferred');
  assert.equal(gate.cursor(), '0-0');

  assert.equal(gate.releaseAfterHydration(), true);
  assert.equal(gate.isReady(), true);
  assert.equal(gate.cursor(), '0-0');

  gate.recordCursor('4-2');
  assert.equal(gate.cursor(), '4-2');
  assert.equal(gate.releaseAfterHydration(), false);
  assert.equal(gate.cursor(), '4-2');
});

test('local snapshot restore allows stream replay immediately', () => {
  const gate = createTaskRestorationStreamGate();

  assert.equal(gate.isReady(), true);
  assert.equal(gate.cursor(), '0-0');
});

test('empty valid durable draft builds matching empty blocks before stream release', () => {
  const flatOutline = [{
    section: 1,
    title: '第一节',
    subsections: [{ subsection: 1, title: '开场', target_words: 1200 }],
  }];

  const result = buildRestoredDraftBlocks({
    flatOutline,
    draft: '',
    countWords: text => text.length,
    defaultTargetWords: 2000,
  });

  assert.deepEqual(result, {
    ready: true,
    blocks: [
      { type: 'section', title: '第1节：第一节', text: '', wordCount: 0, targetWords: 0, section: 1 },
      { type: 'subsection', title: '开场', text: '', wordCount: 0, targetWords: 1200, section: 1, subsection: 1 },
    ],
  });
});

test('unparseable or mismatched durable draft is not ready for stream replay', () => {
  const flatOutline = [{
    section: 1,
    title: '第一节',
    subsections: [{ subsection: 1, title: '开场', target_words: 1200 }],
  }];

  assert.equal(buildRestoredDraftBlocks({ flatOutline, draft: '{' }).ready, false);
  assert.equal(buildRestoredDraftBlocks({
    flatOutline,
    draft: JSON.stringify([{ section: 9, subsection: 9, text: 'orphan' }]),
  }).ready, false);
});

test('durable hydration retries until usable blocks exist', async () => {
  let loads = 0;
  const waits = [];
  const applied = [];

  const ready = await runDurableHydration({
    isCurrent: () => true,
    load: async () => {
      loads += 1;
      return loads === 1 ? { ready: false, blocks: [] } : { ready: true, blocks: ['usable'] };
    },
    apply: blocks => applied.push(blocks),
    wait: async delay => { waits.push(delay); },
    initialRetryMs: 100,
    maxRetryMs: 500,
  });

  assert.equal(ready, true);
  assert.equal(loads, 2);
  assert.deepEqual(waits, [100]);
  assert.deepEqual(applied, [['usable']]);
});

test('stale durable hydration cancels after a failed attempt', async () => {
  let current = true;
  let loads = 0;
  let applied = false;

  const ready = await runDurableHydration({
    isCurrent: () => current,
    load: async () => { loads += 1; throw new Error('draft unavailable'); },
    apply: () => { applied = true; },
    wait: async () => { current = false; },
  });

  assert.equal(ready, false);
  assert.equal(loads, 1);
  assert.equal(applied, false);
});
