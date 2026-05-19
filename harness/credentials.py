"""Credential vault adapter — BEAAA-538 / ADR-0009 §5 #3.

Defensive control implementing the canonical §6b #2 wording
(CEO-accepted on BEAAA-532 / Founder-locked on `contracts/are-scanner-
api-v1.md@v1.0.1`):

> Credential lives in the Prober worker's process memory only. Never
> serialized to disk. Never part of the IPC payload to the Scorer
> (Scorer process has no credential read path). Deleted at scan
> finalize. Scoped, short-lived, read-only only; rotation is the
> customer's lever. Vault adapter (§5 #3) implements this as the only
> contract; any other reach for the credential is a contract breach.

This module is the **only** credential read path in the live-probe
build (Hard Invariant #4 of BEAAA-538). It is imported in the Prober
worker process only. The Scorer worker has no import of this module —
verified at merge time by import-graph check (§Acceptance #3).

Surface
-------
- ``CredentialRef`` — opaque, hashable, frozen identifier wrapping a
  validated str token. The hosted ``/scan`` request carries the
  ``credential_ref`` as a JSON string; the API edge converts it to a
  ``CredentialRef`` before calling ``register``.
- ``CredentialVault`` — in-memory, instance-scoped. Methods:
  - ``register(credential_ref, secret)`` — API-edge side, before
    Prober subprocess spawn.
  - ``resolve(credential_ref) -> str | None`` — Prober side only.
  - ``forget(credential_ref)`` — per-ref deletion.
  - ``forget_all()`` — scan-finalize deletion (success or failure).

Anti-leak posture
-----------------
The secret bytes are held in a module-level dict ``_VAULT_STORE``
keyed by an opaque per-vault token. The vault instance itself stores
only the token, not the secret. Consequence:

- ``vault.__dict__`` contains only ``_vault_id`` — no secret bytes.
- ``repr(vault)`` / ``str(vault)`` return a fixed safe rendering.
- ``pickle.dumps(vault)`` round-trips an *empty* state (``__getstate__``
  returns ``{}``); the restored vault is detached from the store and
  resolves to ``None`` for every ref. Pickling never carries a secret
  across a process boundary.
- ``json.dumps(vault.__dict__)`` cannot emit the secret because the
  secret is not an instance attribute.

These properties are enforced by ``tests/test_credentials_beaaa538.py``
using the literal probe ``SECRET_TEST_DO_NOT_LEAK_a3f9``.
"""
from __future__ import annotations

import re
import secrets as _secrets
from dataclasses import dataclass
from typing import Final

# Module-level secret store. Keyed by an opaque per-vault token so that
# instance ``__dict__`` / ``__getstate__`` / repr surfaces never carry
# secret bytes. NEVER expose this name across the IPC boundary.
_VAULT_STORE: dict[str, dict[str, str]] = {}

_CRED_REF_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-:.]{0,127}$")


class CredentialError(ValueError):
    """Raised for malformed ``credential_ref`` values."""


@dataclass(frozen=True, slots=True)
class CredentialRef:
    """Opaque identifier for a credential held in a ``CredentialVault``.

    The hosted ``/scan`` request carries the ref as a plain string
    (``credential_ref: str``). The API edge calls :meth:`from_str` to
    obtain a typed ``CredentialRef`` before reaching the vault.

    Format constraint: leading alphanumeric, then alphanumeric or
    ``_ - : .`` up to 128 chars total. Conservative on purpose — the
    ref is logged and traced, and locking down the character set
    prevents shell / log / URL injection downstream.
    """

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or not _CRED_REF_RE.match(self.value):
            raise CredentialError("invalid credential_ref format")

    @classmethod
    def from_str(cls, value: str) -> "CredentialRef":
        return cls(value)

    def __str__(self) -> str:  # ref strings are non-secret by construction
        return self.value


def _coerce_ref(ref: "CredentialRef | str") -> CredentialRef:
    return ref if isinstance(ref, CredentialRef) else CredentialRef.from_str(ref)


