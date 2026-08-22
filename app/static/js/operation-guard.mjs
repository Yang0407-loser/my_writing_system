export function createOperationGuard() {
  const active = new Map();

  function begin(key) {
    if (active.has(key)) return null;
    const controller = new AbortController();
    const handle = {
      key,
      signal: controller.signal,
      cancel() { controller.abort(); },
      done() {
        if (active.get(key) === handle) active.delete(key);
      },
    };
    active.set(key, handle);
    return handle;
  }

  return {
    begin,
    cancel(key) {
      const handle = active.get(key);
      if (!handle) return false;
      handle.cancel();
      return true;
    },
    has(key) { return active.has(key); },
    keys() { return [...active.keys()]; },
  };
}
