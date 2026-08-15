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
