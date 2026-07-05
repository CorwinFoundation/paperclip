"""App-gateway middleware — D4 key store + D3 per-tenant rate limit.

BEAAA-11867 (Child B of BEAAA-11862; D3+D4 of the CEO infra decision
BEAAA-11861). STAGING / non-public only. This module sits in front of the
scan-creating route handlers in :mod:`api.app` and provides two co-located
capabilities that both need the *authenticated* key/tenant identity:

**D4 — key store (replaces the flat ``ARE_API_AUTH_TOKENS_JSON`` dict).**
Per-key records ``{key_id, tenant_id, status, expires_at, created_at,
rotated_from?, rate_limit?}`` live in a mutable JSON-file backing store,
consulted per request through a short-TTL (≤5s) in-process cache so that
revocation and expiry propagate in *seconds* without a process restart. A
``revoked`` status **or** ``now > expires_at`` resolves to *no identity*
→ ``401`` at the edge. Every key MUST carry an ``expires_at`` — there are no
infinite-lived keys. Rotation issues a new ``key_id`` with a grace-overlap
(both valid until the old key's ``expires_at``) and then revokes the old one.
The admin CLI (``python -m api.edge_gateway``) mutates the store on disk; the
running app picks the change up within the cache TTL — no restart.

**D3 — per-tenant rate limit.** A token-bucket keyed on the *resolved*
tenant identity (post-auth), NOT source IP, plus a max-concurrent gauge.
Scoped to the scan-creating routes. Phase-1 defaults are ``N`` scans/min and
``M`` max-concurrent per tenant, each per-tenant overridable via the key
record's ``rate_limit``. Over the limit → ``429`` + ``Retry-After``.

Phase-1 is an **in-process, single-node** limiter. A multi-node shared-store
limiter is a deliberate deferral (documented limitation, not a Phase-1 gap):
in a horizontally-scaled deployment each node keeps its own buckets, so the
effective ceiling is ``per-node-limit × node-count``. The staging front is
single-node, so this is exact there.

No ``are-scanner-api-v1`` contract change, no rubric/scoring change. See
``docs/adr/adr-0047-beaaa11867-app-gateway-key-store-rate-limit.md``.
"""
from __future__ import annotations

import json
import math
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

# ---------------------------------------------------------------------------
# Env vars + Phase-1 defaults
# ---------------------------------------------------------------------------

#: Feature flag. Unset/anything-but-"on" ⇒ the app keeps the legacy flat-dict
#: auth path and applies no rate limit (byte-identical to pre-BEAAA-11867).
#: ``on`` ⇒ the app resolves identity through the key store and rate-limits
#: scan-creating routes. Rollback is config-only (unset the flag).
EDGE_GATEWAY_ENABLED_ENV: str = "ARE_EDGE_GATEWAY"

#: Path to the JSON key-store file. When the gateway is enabled and this is
#: set, the store is loaded from (and admin mutations are written to) this
#: file. When unset, the store bootstraps read-only from the legacy
#: ``ARE_API_AUTH_TOKENS_JSON`` env dict (local/test bootstrap only).
KEY_STORE_PATH_ENV: str = "ARE_KEY_STORE_PATH"

#: Legacy flat ``{token: tenant_id}`` dict — retained ONLY as a local/test
#: bootstrap source for the store (and as the fallback path when the gateway
#: is disabled). Bootstrapped keys are assigned a finite expiry (below); the
#: store never holds an infinite-lived key.
API_AUTH_TOKENS_ENV: str = "ARE_API_AUTH_TOKENS_JSON"

#: TTL (seconds) applied to keys bootstrapped from the env dict, so the
#: "no infinite-lived keys" invariant holds even for bootstrap material.
BOOTSTRAP_TTL_ENV: str = "ARE_KEY_STORE_BOOTSTRAP_TTL_SECONDS"
DEFAULT_BOOTSTRAP_TTL_SECONDS: float = 24 * 60 * 60.0

#: Short-TTL cache window for store reloads. Capped at 5s per the design so
#: revoke/expire propagate in seconds without a restart.
CACHE_TTL_ENV: str = "ARE_KEY_STORE_CACHE_TTL_SECONDS"
DEFAULT_CACHE_TTL_SECONDS: float = 5.0
MAX_CACHE_TTL_SECONDS: float = 5.0

