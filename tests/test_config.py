"""Unit tests for the typed api/ config layer (BEAAA-12021).

Covers defaults, sentinel parsing, fail-closed empties, the production
signing-key requirement, and a drift guard pinning the centralized env-var
names to the constants still defined in the migrated modules.
"""

import pytest

from api import config
from api.config import ApiConfig


# ---------------------------------------------------------------------------
# Defaults (empty env) — everything falls back to its historical default.
# ---------------------------------------------------------------------------

def test_defaults_on_empty_env():
    cfg = ApiConfig.from_env({})
    # auth / audience: unset ⇒ None, not configured
    assert cfg.api_auth_tokens_raw is None
    assert cfg.api_auth_configured is False
    assert cfg.uw_audience_tenants_raw is None
    # signing key: unset ⇒ None, dev not allowed
    assert cfg.bundle_signing_key_raw is None
    assert cfg.allow_dev_signing_key is False
    # scan wiring / rescans defaults
    assert cfg.production_scan_wiring is False
    assert cfg.rescans_measured_off is False  # default is measured (on)
    # scan_handlers defaults
    assert cfg.execution_environment == "scanner-runtime"
    assert cfg.scan_tier_enabled is True
    assert cfg.pinned_corpus_snapshot_hash == ""
    assert cfg.scan_webhook_enabled is True
    # runtime factory defaults
    assert cfg.scorer_envelope_drain_timeout_seconds == 10.0
    assert cfg.scorer_row_recv_timeout_seconds(42.0) == 42.0
    assert cfg.scorer_real_enabled is True
    assert cfg.walker_customer_agent_enabled is False
    assert cfg.fired_probe_ledger_path is None
    assert cfg.v0_corpus_dir is None
    assert cfg.fired_probe_audit_spine_path("/d/default") == "/d/default"
    assert cfg.scan_log_path("/d/scan_log") == "/d/scan_log"


# ---------------------------------------------------------------------------
# Sentinels — exact string semantics preserved.
# ---------------------------------------------------------------------------

def test_rescans_measured_off_sentinel():
    assert ApiConfig.from_env({"ARE_RESCANS_MEASURED": "off"}).rescans_measured_off is True
    # any other value keeps the measured route active (rollback Path 1 sentinel)
    for v in ("on", "", "OFF", "0", "true"):
        assert ApiConfig.from_env({"ARE_RESCANS_MEASURED": v}).rescans_measured_off is False


def test_scan_tier_and_webhook_off_sentinel():
    assert ApiConfig.from_env({"ARE_SCAN_TIER": "off"}).scan_tier_enabled is False
    assert ApiConfig.from_env({"ARE_SCAN_TIER": "on"}).scan_tier_enabled is True
    assert ApiConfig.from_env({"ARE_SCAN_TIER": "anything"}).scan_tier_enabled is True
    assert ApiConfig.from_env({"ARE_SCAN_WEBHOOK": "off"}).scan_webhook_enabled is False
    assert ApiConfig.from_env({"ARE_SCAN_WEBHOOK": "whatever"}).scan_webhook_enabled is True


def test_production_scan_wiring_only_exact_one():
    assert ApiConfig.from_env({"ARE_PRODUCTION_SCAN_WIRING": "1"}).production_scan_wiring is True
    for v in ("0", "on", "true", "", "11"):
        assert ApiConfig.from_env({"ARE_PRODUCTION_SCAN_WIRING": v}).production_scan_wiring is False


@pytest.mark.parametrize("var,accessor", [
    ("ARE_SCORER_REAL", "scorer_real_enabled"),
    ("ARE_WALKER_CUSTOMER_AGENT", "walker_customer_agent_enabled"),
])
def test_truthy_token_sets(var, accessor):
    for v in ("on", "1", "true", "yes", " YES ", "True"):
        assert getattr(ApiConfig.from_env({var: v}), accessor) is True
    for v in ("off", "0", "no", "false", ""):
        assert getattr(ApiConfig.from_env({var: v}), accessor) is False


# ---------------------------------------------------------------------------
# Fail-closed empties + JSON-raw passthrough.
# ---------------------------------------------------------------------------

def test_auth_configured_truthiness():
    assert ApiConfig.from_env({"ARE_API_AUTH_TOKENS_JSON": "{}"}).api_auth_configured is True
    assert ApiConfig.from_env({"ARE_API_AUTH_TOKENS_JSON": ""}).api_auth_configured is False


def test_api_auth_required_logic():
    unconfigured = ApiConfig.from_env({})
    assert unconfigured.api_auth_required("local") is False
    assert unconfigured.api_auth_required("pilot") is True
    assert unconfigured.api_auth_required("prod") is True
    configured = ApiConfig.from_env({"ARE_API_AUTH_TOKENS_JSON": '{"t":"x"}'})
    assert configured.api_auth_required("local") is True


