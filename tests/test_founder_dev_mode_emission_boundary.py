"""Production-boundary guard wiring for Founder-Dev-Mode (BEAAA-WAVE1B-DRAFT).

Proves the dev-mode-scored surface fails closed at customer / production
emission points, and is a no-op in production (flag off):

* api.scan_response_envelopes.customer_payload  (per-response customer surface)
* tools.active_test_registry.main               (published-count reconcile)
* tools.validate_content_candidate_registration.main  (published-count validator)
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from api import scan_response_envelopes as env
from tools import active_test_registry as reg
from tools import validate_content_candidate_registration as vccr
from tools.founder_dev_mode import FOUNDER_DEV_MODE_ENV, FounderDevModeError


def _fake_result():
    return SimpleNamespace(
        scan_id="scan-1",
        status="complete",
        scanner_version="0.5.0",
        corpus_version="c@0.1.0",
        incomplete=False,
        incomplete_reason=None,
    )


def test_customer_payload_emits_in_production_default(monkeypatch):
    monkeypatch.delenv(FOUNDER_DEV_MODE_ENV, raising=False)
    payload = env.customer_payload(_fake_result())
    assert payload["scan_id"] == "scan-1"


def test_customer_payload_fails_closed_in_dev_mode(monkeypatch):
    monkeypatch.setenv(FOUNDER_DEV_MODE_ENV, "on")
    with pytest.raises(FounderDevModeError):
        env.customer_payload(_fake_result())


def test_count_reconcile_fails_closed_in_dev_mode(monkeypatch):
    monkeypatch.setenv(FOUNDER_DEV_MODE_ENV, "on")
    monkeypatch.setattr("sys.argv", ["active_test_registry"])
    with pytest.raises(FounderDevModeError):
        reg.main()


def test_count_validator_fails_closed_in_dev_mode(monkeypatch):
    monkeypatch.setenv(FOUNDER_DEV_MODE_ENV, "on")
    with pytest.raises(FounderDevModeError):
        vccr.main()


def test_uw_payload_is_not_gated(monkeypatch):
    # Underwriter/internal surface is not a customer boundary; it must still work
    # in dev mode (internal evidence review is the whole point of dev mode).
    monkeypatch.setenv(FOUNDER_DEV_MODE_ENV, "on")
    out = env.uw_payload(
        SimpleNamespace(
            scan_id="s",
            status="complete",
            scanner_version="0.5.0",
            corpus_version="c@0.1.0",
            rows_emitted=0,
            k_replicates=5,
            live_probe_variance_band=0.0,
            spread_per_dim={},
            pillar_spread={},
            total_spread=0.0,
            incomplete=False,
            incomplete_reason=None,
            full_suite_manifest={},
            anti_gaming=SimpleNamespace(to_dict=lambda: {}),
            dim_results=[],
        )
    )
    assert out["scan_id"] == "s"
