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
