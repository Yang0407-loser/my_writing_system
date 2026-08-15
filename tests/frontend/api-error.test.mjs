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
        assert.ok(error instanceof API.ApiError);
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
    error => {
      assert.ok(error instanceof API.ApiError);
      assert.equal(error.message, '请求超时或已取消');
      assert.equal(error.retryable, true);
      assert.equal(error.status, 0);
      assert.equal(error.detail, null);
      assert.equal(error.url, '/contract');
      return true;
    },
  );
});
