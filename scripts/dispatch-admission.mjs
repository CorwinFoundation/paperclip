/** Process-local admission barrier. Close before observing run counts.
 * Callers must hold a lease across every asynchronous claim/dispatch operation.
 * A successful drain still requires authoritative active-run verification.
 */
export function createDispatchAdmission() {
  let closed = false;
  let active = 0;
  const waiters = new Set();
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
    async drain(timeoutMs) {
      if (!Number.isSafeInteger(timeoutMs) || timeoutMs < 0) {
        throw new RangeError('Drain timeout must be a nonnegative safe integer');
      }
      closed = true;
      if (active === 0) return true;
      return new Promise((resolve) => {
        const finish = (drained) => {
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
