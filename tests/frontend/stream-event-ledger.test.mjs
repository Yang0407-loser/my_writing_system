import test from 'node:test';
import assert from 'node:assert/strict';
import { createStreamEventLedger } from '../../app/static/js/stream-event-ledger.mjs';

test('stream event ledger rejects duplicate Redis message IDs', () => {
  const ledger = createStreamEventLedger();
  assert.equal(ledger.accept('1-0', { event: 'token' }), true);
  assert.equal(ledger.accept('1-0', { event: 'token' }), false);
  assert.equal(ledger.size(), 1);
});

test('stream event ledger closes after terminal events and ignores late events', () => {
  const ledger = createStreamEventLedger();
  assert.equal(ledger.accept('2-0', { event: 'done' }), true);
  assert.equal(ledger.isClosed(), true);
  assert.equal(ledger.accept('3-0', { event: 'token', text: 'late' }), false);
});

test('stream event ledger bounds retained IDs', () => {
  const ledger = createStreamEventLedger({ maxEntries: 2 });
  ledger.accept('1-0', { event: 'token' });
  ledger.accept('2-0', { event: 'token' });
  ledger.accept('3-0', { event: 'token' });
  assert.equal(ledger.size(), 2);
  assert.equal(ledger.accept('1-0', { event: 'token' }), true);
});
