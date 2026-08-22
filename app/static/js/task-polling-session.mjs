export function createTaskPollingSession(
  taskId,
  connectionController,
  AbortControllerClass = AbortController,
) {
  const abortController = new AbortControllerClass();
  let active = true;
  return {
    taskId,
    signal: abortController.signal,
    isActive: () => active,
    runIfActive(callback) {
      if (active) return callback();
    },
    retire() {
      if (!active) return;
      active = false;
      connectionController.retire();
      abortController.abort();
    },
  };
}
