"""Durable, at-least-once signed-webhook delivery outbox — BEAAA-14400.

Parent ``BEAAA-14394`` (P1#5 — restore CI/CD scan lifecycle after the async
full-scan cutover). This module is *fix 2 of 4*: deliver the signed completion
webhook **on the terminal transition** (after the score envelope is persisted),
not at queue time, and do it through a durable outbox with retry, a stable
idempotency key, and at-least-once de-duplication.

Classification (per ``contracts/scanner-implementation-supervision-v1.md@v1.0``)
-------------------------------------------------------------------------------
**Rule-execution** — pure delivery plumbing on top of the already-ratified
HMAC-SHA256 webhook contract (ADR-0042 §2). No rubric point, no scoring math,
no judge, no probe family, no ``contracts/are-scanner-api-v1.md`` output-shape
change. The signed body is the same customer score envelope
``GET /scan/{scan_id}`` already returns; this module only changes *when* and
*how reliably* it is delivered. Wrap, don't fork: the HMAC signing +
fail-closed secret resolution is reused verbatim from
:func:`api.ci_scan_ext.build_signed_delivery`.

Design invariants
-----------------
* **Fail-closed.** If the ``secret_ref`` cannot be resolved, no unsigned body
  is ever posted (``build_signed_delivery`` raises
  :class:`~api.ci_scan_ext.WebhookSecretResolutionError`); the record stays
  retryable so an operator can provision the secret and a later
  :meth:`WebhookOutbox.deliver_pending` drains it.
* **Delivery never falsifies scan status.** Every delivery attempt is wrapped
  and swallows all exceptions. Callers enqueue *after* the scan result is
  persisted; a delivery failure can never change what the scan produced.
* **At-least-once + dedupe.** The idempotency key is the ``scan_id`` (1:1 with a
  terminal scan). The ``X-BackBond-Delivery`` header is *derived from* that key
  (:func:`stable_delivery_id`), so every retry of the same terminal event
  carries the *same* delivery id and the vendor's CI de-duplicates it. A record
  that has already reached ``delivered`` is never re-sent.
* **Restart-safe by construction (composes with fix 4, BEAAA-14401).** The
  persistence surface is the injectable :class:`OutboxStore`. The default
  :class:`InMemoryOutboxStore` is process-local; fix 4 swaps in a durable store
  and calls :meth:`WebhookOutbox.deliver_pending` on boot to replay records
  that were pending when the process died — without duplicating any already
  ``delivered`` record.
"""
from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Protocol

from api.ci_scan_ext import WebhookSecretResolutionError, build_signed_delivery

# ---------------------------------------------------------------------------
# Record + status model.
# ---------------------------------------------------------------------------

#: Delivery is queued and has not yet succeeded — eligible for retry.
STATUS_PENDING: str = "pending"
#: Delivery succeeded exactly once — terminal, never re-sent.
STATUS_DELIVERED: str = "delivered"
#: Retries exhausted without success — terminal, requires operator attention.
STATUS_DEAD: str = "dead"

#: Default retry ceiling before a record is parked ``dead``. At-least-once
#: means the vendor de-dupes on ``X-BackBond-Delivery``; the ceiling only
#: bounds wasted attempts, it does not risk a missed-or-duplicated delivery.
DEFAULT_MAX_ATTEMPTS: int = 5

#: Stable UUIDv5 namespace so ``stable_delivery_id`` is deterministic across
#: retries *and* across process restarts (fix 4 recovery replays the same id).
_DELIVERY_NAMESPACE: uuid.UUID = uuid.UUID("6f2b8a3e-4c1d-5e7a-9b0c-14400ce1d0a2")


def stable_delivery_id(idempotency_key: str) -> str:
    """Return the deterministic ``X-BackBond-Delivery`` id for a key.

    UUIDv5 of the idempotency key, so every retry (and every post-restart
    recovery replay) of the *same* terminal scan carries the *same* delivery
    id. That is what lets the vendor's CI de-duplicate an at-least-once
    delivery down to exactly-once processing.
    """
    return str(uuid.uuid5(_DELIVERY_NAMESPACE, idempotency_key))


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class OutboxRecord:
    """One terminal-webhook delivery, keyed by ``idempotency_key`` (scan_id)."""

    idempotency_key: str
    url: str
    secret_ref: str
    payload: dict[str, Any]
    delivery_id: str
    status: str = STATUS_PENDING
    attempts: int = 0
    last_error: str | None = None
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)

    @property
    def is_terminal(self) -> bool:
        return self.status in (STATUS_DELIVERED, STATUS_DEAD)


