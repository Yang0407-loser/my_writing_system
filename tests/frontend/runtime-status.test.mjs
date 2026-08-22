import test from 'node:test';
import assert from 'node:assert/strict';

import { connectionStateFromStatus } from '../../app/static/js/runtime-status.mjs';


test('durable fallback status keeps the runtime in reconnecting state', () => {
  assert.equal(
    connectionStateFromStatus({ runtime_available: false }),
    'reconnecting',
  );
});


test('live runtime status reports an online connection', () => {
  assert.equal(connectionStateFromStatus({ runtime_available: true }), 'online');
  assert.equal(connectionStateFromStatus({}), 'online');
});
