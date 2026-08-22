import test from 'node:test';
import assert from 'node:assert/strict';
import { createOperationGuard } from '../../app/static/js/operation-guard.mjs';

test('operation guard blocks duplicate starts and releases the slot', () => {
  const guard = createOperationGuard();
  const first = guard.begin('analysis');
  assert.ok(first);
  assert.equal(guard.begin('analysis'), null);
  assert.equal(guard.has('analysis'), true);
  first.done();
  assert.equal(guard.has('analysis'), false);
  assert.ok(guard.begin('analysis'));
});

test('operation guard exposes cancellation through AbortSignal', () => {
  const guard = createOperationGuard();
  const operation = guard.begin('export');
  assert.equal(operation.signal.aborted, false);
  assert.equal(guard.cancel('export'), true);
  assert.equal(operation.signal.aborted, true);
  operation.done();
  assert.equal(guard.cancel('export'), false);
});