# ---------------------------------------------------------------------------
# Persistence surface — in-memory default; fix 4 (BEAAA-14401) injects durable.
# ---------------------------------------------------------------------------


class OutboxStore(Protocol):
    """Minimal persistence surface the outbox drives.

    A durable implementation (fix 4) need only make :meth:`put` write-through
    and :meth:`pending` survive a restart; the delivery semantics live entirely
    in :class:`WebhookOutbox`.
    """

    def get(self, idempotency_key: str) -> OutboxRecord | None: ...

    def put(self, record: OutboxRecord) -> None: ...

    def pending(self) -> list[OutboxRecord]: ...


class InMemoryOutboxStore:
    """Process-local, thread-safe :class:`OutboxStore` (the default).

    Not restart-safe on its own — that is exactly what fix 4 (BEAAA-14401)
    replaces. Kept deliberately tiny so the durable store is a drop-in.
    """

    def __init__(self) -> None:
        self._records: dict[str, OutboxRecord] = {}
        self._lock = threading.RLock()

    def get(self, idempotency_key: str) -> OutboxRecord | None:
        with self._lock:
            return self._records.get(idempotency_key)

    def put(self, record: OutboxRecord) -> None:
        with self._lock:
            self._records[record.idempotency_key] = record

    def pending(self) -> list[OutboxRecord]:
        with self._lock:
            return [r for r in self._records.values() if r.status == STATUS_PENDING]


# ---------------------------------------------------------------------------
# The outbox.
# ---------------------------------------------------------------------------

#: A transport that POSTs the signed body. MUST raise on a non-2xx / network
#: error so the outbox retries; a clean return means the vendor accepted it.
HttpPoster = Callable[[str, Mapping[str, str], bytes], None]
#: Resolves a ``secret_ref`` to signing material, fail-closed (``None`` ⇒ hold).
SecretResolver = Callable[[str], "str | bytes | None"]


