import test from 'node:test';
import assert from 'node:assert/strict';

globalThis.window = { location: { origin: 'http://writer.test' } };

function response(status, payload, headers = {}) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: (name) => headers[name.toLowerCase()] || (name.toLowerCase() === 'content-type' ? 'application/json' : '') },
    json: async () => payload,
    text: async () => typeof payload === 'string' ? payload : JSON.stringify(payload),
  };
}

test('request exposes structured error metadata from the server', async () => {
  globalThis.fetch = async () => response(409, {
    error: 'conflict',
    detail: { code: 'stale_version', message: '版本已过期', fields: { base_text: '不一致' } },
  }, { 'x-request-id': 'req-409' });
  const API = await import(`../../app/static/js/api.js?client-error=${Date.now()}`);

  await assert.rejects(API.request('/contract', { timeoutMs: 0 }), (error) => {
    assert.ok(error instanceof API.ApiError);
    assert.equal(error.status, 409);
    assert.equal(error.message, '版本已过期');
    assert.equal(error.code, 'stale_version');
    assert.deepEqual(error.fields, { base_text: '不一致' });
    assert.equal(error.requestId, 'req-409');
    assert.equal(error.retryable, false);
    return true;
  });
});

test('request retries a safe GET only when explicitly configured', async () => {
  let calls = 0;
  globalThis.fetch = async () => {
    calls += 1;
    return calls === 1 ? response(503, { detail: '暂时不可用' }) : response(200, { ok: true });
  };
  const API = await import(`../../app/static/js/api.js?client-retry=${Date.now()}`);

  const result = await API.request('/retry', { timeoutMs: 0, retries: 1, retryDelayMs: 0 });
  assert.deepEqual(result, { ok: true });
  assert.equal(calls, 2);
});

test('request adds a correlation id without overriding a caller supplied one', async () => {
  const seen = [];
  globalThis.fetch = async (_url, options) => {
    seen.push(options.headers['X-Request-ID']);
    return response(200, { ok: true });
  };
  const API = await import(`../../app/static/js/api.js?client-request-id=${Date.now()}`);

  await API.request('/request-id', { timeoutMs: 0 });
  await API.request('/request-id', { timeoutMs: 0, headers: { 'X-Request-ID': 'caller-id' } });
  assert.match(seen[0], /^writer-|^[0-9a-f-]{20,}$/);
  assert.equal(seen[1], 'caller-id');
});

test('request does not retry POST by default', async () => {
  let calls = 0;
  globalThis.fetch = async () => {
    calls += 1;
    return response(503, { detail: '写作服务暂时不可用' });
  };
  const API = await import(`../../app/static/js/api.js?client-post=${Date.now()}`);

  await assert.rejects(API.request('/write', { method: 'POST', timeoutMs: 0 }), (error) => {
    assert.equal(error.status, 503);
    assert.equal(error.retryable, true);
    return true;
  });
  assert.equal(calls, 1);
});

test('external cancellation is distinct from a client timeout', async () => {
  const abortController = new AbortController();
  globalThis.fetch = async (_url, options) => {
    await new Promise((resolve, reject) => {
      options.signal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')), { once: true });
    });
  };
  const API = await import(`../../app/static/js/api.js?client-abort=${Date.now()}`);
  const pending = API.request('/abort', { timeoutMs: 0, signal: abortController.signal });
  abortController.abort();

  await assert.rejects(pending, (error) => {
    assert.equal(error.message, '请求已取消');
    assert.equal(error.cancelled, true);
    assert.equal(error.timedOut, false);
    assert.equal(error.retryable, false);
    return true;
  });
});