#: Phase-1 rate-limit defaults (per tenant), overridable per env and per key.
SCANS_PER_MIN_ENV: str = "ARE_RATE_LIMIT_SCANS_PER_MIN"
MAX_CONCURRENT_ENV: str = "ARE_RATE_LIMIT_MAX_CONCURRENT"
DEFAULT_SCANS_PER_MIN: int = 10
DEFAULT_MAX_CONCURRENT: int = 3

#: Retry-After (seconds) advertised when the concurrency gauge (not the rate
#: bucket) is the limiter — there is no token-refill clock to derive it from.
CONCURRENCY_RETRY_AFTER_SECONDS: int = 1


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class AuthError(Exception):
    """Identity could not be resolved (unknown / revoked / expired key)."""

    def __init__(self, code: str = "authorization_invalid") -> None:
        super().__init__(code)
        self.code = code


class RateLimited(Exception):
    """Tenant exceeded a scans/min or max-concurrent limit."""

    def __init__(self, *, reason: str, retry_after: int) -> None:
        super().__init__(reason)
        self.reason = reason
        self.retry_after = max(1, int(retry_after))


# ---------------------------------------------------------------------------
# D4 — key store
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class KeyRecord:
    """One issued key. ``token`` is the secret presented in Authorization;
    ``key_id`` is a non-secret identifier used for rotation lineage."""

    key_id: str
    tenant_id: str
    status: str  # "active" | "revoked"
    expires_at: float  # wall-clock epoch seconds; REQUIRED (no infinite keys)
    created_at: float
    rotated_from: str | None = None
    rate_limit: dict[str, int] | None = None

    def is_live(self, now: float) -> bool:
        return self.status == "active" and now <= self.expires_at