class WebhookOutbox:
    """Deliver signed terminal webhooks with retry, idempotency, and dedupe.

    Thread-safe: a per-record lock serialises concurrent attempts on the same
    idempotency key so a retry tick and an enqueue cannot double-post.
    """

    def __init__(
        self,
        *,
        http_poster: HttpPoster,
        secret_resolver: SecretResolver,
        store: OutboxStore | None = None,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        delivery_id_factory: Callable[[str], str] = stable_delivery_id,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        self._http_poster = http_poster
        self._secret_resolver = secret_resolver
        self._store: OutboxStore = store or InMemoryOutboxStore()
        self._max_attempts = int(max_attempts)
        self._delivery_id_factory = delivery_id_factory
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    # -- public API ---------------------------------------------------------

    def enqueue(
        self,
        *,
        idempotency_key: str,
        url: str,
        secret_ref: str,
        payload: Mapping[str, Any],
    ) -> OutboxRecord:
        """Record a terminal delivery and attempt it once (best-effort).

        Idempotent on ``idempotency_key``: a key that already ``delivered`` is
        returned untouched (never re-sent); a ``dead`` key is likewise not
        revived here (operator/recovery concern). A still-``pending`` key is
        retried rather than duplicated. Never raises — a delivery failure must
        not propagate into the scan-accept path.
        """
        with self._key_lock(idempotency_key):
            existing = self._store.get(idempotency_key)
            if existing is not None and existing.is_terminal:
                return existing
            record = existing or OutboxRecord(
                idempotency_key=idempotency_key,
                url=url,
                secret_ref=secret_ref,
                payload=dict(payload),
                delivery_id=self._delivery_id_factory(idempotency_key),
            )
            if existing is None:
                self._store.put(record)
            self._attempt_locked(record)
            return record

    def deliver_pending(self) -> list[OutboxRecord]:
        """Retry every ``pending`` record — the retry tick + restart-recovery
        drain. Fix 4 (BEAAA-14401) calls this on boot after loading a durable
        store; ``delivered`` records are skipped, so recovery never duplicates.
        """
        attempted: list[OutboxRecord] = []
        for record in self._store.pending():
            with self._key_lock(record.idempotency_key):
                # Re-read under the lock in case another thread just delivered.
                fresh = self._store.get(record.idempotency_key) or record
                if fresh.status != STATUS_PENDING:
                    continue
                self._attempt_locked(fresh)
                attempted.append(fresh)
        return attempted

    def get(self, idempotency_key: str) -> OutboxRecord | None:
        return self._store.get(idempotency_key)

    # -- internals ----------------------------------------------------------

    def _key_lock(self, idempotency_key: str) -> threading.Lock:
        with self._locks_guard:
            lock = self._locks.get(idempotency_key)
            if lock is None:
                lock = threading.Lock()
                self._locks[idempotency_key] = lock
            return lock

    def _attempt_locked(self, record: OutboxRecord) -> None:
        """Attempt one signed delivery. Caller holds the per-key lock."""
        if record.status == STATUS_DELIVERED:
            return
        try:
            headers, body_bytes = build_signed_delivery(
                record.payload,
                record.secret_ref,
                self._secret_resolver,
                delivery_id_factory=lambda: record.delivery_id,
            )
            self._http_poster(record.url, headers, body_bytes)
        except WebhookSecretResolutionError as exc:
            # Fail-closed: no unsigned body was posted. Keep the record
            # retryable so a provisioned secret drains it on the next tick.
            self._fail(record, f"secret_resolution: {exc}")
        except Exception as exc:  # noqa: BLE001 — transport errors must not escape
            self._fail(record, f"transport: {type(exc).__name__}: {exc}")
        else:
            record.status = STATUS_DELIVERED
            record.last_error = None
            record.updated_at = _utcnow()
            self._store.put(record)

    def _fail(self, record: OutboxRecord, error: str) -> None:
        record.attempts += 1
        record.last_error = error
        record.status = STATUS_DEAD if record.attempts >= self._max_attempts else STATUS_PENDING
        record.updated_at = _utcnow()
        self._store.put(record)


# ---------------------------------------------------------------------------
# Production adapters — lazy httpx poster + env-backed fail-closed resolver.
# ---------------------------------------------------------------------------

#: JSON object mapping ``secret_ref`` → provisioned webhook secret. The block on
#: the request carries only a ``secret_ref`` (ADR-0042 §2 — never the secret);
#: production resolves it out of band from this store.
WEBHOOK_SECRETS_JSON_ENV: str = "ARE_WEBHOOK_SECRETS_JSON"
#: Per-delivery HTTP timeout for the default httpx poster.
WEBHOOK_HTTP_TIMEOUT_SECONDS: float = 10.0


def httpx_http_poster(
    url: str,
    headers: Mapping[str, str],
    body_bytes: bytes,
    *,
    timeout: float = WEBHOOK_HTTP_TIMEOUT_SECONDS,
) -> None:
    """Default production transport — POST the signed body via httpx.

    Raises on any non-2xx (``raise_for_status``) or transport error so the
    outbox marks the attempt failed and retries. httpx is imported lazily so
    the API-edge import surface stays tight for callers that never deliver.
    """
    import httpx

    response = httpx.post(url, content=body_bytes, headers=dict(headers), timeout=timeout)
    response.raise_for_status()


def env_webhook_secret_resolver(
    env: Mapping[str, str] | None = None,
) -> SecretResolver:
    """Build a fail-closed resolver over ``ARE_WEBHOOK_SECRETS_JSON``.

    Returns ``None`` for any ref that is absent or unparseable — the outbox
    then holds the delivery (never sends unsigned) until the secret store is
    fixed. Reads the env on every call so a rotated secret is picked up without
    a process restart.
    """
    import json
    import os

    source: Mapping[str, str] = os.environ if env is None else env

    def resolver(secret_ref: str) -> str | bytes | None:
        raw = source.get(WEBHOOK_SECRETS_JSON_ENV)
        if not raw:
            return None
        try:
            secrets = json.loads(raw)
        except (ValueError, TypeError):
            return None
        if not isinstance(secrets, dict):
            return None
        value = secrets.get(secret_ref)
        return value if isinstance(value, (str, bytes)) else None

    return resolver


def make_default_webhook_outbox(
    *,
    env: Mapping[str, str] | None = None,
    store: OutboxStore | None = None,
) -> WebhookOutbox:
    """Production outbox: env-backed fail-closed resolver + httpx poster.

    The in-memory store is the default; fix 4 (BEAAA-14401) passes a durable
    ``store`` here and drives :meth:`WebhookOutbox.deliver_pending` on boot.
    """
    return WebhookOutbox(
        http_poster=httpx_http_poster,
        secret_resolver=env_webhook_secret_resolver(env),
        store=store,
    )


__all__ = [
    "DEFAULT_MAX_ATTEMPTS",
    "InMemoryOutboxStore",
    "OutboxRecord",
    "OutboxStore",
    "STATUS_DEAD",
    "STATUS_DELIVERED",
    "STATUS_PENDING",
    "WEBHOOK_SECRETS_JSON_ENV",
    "WebhookOutbox",
    "env_webhook_secret_resolver",
    "httpx_http_poster",
    "make_default_webhook_outbox",
    "stable_delivery_id",
]
