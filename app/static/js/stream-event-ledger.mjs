const TERMINAL_EVENTS = new Set(['done', 'failed', 'error', 'cancelled', 'stopped']);

export function createStreamEventLedger({ maxEntries = 512 } = {}) {
  const seen = new Set();
  let closed = false;
  const eventName = (event) => typeof event === 'string' ? event : event?.event;
  return {
    accept(messageId, event) {
      if (closed) return false;
      const key = String(messageId || '');
      if (key && seen.has(key)) return false;
      if (key) {
        seen.add(key);
        while (seen.size > maxEntries) seen.delete(seen.values().next().value);
      }
      if (TERMINAL_EVENTS.has(eventName(event))) closed = true;
      return true;
    },
    close() { closed = true; },
    isClosed() { return closed; },
    size() { return seen.size; },
  };
}