class KeyStore:
    """JSON-file-backed key store with a short-TTL in-process read cache.

    The store is the source of truth on disk; ``resolve`` serves from an
    in-memory snapshot that is reloaded once per cache-TTL window. Expiry is
    evaluated against the snapshot on *every* resolve (so it propagates
    immediately); a revoke written by the admin CLI propagates on the next
    reload (≤ cache-TTL). ``clock`` is injectable for deterministic tests.
    """

    def __init__(
        self,
        path: str | os.PathLike[str] | None,
        *,
        cache_ttl_seconds: float = DEFAULT_CACHE_TTL_SECONDS,
        clock: Callable[[], float] = time.time,
        bootstrap_env: dict[str, str] | None = None,
        bootstrap_ttl_seconds: float = DEFAULT_BOOTSTRAP_TTL_SECONDS,
    ) -> None:
        self._path = Path(path) if path is not None else None
        # Hard cap at 5s per the design; a larger configured value is clamped.
        self._cache_ttl = min(float(cache_ttl_seconds), MAX_CACHE_TTL_SECONDS)
        self._clock = clock
        self._bootstrap_ttl = float(bootstrap_ttl_seconds)
        # Reentrant: _mutate holds the lock across _write → force_reload, which
        # re-acquires it. A plain Lock would deadlock on the first mutation.
        self._lock = threading.RLock()
        self._snapshot: dict[str, KeyRecord] = {}
        self._loaded_at: float = float("-inf")
        # Seed a file-less store from the legacy env dict (bootstrap only).
        if self._path is None and bootstrap_env:
            self._snapshot = self._records_from_env(bootstrap_env)
            self._loaded_at = self._clock()
        elif self._path is not None and not self._path.exists() and bootstrap_env:
            # First boot with a store path but no file yet: materialise the
            # env bootstrap onto disk so the admin CLI has something to mutate.
            self._write({tok: rec for tok, rec in self._records_from_env(bootstrap_env).items()})

    # -- resolution -------------------------------------------------------

    def resolve(self, token: str) -> KeyRecord | None:
        """Return the live :class:`KeyRecord` for ``token`` or ``None``.

        ``None`` covers unknown, revoked, and expired keys alike — the caller
        maps all three to ``401`` without leaking which case occurred.
        """
        now = self._clock()
        with self._lock:
            self._maybe_reload(now)
            record = self._snapshot.get(token)
        if record is None or not record.is_live(now):
            return None
        return record

    def _maybe_reload(self, now: float) -> None:
        if self._path is None:
            return  # file-less bootstrap snapshot never reloads
        if now - self._loaded_at <= self._cache_ttl:
            return
        self._snapshot = self._read()
        self._loaded_at = now

    def force_reload(self) -> None:
        """Drop the cache so the next resolve re-reads the file (tests/admin)."""
        with self._lock:
            self._loaded_at = float("-inf")

    # -- persistence ------------------------------------------------------

    def _read(self) -> dict[str, KeyRecord]:
        if self._path is None or not self._path.exists():
            return {}
        raw = json.loads(self._path.read_text())
        keys = raw.get("keys", {}) if isinstance(raw, dict) else {}
        out: dict[str, KeyRecord] = {}
        for token, rec in keys.items():
            out[token] = KeyRecord(
                key_id=str(rec["key_id"]),
                tenant_id=str(rec["tenant_id"]),
                status=str(rec.get("status", "active")),
                expires_at=float(rec["expires_at"]),
                created_at=float(rec.get("created_at", 0.0)),
                rotated_from=(str(rec["rotated_from"]) if rec.get("rotated_from") else None),
                rate_limit=(dict(rec["rate_limit"]) if rec.get("rate_limit") else None),
            )
        return out

    def _write(self, records: dict[str, KeyRecord]) -> None:
        if self._path is None:
            self._snapshot = records
            self._loaded_at = self._clock()
            return
        payload = {
            "version": 1,
            "keys": {
                token: {
                    "key_id": r.key_id,
                    "tenant_id": r.tenant_id,
                    "status": r.status,
                    "expires_at": r.expires_at,
                    "created_at": r.created_at,
                    "rotated_from": r.rotated_from,
                    "rate_limit": r.rate_limit,
                }
                for token, r in records.items()
            },
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True))
        tmp.replace(self._path)  # atomic swap
        self.force_reload()

    def _records_from_env(self, env_dict: dict[str, str]) -> dict[str, KeyRecord]:
        now = self._clock()
        out: dict[str, KeyRecord] = {}
        for idx, (token, tenant_id) in enumerate(env_dict.items()):
            out[token] = KeyRecord(
                key_id=f"bootstrap-{idx}",
                tenant_id=str(tenant_id),
                status="active",
                expires_at=now + self._bootstrap_ttl,
                created_at=now,
            )
        return out

    # -- mutation (admin CLI, no restart) ---------------------------------

    def _mutate(self, fn: Callable[[dict[str, KeyRecord]], None]) -> None:
        with self._lock:
            records = self._read() if self._path is not None else dict(self._snapshot)
            fn(records)
            self._write(records)

    def issue(
        self,
        *,
        token: str,
        key_id: str,
        tenant_id: str,
        ttl_seconds: float,
        rate_limit: dict[str, int] | None = None,
        rotated_from: str | None = None,
    ) -> KeyRecord:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive — no infinite-lived keys")
        now = self._clock()
        record = KeyRecord(
            key_id=key_id,
            tenant_id=tenant_id,
            status="active",
            expires_at=now + float(ttl_seconds),
            created_at=now,
            rotated_from=rotated_from,
            rate_limit=rate_limit,
        )
        self._mutate(lambda recs: recs.__setitem__(token, record))
        return record

    def revoke(self, key_id: str) -> bool:
        found = {"hit": False}

        def _do(recs: dict[str, KeyRecord]) -> None:
            for token, r in list(recs.items()):
                if r.key_id == key_id:
                    recs[token] = KeyRecord(
                        key_id=r.key_id,
                        tenant_id=r.tenant_id,
                        status="revoked",
                        expires_at=r.expires_at,
                        created_at=r.created_at,
                        rotated_from=r.rotated_from,
                        rate_limit=r.rate_limit,
                    )
                    found["hit"] = True

        self._mutate(_do)
        return found["hit"]

    def rotate(
        self,
        *,
        old_key_id: str,
        new_token: str,
        new_key_id: str,
    ) -> KeyRecord:
        """Issue ``new_key_id`` with grace-overlap (valid until the old key's
        ``expires_at``), inheriting the old tenant + rate override. Both keys
        are live until the old expiry; call :meth:`revoke` on ``old_key_id``
        to cut over early."""
        old = self._find_by_key_id(old_key_id)
        if old is None:
            raise KeyError(f"unknown key_id: {old_key_id}")
        now = self._clock()
        grace_ttl = old.expires_at - now
        if grace_ttl <= 0:
            raise ValueError("cannot rotate an already-expired key")
        return self.issue(
            token=new_token,
            key_id=new_key_id,
            tenant_id=old.tenant_id,
            ttl_seconds=grace_ttl,
            rate_limit=old.rate_limit,
            rotated_from=old_key_id,
        )

    def set_rate_limit(self, key_id: str, rate_limit: dict[str, int] | None) -> bool:
        found = {"hit": False}

        def _do(recs: dict[str, KeyRecord]) -> None:
            for token, r in list(recs.items()):
                if r.key_id == key_id:
                    recs[token] = KeyRecord(
                        key_id=r.key_id,
                        tenant_id=r.tenant_id,
                        status=r.status,
                        expires_at=r.expires_at,
                        created_at=r.created_at,
                        rotated_from=r.rotated_from,
                        rate_limit=rate_limit,
                    )
                    found["hit"] = True

        self._mutate(_do)
        return found["hit"]

    def _find_by_key_id(self, key_id: str) -> KeyRecord | None:
        records = self._read() if self._path is not None else self._snapshot
        for r in records.values():
            if r.key_id == key_id:
                return r
        return None

    def list_records(self) -> list[KeyRecord]:
        records = self._read() if self._path is not None else self._snapshot
        return list(records.values())


