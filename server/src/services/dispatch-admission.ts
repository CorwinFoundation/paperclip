/** Process-local admission barrier. Close before observing run counts.
 * Callers must hold a lease across every asynchronous claim/dispatch operation.
 * A successful drain still requires authoritative active-run verification.
 */
export function createDispatchAdmission() {
  let closed = false;
  let active = 0;
  const waiters = new Set<() => void>();
  return {
    acquire() {
      if (closed) return null;
      active += 1;
      let released = false;
      return () => {
        if (released) return;
        released = true;
        active -= 1;
        if (active === 0) for (const notify of [...waiters]) notify();
      };
    },
    async drain(timeoutMs: number) {
      if (!Number.isSafeInteger(timeoutMs) || timeoutMs < 0) {
        throw new RangeError('Drain timeout must be a nonnegative safe integer');
      }
      closed = true;
      if (active === 0) return true;
      return new Promise<boolean>((resolve) => {
        const finish = (drained: boolean) => {
          clearTimeout(timer);
          waiters.delete(onIdle);
          resolve(drained);
        };
        const onIdle = () => finish(true);
        const timer = setTimeout(() => finish(false), timeoutMs);
        waiters.add(onIdle);
      });
    },
    resume() { closed = false; },
    get closed() { return closed; },
    get active() { return active; },
  };
}

// One barrier for every heartbeatService in this server process.
// Not a cross-process or first-install preservation guarantee.
export const dispatchAdmission = createDispatchAdmission();

export async function withDispatchAdmission<T>(
  admission: ReturnType<typeof createDispatchAdmission>,
  claim: () => Promise<T>,
): Promise<T | null> {
  const release = admission.acquire();
  if (!release) return null;
  try {
    return await claim();
  } finally {
    release();
  }
}

/** Operator controls never cancel runs or alter durable queues/session state. */
export function createDispatchControl(
  admission: ReturnType<typeof createDispatchAdmission>,
  countRunning: () => Promise<number>,
) {
  let revision = 0;
  async function status() {
    // Capture revision before the query so concurrent resume cannot certify idle.
    const observedRevision = revision;
    const running = await countRunning();
    if (!Number.isSafeInteger(running) || running < 0) {
      throw new Error("Authoritative active-run count unavailable");
    }
    const closed = admission.closed;
    const claims = admission.active;
    return {
      closed, claims, running,
      restartReady: observedRevision === revision && closed && claims === 0 && running === 0,
    };
  }
  return {
    status,
    async drain(timeoutMs: number) {
      const observedRevision = revision;
      const settled = await admission.drain(timeoutMs);
      const current = await status();
      return { ...current, restartReady: settled && observedRevision === revision && current.restartReady };
    },
    resume() {
      revision += 1;
      admission.resume();
      return { closed: false, restartReady: false };
    },
  };
}