class CredentialVault:
    """In-memory, instance-scoped credential vault.

    Lifecycle (per scan):
        1. API edge: ``vault = CredentialVault()`` once per scan request.
        2. API edge: ``vault.register(ref, secret)`` with the validated
           credential. Subprocess spawn carries the vault into the
           Prober worker; the secret is never re-serialized.
        3. Prober: ``vault.resolve(ref)`` exactly when it needs to call
           the target endpoint. Result is held in a local variable
           that goes out of scope when the call returns.
        4. Scan finalize (success OR failure): ``vault.forget_all()``.

    The Scorer worker MUST NOT import this module. The merge-time
    import-graph check (BEAAA-538 §Acceptance #3) enforces that.
    """

    __slots__ = ("_vault_id",)

    def __init__(self) -> None:
        token = _secrets.token_hex(16)
        # token uniqueness is overwhelming, but guard anyway.
        while token in _VAULT_STORE:
            token = _secrets.token_hex(16)
        _VAULT_STORE[token] = {}
        object.__setattr__(self, "_vault_id", token)

    # ------------------------------------------------------------------
    # Public surface (per BEAAA-538 §Scope)
    # ------------------------------------------------------------------

    def _bucket(self) -> dict[str, str] | None:
        token = getattr(self, "_vault_id", None)
        if token is None:
            return None
        return _VAULT_STORE.get(token)

    def register(self, credential_ref: "CredentialRef | str", secret: str) -> None:
        """Store ``secret`` under ``credential_ref``. API-edge side."""
        if not isinstance(secret, str) or not secret:
            raise CredentialError("secret must be a non-empty str")
        ref = _coerce_ref(credential_ref)
        bucket = self._bucket()
        if bucket is None:
            # Vault was detached (e.g. unpickled or forget_all-ed).
            # Re-attaching would silently re-grant a credential read
            # path that the contract said was gone. Refuse.
            raise CredentialError("vault is detached; cannot register")
        bucket[ref.value] = secret

    def resolve(self, credential_ref: "CredentialRef | str") -> str | None:
        """Return the secret for ``credential_ref`` or ``None``. Prober side."""
        ref = _coerce_ref(credential_ref)
        bucket = self._bucket()
        if bucket is None:
            return None
        return bucket.get(ref.value)

    def forget(self, credential_ref: "CredentialRef | str") -> None:
        """Delete one credential. Idempotent — no error if absent."""
        ref = _coerce_ref(credential_ref)
        bucket = self._bucket()
        if bucket is not None:
            bucket.pop(ref.value, None)

    def forget_all(self) -> None:
        """Delete every credential held by this vault. Scan-finalize hook."""
        token = getattr(self, "_vault_id", None)
        if token is None:
            return
        bucket = _VAULT_STORE.pop(token, None)
        if bucket is not None:
            # Best-effort overwrite before drop so a held reference
            # to the dict (none expected, but be paranoid) no longer
            # carries secret bytes.
            for k in list(bucket.keys()):
                bucket[k] = ""
            bucket.clear()

    # ------------------------------------------------------------------
    # Anti-leak overrides
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return "<CredentialVault: opaque>"

    def __str__(self) -> str:
        return "<CredentialVault: opaque>"

    def __getstate__(self) -> dict:
        # Pickling must not carry secrets across a process boundary —
        # the only legitimate cross-process transport is subprocess
        # spawn with fork-inherited memory, not pickle.
        return {}

    def __setstate__(self, state: dict) -> None:  # noqa: ARG002 — signature required
        # Restored vaults are detached: no _vault_id, so resolve()
        # returns None and register() refuses.
        pass

    def __reduce__(self):
        # Belt-and-braces: explicit reducer returning a fresh detached
        # instance. ``copyreg.__newobj__`` would otherwise call
        # ``__init__`` and accidentally allocate a new entry in the
        # store on the unpickling side. The lambda below produces a
        # vault with no ``_vault_id`` set — fully inert.
        return (_detached_vault, ())

    def __del__(self) -> None:
        # GC-time best-effort cleanup. Callers MUST still invoke
        # ``forget_all`` explicitly per the contract; this is a safety
        # net for crash paths.
        try:
            self.forget_all()
        except Exception:  # noqa: BLE001 — never raise from __del__
            pass


def _detached_vault() -> CredentialVault:
    """Construct a vault that is NOT registered in ``_VAULT_STORE``.

    Used by :meth:`CredentialVault.__reduce__` so that unpickling does
    not silently allocate a fresh vault slot on the receiving side.
    The returned object has no ``_vault_id``; ``resolve`` returns
    ``None`` and ``register`` raises.
    """
    vault = CredentialVault.__new__(CredentialVault)
    # No _vault_id set: every method that consults _VAULT_STORE falls
    # through the "vault is detached" path.
    return vault


__all__ = [
    "CredentialError",
    "CredentialRef",
    "CredentialVault",
]