# ---------------------------------------------------------------------------
# D3 — per-tenant rate limit
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RateLimitConfig:
    scans_per_min: int
    max_concurrent: int

    @staticmethod
    def resolve(record: KeyRecord | None, defaults: "RateLimitConfig") -> "RateLimitConfig":
        """Per-tenant override from the key record, else the phase-1 defaults."""
        override = record.rate_limit if record and record.rate_limit else None
        if not override:
            return defaults
        return RateLimitConfig(
            scans_per_min=int(override.get("scans_per_min", defaults.scans_per_min)),
            max_concurrent=int(override.get("max_concurrent", defaults.max_concurrent)),
        )


@dataclass
class _TokenBucket:
    """Continuously-refilling token bucket. Capacity == burst == N; refill
    rate == N tokens / 60s."""

    capacity: float
    tokens: float
    refill_per_sec: float
    updated_at: float

    def _refill(self, now: float) -> None:
        if now > self.updated_at:
            self.tokens = min(self.capacity, self.tokens + (now - self.updated_at) * self.refill_per_sec)
            self.updated_at = now

    def try_consume(self, now: float, amount: float = 1.0) -> bool:
        self._refill(now)
        if self.tokens >= amount:
            self.tokens -= amount
            return True
        return False

    def retry_after(self, now: float, amount: float = 1.0) -> int:
        self._refill(now)
        if self.refill_per_sec <= 0:
            return CONCURRENCY_RETRY_AFTER_SECONDS
        deficit = amount - self.tokens
        return max(1, math.ceil(deficit / self.refill_per_sec))


@dataclass
class ConcurrencyLease:
    """Handle for one held concurrency slot. Released exactly once."""

    _release: Callable[[], None]
    _released: bool = field(default=False)

    def release(self) -> None:
        if not self._released:
            self._released = True
            self._release()

    def __enter__(self) -> "ConcurrencyLease":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.release()