# ---------------------------------------------------------------------------
# Signing key: require + resolve, mirroring create_app / bundle._signing_key.
# ---------------------------------------------------------------------------

DEV = b"dev-key"


def test_require_production_signing_key():
    with pytest.raises(ValueError):
        ApiConfig.from_env({}).require_production_signing_key()
    # dev override satisfies the startup guard
    ApiConfig.from_env({"ARE_ALLOW_DEV_SIGNING_KEY": "true"}).require_production_signing_key()
    # a real key satisfies it
    ApiConfig.from_env({"ARE_BUNDLE_SIGNING_KEY": "k"}).require_production_signing_key()
    # only the exact "true" token unlocks dev signing
    with pytest.raises(ValueError):
        ApiConfig.from_env({"ARE_ALLOW_DEV_SIGNING_KEY": "yes"}).require_production_signing_key()


def test_bundle_signing_key_resolution():
    assert ApiConfig.from_env({"ARE_BUNDLE_SIGNING_KEY": "sekret"}).bundle_signing_key(DEV) == b"sekret"
    assert ApiConfig.from_env({"ARE_ALLOW_DEV_SIGNING_KEY": "true"}).bundle_signing_key(DEV) == DEV
    with pytest.raises(ValueError):
        ApiConfig.from_env({}).bundle_signing_key(DEV)


# ---------------------------------------------------------------------------
# Float coercion timing — malformed raises exactly when accessed.
# ---------------------------------------------------------------------------

def test_float_coercion_and_malformed():
    assert ApiConfig.from_env({"ARE_SCORER_ENVELOPE_DRAIN_TIMEOUT_SECONDS": "2.5"}).scorer_envelope_drain_timeout_seconds == 2.5
    assert ApiConfig.from_env({"ARE_SCORER_ROW_RECV_TIMEOUT_SECONDS": "7"}).scorer_row_recv_timeout_seconds(1.0) == 7.0
    # Construction is lenient; the ValueError only surfaces on access of the bad field.
    cfg = ApiConfig.from_env({"ARE_SCORER_ENVELOPE_DRAIN_TIMEOUT_SECONDS": "not-a-float"})
    assert cfg.scan_tier_enabled is True  # unrelated field is unaffected
    with pytest.raises(ValueError):
        _ = cfg.scorer_envelope_drain_timeout_seconds


# ---------------------------------------------------------------------------
# Injected-env isolation: from_env never leaks os.environ when a map is given.
# ---------------------------------------------------------------------------

def test_injected_env_isolated(monkeypatch):
    monkeypatch.setenv("ARE_SCAN_TIER", "off")
    # explicit empty mapping ignores the process env
    assert ApiConfig.from_env({}).scan_tier_enabled is True
    # None ⇒ reads os.environ
    assert ApiConfig.from_env().scan_tier_enabled is False


# ---------------------------------------------------------------------------
# Drift guard: centralized names must match the constants left in the modules.
# ---------------------------------------------------------------------------

def test_names_match_module_constants():
    from api import app, scan_handlers, scan_runtime_factory

    assert config.API_AUTH_TOKENS_ENV == app.API_AUTH_TOKENS_ENV
    assert config.UW_AUDIENCE_TENANTS_ENV == app.UW_AUDIENCE_TENANTS_ENV
    assert config.PRODUCTION_SCAN_WIRING_ENV == app.PRODUCTION_SCAN_WIRING_ENV
    assert config.RESCANS_MEASURED_ENV == app.RESCANS_MEASURED_ENV
    assert config.RESCANS_MEASURED_OFF == app.RESCANS_MEASURED_OFF

    assert config.SCAN_TIER_ENV == scan_handlers.SCAN_TIER_ENV
    assert config.PINNED_CORPUS_SNAPSHOT_HASH_ENV == scan_handlers.PINNED_CORPUS_SNAPSHOT_HASH_ENV
    assert config.SCAN_WEBHOOK_ENV == scan_handlers.SCAN_WEBHOOK_ENV

    assert config.SCORER_REAL_ENV == scan_runtime_factory.ARE_SCORER_REAL_ENV
    assert config.WALKER_CUSTOMER_AGENT_ENV == scan_runtime_factory.ARE_WALKER_CUSTOMER_AGENT_ENV
    assert config.FIRED_PROBE_AUDIT_SPINE_PATH_ENV == scan_runtime_factory.ARE_FIRED_PROBE_AUDIT_SPINE_PATH_ENV
    assert config.FIRED_PROBE_LEDGER_PATH_ENV == scan_runtime_factory.ARE_FIRED_PROBE_LEDGER_PATH_ENV
    assert config.SCAN_LOG_PATH_ENV == scan_runtime_factory.ARE_SCAN_LOG_PATH_ENV
