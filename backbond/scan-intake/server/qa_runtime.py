"""Bounded scan dispatch and durable report delivery; payloads stay in memory."""
import threading
import time
from collections import OrderedDict

# Statuses that hold one of the scanner's concurrent slots. 'starting' and
# 'dispatch_unknown' may already be sending requests to the customer's agent.
SLOT_STATUSES = ('reserved', 'starting', 'running', 'dispatch_unknown')


class QueueFull(RuntimeError):
    pass


class DispatchNotSent(RuntimeError):
    """The start request provably never reached the scanner, or the scanner refused it."""


class DispatchUnknown(RuntimeError):
    """The scanner may have accepted the start. Fail closed: keep the slot, count the scan."""


class ScanQueue:
    def __init__(self, store, start, capacity=3, max_pending=10, pending_ttl=900):
        self.store, self.start, self.capacity = store, start, capacity
        self.max_pending, self.pending_ttl = max_pending, pending_ttl
        self.lock = threading.RLock()
        # A start that was in flight when the process died may have reached the scanner.
        store.db.execute("UPDATE scans SET status='dispatch_unknown' WHERE status='starting'")
        # 'starting' is written under the lock before any start request, so a 'reserved'
        # row never reached the scanner (at most a legacy connectivity recheck ran).
        store.db.execute("UPDATE scans SET status='interrupted' WHERE status='reserved'")
        # Slots are only released by a confirmed remote result or operator reconciliation,
        # never by elapsed time: the scanner may still be sending requests.
        self.active = {row['id']: time.monotonic() for row in store.db.execute(
            f"SELECT id FROM scans WHERE status IN ({','.join('?' * len(SLOT_STATUSES))})", SLOT_STATUSES)}
        # Queued work never reached the scanner, so it is not charged against quota.
        store.db.execute("UPDATE scans SET status='interrupted' WHERE status='queued'")
        self.pending = OrderedDict()  # scan_id -> (payload, enqueued_at)
        self.awaiting_bac = OrderedDict()

    def defer_bac(self, scan_id, request_id, payload, now=None):
        """Own the original payload in memory until acknowledgment or expiry."""
        with self.lock:
            self.expire_pending(now)
            if len(self.pending) + len(self.awaiting_bac) >= self.max_pending:
                raise QueueFull()
            if scan_id in self.awaiting_bac:
                raise ValueError('reservation already retained')
            self.store.queued(scan_id)
            self.awaiting_bac[scan_id] = (request_id, payload,
                                        time.monotonic() if now is None else now)

    def recover_bac(self, bac, now=None):
        """Transfer acknowledged original reservations to dispatch exactly once."""
        with self.lock:
            self.expire_pending(now)
            for scan_id, (request_id, payload, _) in list(self.awaiting_bac.items()):
                # The reconciler owns authenticated network reads and durable backoff.
                with bac.lock:
                    row = bac.db.execute('SELECT state FROM bac_intake WHERE request_id=?',
                                         (request_id,)).fetchone()
                if not row or row[0] != 'acknowledged':
                    continue
                self.awaiting_bac.pop(scan_id)
                current = self.store.scan(scan_id)
                if not current or current['status'] != 'queued':
                    payload.clear()
                    continue
                try:
                    self.submit(scan_id, payload, now)
                except QueueFull:
                    payload.clear()
                    self.store.interrupted(scan_id)
                except (DispatchUnknown, DispatchNotSent):
                    pass  # Durable launch state forbids another scanner start.

    def submit(self, scan_id, payload, now=None):
        with self.lock:
            self.expire_pending(now)
            if len(self.active) >= self.capacity:
                if len(self.pending) + len(self.awaiting_bac) >= self.max_pending:
                    raise QueueFull()
                self.store.queued(scan_id)
                self.pending[scan_id] = (payload, time.monotonic() if now is None else now)
                return 'queued'
            self.active[scan_id] = time.monotonic()
            self.store.starting(scan_id)
        self._launch(scan_id, payload)
        return 'running'

    def _launch(self, scan_id, payload):
        try:
            try:
                response = self.start(payload)
            except DispatchNotSent:
                with self.lock:
                    self.store.launch_failed(scan_id)
                    self.complete(scan_id)
                raise
            except Exception as exc:
                self.store.dispatch_unknown(scan_id)  # slot intentionally retained
                raise DispatchUnknown() from exc
            remote_id = response.get('scan_id') if isinstance(response, dict) else None
            if not isinstance(remote_id, str) or not remote_id or len(remote_id) > 128:
                # Accepted (2xx) but uncorrelatable: it may be running.
                self.store.dispatch_unknown(scan_id)
                raise DispatchUnknown()
            # Keep the queue lock through binding to avoid a callback race.
            with self.lock:
                self.store.record_provenance(scan_id, response.get("provenance") or {})
                early = self.store.bind_scanner(scan_id, remote_id)
                self.store.running(scan_id)
                if early is not None:
                    self.store.finish(scan_id, early)
                    self.complete(scan_id)
        finally:
            payload.clear()

    def callback(self, remote_id, tests):
        with self.lock:
            row = self.store.scan_for_scanner(remote_id)
            if row is None:
                self.store.early_callback(remote_id, tests)
                return
            self.store.finish(row['id'], tests)
            self.complete(row['id'])

    def complete(self, scan_id):
        with self.lock:
            self.active.pop(scan_id, None)
            self.expire_pending()
            if self.pending and len(self.active) < self.capacity:
                next_id, (payload, _) = self.pending.popitem(last=False)
                self.active[next_id] = time.monotonic()
                # Mark before handoff so a concurrent cancel sees an uncancellable state.
                self.store.starting(next_id)
                threading.Thread(target=self._launch_queued, args=(next_id, payload), daemon=True).start()

    def _launch_queued(self, scan_id, payload):
        try:
            self._launch(scan_id, payload)
        except Exception:
            pass  # Status is persisted; credential-bearing exceptions are not logged.

    def expire_pending(self, now=None):
        """Drop never-dispatched payloads older than the TTL; not charged against quota."""
        now = time.monotonic() if now is None else now
        with self.lock:
            for scan_id in [s for s, (_, _, at) in self.awaiting_bac.items()
                            if now - at >= self.pending_ttl]:
                _, payload, _ = self.awaiting_bac.pop(scan_id)
                payload.clear()
                self.store.interrupted(scan_id)
            for scan_id in [s for s, (_, at) in self.pending.items() if now - at >= self.pending_ttl]:
                payload, _ = self.pending.pop(scan_id)
                payload.clear()
                self.store.interrupted(scan_id)

    def reconcile_unknown(self, scan_id, *, remote_id=None, confirmed_not_started=False):
        """Operator-only. Requires an out-of-band answer from the scanner operator; the
        scanner API offers no lookup or idempotency key, so nothing here infers one."""
        with self.lock:
            row = self.store.scan(scan_id)
            if not row or row['status'] != 'dispatch_unknown':
                return False
            if remote_id:
                early = self.store.bind_scanner(scan_id, remote_id)
                self.store.running(scan_id)
                if early is not None:
                    self.store.finish(scan_id, early)
                    self.complete(scan_id)
            elif confirmed_not_started:
                self.store.launch_failed(scan_id)
                self.complete(scan_id)
            else:
                return False
            return True

    def cancel(self, scan_id):
        """Cancel only work that has not been sent to the scanner."""
        with self.lock:
            waiting = self.awaiting_bac.pop(scan_id, None)
            if waiting is not None:
                waiting[1].clear()
                return self.store.cancel_unsent(scan_id)
            item = self.pending.pop(scan_id, None)
            if item is None:
                return False
            item[0].clear()
            return self.store.cancel_unsent(scan_id)

    def close(self):
        with self.lock:
            for scan_id, (payload, _) in self.pending.items():
                payload.clear()
                self.store.interrupted(scan_id)
            self.pending.clear()
            for scan_id, (_, payload, _) in self.awaiting_bac.items():
                payload.clear()
                self.store.interrupted(scan_id)
            self.awaiting_bac.clear()


def deliver_pending(store, send, now=None):
    """BEAAA-42596: customer report mail is disabled; the Operations workflow sends the report."""
    raise RuntimeError('customer report mail is disabled (BEAAA-42596)')