class RateLimiter:
    """In-process, single-node token-bucket + concurrency limiter, keyed on
    the resolved tenant identity (NOT source IP)."""

    def __init__(self, *, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._buckets: dict[str, _TokenBucket] = {}
        self._inflight: dict[str, int] = {}

    def _bucket_for(self, tenant_id: str, cfg: RateLimitConfig, now: float) -> _TokenBucket:
        bucket = self._buckets.get(tenant_id)
        if bucket is None or bucket.capacity != float(cfg.scans_per_min):
            # (Re)provision on first sight or when the tenant's limit changed.
            bucket = _TokenBucket(
                capacity=float(cfg.scans_per_min),
                tokens=float(cfg.scans_per_min),
                refill_per_sec=float(cfg.scans_per_min) / 60.0,
                updated_at=now,
            )
            self._buckets[tenant_id] = bucket
        return bucket

    def admit_scan(self, tenant_id: str, cfg: RateLimitConfig) -> ConcurrencyLease:
        """Admit one scan for ``tenant_id`` or raise :class:`RateLimited`.

        Checks max-concurrent first (no side effect on failure), then consumes
        one scans/min token. On success the caller holds a
        :class:`ConcurrencyLease` that MUST be released when the scan reaches a
        terminal state.
        """
        now = self._clock()
        with self._lock:
            inflight = self._inflight.get(tenant_id, 0)
            if inflight >= cfg.max_concurrent:
                raise RateLimited(reason="max_concurrent", retry_after=CONCURRENCY_RETRY_AFTER_SECONDS)
            bucket = self._bucket_for(tenant_id, cfg, now)
            if not bucket.try_consume(now):
                raise RateLimited(reason="scans_per_min", retry_after=bucket.retry_after(now))
            self._inflight[tenant_id] = inflight + 1

        return ConcurrencyLease(_release=lambda: self._release(tenant_id))

    def _release(self, tenant_id: str) -> None:
        with self._lock:
            current = self._inflight.get(tenant_id, 0)
            if current <= 1:
                self._inflight.pop(tenant_id, None)
            else:
                self._inflight[tenant_id] = current - 1


# ---------------------------------------------------------------------------
# Facade
# ---------------------------------------------------------------------------


class EdgeGateway:
    """Ties the key store (D4) to the rate limiter (D3). Constructed once per
    app when ``ARE_EDGE_GATEWAY=on``; ``api.app`` calls :meth:`resolve_tenant`
    for every authenticated route and :meth:`admit_scan` on scan-creating
    routes."""

    def __init__(
        self,
        *,
        store: KeyStore,
        limiter: RateLimiter,
        defaults: RateLimitConfig,
    ) -> None:
        self.store = store
        self.limiter = limiter
        self.defaults = defaults

    def resolve_record(self, token: str) -> KeyRecord | None:
        return self.store.resolve(token)

    def resolve_tenant(self, token: str) -> str:
        record = self.store.resolve(token)
        if record is None:
            raise AuthError("authorization_invalid")
        return record.tenant_id

    def admit_scan(self, record: KeyRecord) -> ConcurrencyLease:
        cfg = RateLimitConfig.resolve(record, self.defaults)
        return self.limiter.admit_scan(record.tenant_id, cfg)

    # -- construction from the environment --------------------------------

    @classmethod
    def from_env(
        cls,
        env: dict[str, str] | None = None,
        *,
        clock: Callable[[], float] = time.time,
    ) -> "EdgeGateway | None":
        """Build a gateway when ``ARE_EDGE_GATEWAY=on``, else ``None``.

        Reads the store path + rate defaults from the environment and
        bootstraps the store from ``ARE_API_AUTH_TOKENS_JSON`` when no store
        file exists yet.
        """
        env = os.environ if env is None else env
        if (env.get(EDGE_GATEWAY_ENABLED_ENV) or "").strip().lower() != "on":
            return None

        store_path = env.get(KEY_STORE_PATH_ENV) or None
        bootstrap_env: dict[str, str] | None = None
        raw_tokens = env.get(API_AUTH_TOKENS_ENV)
        if raw_tokens:
            parsed = json.loads(raw_tokens)
            if isinstance(parsed, dict) and parsed:
                bootstrap_env = {str(k): str(v) for k, v in parsed.items()}

        store = KeyStore(
            store_path,
            cache_ttl_seconds=_float_env(env, CACHE_TTL_ENV, DEFAULT_CACHE_TTL_SECONDS),
            clock=clock,
            bootstrap_env=bootstrap_env,
            bootstrap_ttl_seconds=_float_env(env, BOOTSTRAP_TTL_ENV, DEFAULT_BOOTSTRAP_TTL_SECONDS),
        )
        defaults = RateLimitConfig(
            scans_per_min=int(_float_env(env, SCANS_PER_MIN_ENV, DEFAULT_SCANS_PER_MIN)),
            max_concurrent=int(_float_env(env, MAX_CONCURRENT_ENV, DEFAULT_MAX_CONCURRENT)),
        )
        return cls(store=store, limiter=RateLimiter(clock=clock), defaults=defaults)


def _float_env(env: dict[str, str], name: str, default: float) -> float:
    raw = env.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    return float(raw)


# ---------------------------------------------------------------------------
# Admin CLI — `python -m api.edge_gateway <cmd>` (mutates the store, no restart)
# ---------------------------------------------------------------------------


def _cli(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="are-keys",
        description="BackBond Scanner key-store admin (BEAAA-11867). Mutations "
        "take effect within the app's cache-TTL — no restart.",
    )
    parser.add_argument("--store", required=True, help="path to the JSON key-store file")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_issue = sub.add_parser("issue", help="issue a new key")
    p_issue.add_argument("--token", required=True)
    p_issue.add_argument("--key-id", required=True)
    p_issue.add_argument("--tenant-id", required=True)
    p_issue.add_argument("--ttl-seconds", type=float, required=True)
    p_issue.add_argument("--scans-per-min", type=int, default=None)
    p_issue.add_argument("--max-concurrent", type=int, default=None)

    p_revoke = sub.add_parser("revoke", help="revoke a key by key_id")
    p_revoke.add_argument("--key-id", required=True)

    p_rotate = sub.add_parser("rotate", help="rotate a key (grace-overlap)")
    p_rotate.add_argument("--old-key-id", required=True)
    p_rotate.add_argument("--new-token", required=True)
    p_rotate.add_argument("--new-key-id", required=True)

    p_limit = sub.add_parser("set-limit", help="set/clear a per-tenant override")
    p_limit.add_argument("--key-id", required=True)
    p_limit.add_argument("--scans-per-min", type=int, default=None)
    p_limit.add_argument("--max-concurrent", type=int, default=None)
    p_limit.add_argument("--clear", action="store_true")

    sub.add_parser("list", help="list all key records")

    args = parser.parse_args(argv)
    store = KeyStore(args.store)

    if args.cmd == "issue":
        rate = _rate_from_args(args.scans_per_min, args.max_concurrent)
        rec = store.issue(
            token=args.token,
            key_id=args.key_id,
            tenant_id=args.tenant_id,
            ttl_seconds=args.ttl_seconds,
            rate_limit=rate,
        )
        print(f"issued {rec.key_id} tenant={rec.tenant_id} expires_at={rec.expires_at}")
    elif args.cmd == "revoke":
        print("revoked" if store.revoke(args.key_id) else "no such key_id")
    elif args.cmd == "rotate":
        rec = store.rotate(
            old_key_id=args.old_key_id,
            new_token=args.new_token,
            new_key_id=args.new_key_id,
        )
        print(f"rotated → {rec.key_id} (grace until {rec.expires_at}); revoke {args.old_key_id} to cut over")
    elif args.cmd == "set-limit":
        rate = None if args.clear else _rate_from_args(args.scans_per_min, args.max_concurrent)
        print("updated" if store.set_rate_limit(args.key_id, rate) else "no such key_id")
    elif args.cmd == "list":
        for r in store.list_records():
            print(
                f"{r.key_id}\ttenant={r.tenant_id}\tstatus={r.status}"
                f"\texpires_at={r.expires_at}\trotated_from={r.rotated_from}\trate={r.rate_limit}"
            )
    return 0


def _rate_from_args(scans_per_min: int | None, max_concurrent: int | None) -> dict[str, int] | None:
    rate: dict[str, int] = {}
    if scans_per_min is not None:
        rate["scans_per_min"] = scans_per_min
    if max_concurrent is not None:
        rate["max_concurrent"] = max_concurrent
    return rate or None


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())
