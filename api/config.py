"""Typed, startup-validated configuration layer for the ``api/`` package.

BEAAA-12021 (parent BEAAA-12018, third-party code-review action item #1).
Consolidates the ad-hoc ``os.environ.get()`` reads that were scattered across
``api/app.py``, ``api/bundle.py``, ``api/scan_handlers.py``, and
``api/scan_runtime_factory.py`` into a single typed accessor with the *exact*
current defaults, sentinels, and coercions.

Design notes
------------
* **Behavior-preserving by construction.** Each env var is read + coerced
  *lazily, on attribute access*, exactly where the pre-refactor call site read
  it. That preserves per-field read timing and per-field failure semantics
  (e.g. a malformed ``ARE_SCORER_ROW_RECV_TIMEOUT_SECONDS`` still raises
  ``ValueError`` only when that specific field is accessed, at
  ``scan_runtime_factory`` import time — not eagerly at ``ApiConfig``
  construction). A plain ``pydantic.BaseModel`` (which validates *all* fields
  at construction) would have moved those failures earlier and changed
  behavior, so we use a frozen dataclass over the raw mapping instead. pydantic
  2.13.3 is already a dependency; ``pydantic-settings`` is intentionally **not**
  added (no new supply-chain dependency).
* **Injected-env pattern preserved.** ``from_env(env)`` defaults to
  ``os.environ`` but accepts any ``Mapping[str, str]`` for testability, mirroring
  the pattern already used by ``runtime_guards.py`` and ``edge_gateway.py``
  (which are intentionally *not* migrated — they already take an injected env).
* **Env-var names are the single source of truth here.** The pre-existing
  name constants in the migrated modules are kept for their message/f-string
  references; ``tests/test_config.py`` asserts they stay byte-identical to the
  names defined below, so the two cannot drift.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

# ---------------------------------------------------------------------------
# Canonical env-var names (single source of truth). These mirror the constants
# historically defined inline in the migrated modules; a drift test pins them.
# ---------------------------------------------------------------------------

#: app.py
API_AUTH_TOKENS_ENV: str = "ARE_API_AUTH_TOKENS_JSON"
UW_AUDIENCE_TENANTS_ENV: str = "ARE_UW_AUDIENCE_TENANTS_JSON"
BUNDLE_SIGNING_KEY_ENV: str = "ARE_BUNDLE_SIGNING_KEY"
ALLOW_DEV_SIGNING_KEY_ENV: str = "ARE_ALLOW_DEV_SIGNING_KEY"
PRODUCTION_SCAN_WIRING_ENV: str = "ARE_PRODUCTION_SCAN_WIRING"
RESCANS_MEASURED_ENV: str = "ARE_RESCANS_MEASURED"
#: Rollback Path 1 sentinel: any value other than ``"off"`` keeps the measured
#: /scanner/v1/rescans route active (ADR-0017 §6).
RESCANS_MEASURED_OFF: str = "off"

#: scan_handlers.py
EXECUTION_ENVIRONMENT_ENV: str = "BACKBOND_EXECUTION_ENVIRONMENT"
EXECUTION_ENVIRONMENT_DEFAULT: str = "scanner-runtime"
SCAN_TIER_ENV: str = "ARE_SCAN_TIER"
PINNED_CORPUS_SNAPSHOT_HASH_ENV: str = "ARE_PINNED_CORPUS_SNAPSHOT_HASH"
SCAN_WEBHOOK_ENV: str = "ARE_SCAN_WEBHOOK"

#: scan_runtime_factory.py
SCORER_ENVELOPE_DRAIN_TIMEOUT_SECONDS_ENV: str = "ARE_SCORER_ENVELOPE_DRAIN_TIMEOUT_SECONDS"
SCORER_ENVELOPE_DRAIN_TIMEOUT_DEFAULT: str = "10"
SCORER_ROW_RECV_TIMEOUT_SECONDS_ENV: str = "ARE_SCORER_ROW_RECV_TIMEOUT_SECONDS"
SCORER_REAL_ENV: str = "ARE_SCORER_REAL"
WALKER_CUSTOMER_AGENT_ENV: str = "ARE_WALKER_CUSTOMER_AGENT"
FIRED_PROBE_AUDIT_SPINE_PATH_ENV: str = "ARE_FIRED_PROBE_AUDIT_SPINE_PATH"
FIRED_PROBE_LEDGER_PATH_ENV: str = "ARE_FIRED_PROBE_LEDGER_PATH"
V0_CORPUS_DIR_ENV: str = "ARE_V0_CORPUS_DIR"
SCAN_LOG_PATH_ENV: str = "ARE_SCAN_LOG_PATH"

#: Truthy-token sets used by the pre-existing boolean toggles (preserved exactly).
_TRUTHY: frozenset[str] = frozenset({"on", "1", "true", "yes"})


@dataclass(frozen=True)
class ApiConfig:
    """Lazy typed accessor over an env mapping.

    Construct via :meth:`from_env`. Every property reads the underlying mapping
    on access, coercing with the same default/sentinel/parse rules as the
    pre-refactor call sites, so read timing and failure timing are unchanged.
    """

    env: Mapping[str, str]

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "ApiConfig":
        return cls(env=os.environ if env is None else env)

    # -- app.py: auth + audience --------------------------------------------

    @property
    def api_auth_tokens_raw(self) -> str | None:
        """Raw ``ARE_API_AUTH_TOKENS_JSON`` value (``None`` if unset)."""
        return self.env.get(API_AUTH_TOKENS_ENV)

    @property
    def api_auth_configured(self) -> bool:
        """Truthy iff a token mapping is configured (matches ``if os.environ.get(...)``)."""
        return bool(self.api_auth_tokens_raw)

    def api_auth_required(self, runtime_profile: str) -> bool:
        """Mirror of ``_api_auth_required``: required if a mapping is configured
        or the runtime profile is pilot/prod."""
        if self.api_auth_configured:
            return True
        return runtime_profile in {"pilot", "prod"}

    @property
    def uw_audience_tenants_raw(self) -> str | None:
        """Raw ``ARE_UW_AUDIENCE_TENANTS_JSON`` value (``None`` if unset)."""
        return self.env.get(UW_AUDIENCE_TENANTS_ENV)

    # -- app.py + bundle.py: bundle signing key -----------------------------

    @property
    def bundle_signing_key_raw(self) -> str | None:
        return self.env.get(BUNDLE_SIGNING_KEY_ENV)

    @property
    def allow_dev_signing_key(self) -> bool:
        return self.env.get(ALLOW_DEV_SIGNING_KEY_ENV) == "true"

    def require_production_signing_key(self) -> None:
        """Startup guard (create_app): raise unless a signing key is set or dev
        signing is explicitly allowed. Byte-identical to the previous inline
        check in ``create_app``."""
        if not self.bundle_signing_key_raw and not self.allow_dev_signing_key:
            raise ValueError("ARE_BUNDLE_SIGNING_KEY must be set in production")

    def bundle_signing_key(self, dev_key: bytes) -> bytes:
        """Resolve the bundle signing key bytes. Mirrors ``bundle._signing_key``:
        the configured key if set, else the dev key when dev signing is allowed,
        else raise."""
        raw = self.bundle_signing_key_raw
        if raw:
            return raw.encode("utf-8")
        if self.allow_dev_signing_key:
            return dev_key
        raise ValueError("ARE_BUNDLE_SIGNING_KEY must be set in production")

    # -- app.py: scan wiring + rescans rollback -----------------------------

    @property
    def production_scan_wiring(self) -> bool:
        """``ARE_PRODUCTION_SCAN_WIRING == "1"`` (BEAAA-553 opt-in, default off)."""
        return self.env.get(PRODUCTION_SCAN_WIRING_ENV) == "1"

    @property
    def rescans_measured_off(self) -> bool:
        """``ARE_RESCANS_MEASURED == "off"`` (rollback Path 1; default: measured)."""
        return self.env.get(RESCANS_MEASURED_ENV) == RESCANS_MEASURED_OFF

    # -- scan_handlers.py ---------------------------------------------------

    @property
    def execution_environment(self) -> str:
        return self.env.get(EXECUTION_ENVIRONMENT_ENV, EXECUTION_ENVIRONMENT_DEFAULT)

    @property
    def scan_tier_enabled(self) -> bool:
        """``ARE_SCAN_TIER != "off"`` (default on; Tier-1 rollback disables smoke)."""
        return self.env.get(SCAN_TIER_ENV, "on") != "off"

    @property
    def pinned_corpus_snapshot_hash(self) -> str:
        return self.env.get(PINNED_CORPUS_SNAPSHOT_HASH_ENV, "")

    @property
    def scan_webhook_enabled(self) -> bool:
        """``ARE_SCAN_WEBHOOK != "off"`` (default on; Tier-1 rollback disables)."""
        return self.env.get(SCAN_WEBHOOK_ENV, "on") != "off"

    # -- scan_runtime_factory.py --------------------------------------------

    @property
    def scorer_envelope_drain_timeout_seconds(self) -> float:
        """``float(ARE_SCORER_ENVELOPE_DRAIN_TIMEOUT_SECONDS or "10")``.
        Coerced on access (import-time at the call site), so a malformed value
        raises ``ValueError`` exactly as before."""
        return float(
            self.env.get(
                SCORER_ENVELOPE_DRAIN_TIMEOUT_SECONDS_ENV,
                SCORER_ENVELOPE_DRAIN_TIMEOUT_DEFAULT,
            )
        )

    def scorer_row_recv_timeout_seconds(self, default: float) -> float:
        """``float(ARE_SCORER_ROW_RECV_TIMEOUT_SECONDS or str(default))``. The
        default is injected because it lives in the orchestrator module."""
        return float(
            self.env.get(SCORER_ROW_RECV_TIMEOUT_SECONDS_ENV, str(default))
        )

    @property
    def scorer_real_enabled(self) -> bool:
        """``ARE_SCORER_REAL`` (default on). BEAAA-2182 Track A kill-switch."""
        return self.env.get(SCORER_REAL_ENV, "on").strip().lower() in _TRUTHY

    @property
    def walker_customer_agent_enabled(self) -> bool:
        """``ARE_WALKER_CUSTOMER_AGENT`` (default off). BEAAA-5414 Track B walker."""
        return self.env.get(WALKER_CUSTOMER_AGENT_ENV, "0").strip().lower() in _TRUTHY

    def fired_probe_audit_spine_path(self, default: str) -> str:
        return self.env.get(FIRED_PROBE_AUDIT_SPINE_PATH_ENV, default)

    @property
    def fired_probe_ledger_path(self) -> str | None:
        return self.env.get(FIRED_PROBE_LEDGER_PATH_ENV)

    @property
    def v0_corpus_dir(self) -> str | None:
        return self.env.get(V0_CORPUS_DIR_ENV)

    def scan_log_path(self, default: str) -> str:
        return self.env.get(SCAN_LOG_PATH_ENV, default)
