const CHANNELS = new Set(['status', 'stream']);

export function createTaskConnectionController({
  now = () => Date.now(),
  failureThreshold = 3,
  retryAfterMs = 10_000,
  offlineAfterMs = 30_000,
  initialRuntimeAvailable = true,
} = {}) {
  const failures = { status: 0, stream: 0 };
  let runtimeAvailable = Boolean(initialRuntimeAvailable);
  let retired = false;
  let outageStartedAt = null;
  let awaitingRecovery = null;

  function validateChannel(channel) {
    if (!CHANNELS.has(channel)) throw new TypeError(`Unknown connection channel: ${channel}`);
  }

  function transportFailed() {
    return failures.status >= failureThreshold || failures.stream >= failureThreshold;
  }

  function isReconnecting() {
    return !runtimeAvailable || awaitingRecovery !== null || transportFailed();
  }

  function clearOutageIfRecovered() {
    if (!awaitingRecovery && !transportFailed()) outageStartedAt = null;
  }

  return {
    snapshot() {
      const timestamp = now();
      const outageElapsed = outageStartedAt === null ? null : timestamp - outageStartedAt;
      const repeatedTransportOutage = transportFailed() && outageStartedAt !== null;
      const isOffline = repeatedTransportOutage && outageElapsed >= offlineAfterMs;
      const reconnecting = !isOffline && isReconnecting();
      const canManualRetry = repeatedTransportOutage && outageElapsed >= retryAfterMs;

      return {
        state: isOffline ? 'offline' : reconnecting ? 'reconnecting' : 'online',
        canManualRetry,
        shouldPollStream: runtimeAvailable && !isOffline,
        nextTransitionAt: isOffline || !repeatedTransportOutage
          ? null
          : timestamp < outageStartedAt + retryAfterMs
            ? outageStartedAt + retryAfterMs
            : outageStartedAt + offlineAfterMs,
        runtimeAvailable,
        retired,
      };
    },

    failureCount(channel) {
      validateChannel(channel);
      return failures[channel];
    },

    recordFailure(channel) {
      validateChannel(channel);
      if (retired || (!runtimeAvailable && channel === 'stream')) return;
      failures[channel] += 1;
      if (failures[channel] === failureThreshold && outageStartedAt === null) outageStartedAt = now();
    },

    recordSuccess(channel) {
      validateChannel(channel);
      if (retired) return;
      failures[channel] = 0;
      if (awaitingRecovery) {
        awaitingRecovery.delete(channel);
        if (awaitingRecovery.size === 0) awaitingRecovery = null;
      }
      clearOutageIfRecovered();
    },

    setRuntimeAvailable(value) {
      if (retired) return;
      runtimeAvailable = Boolean(value);
    },

    beginManualRetry(channels = ['status', 'stream']) {
      if (retired) return;
      for (const channel of channels) validateChannel(channel);
      failures.status = 0;
      failures.stream = 0;
      outageStartedAt = null;
      awaitingRecovery = new Set(channels);
    },

    retire() {
      retired = true;
    },
  };
}
