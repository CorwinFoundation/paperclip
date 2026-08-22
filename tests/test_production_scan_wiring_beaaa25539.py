"""Startup regressions for strict production scan wiring configuration."""

from unittest.mock import patch

import pytest

from api.app import create_app


@pytest.mark.parametrize("value", ["0", "false", "off", "no"])
def test_false_tokens_start_without_production_signing_key(monkeypatch, value):
    monkeypatch.setenv("ARE_PRODUCTION_SCAN_WIRING", value)
    monkeypatch.delenv("ARE_BUNDLE_SIGNING_KEY", raising=False)
    monkeypatch.delenv("ARE_ALLOW_DEV_SIGNING_KEY", raising=False)
    app = create_app()
    assert app.state.scan_runtime is None


@pytest.mark.parametrize("value", ["1", "true", "on", "yes"])
def test_true_tokens_preserve_production_signing_key_enforcement(monkeypatch, value):
    monkeypatch.setenv("ARE_PRODUCTION_SCAN_WIRING", value)
    monkeypatch.delenv("ARE_BUNDLE_SIGNING_KEY", raising=False)
    monkeypatch.delenv("ARE_ALLOW_DEV_SIGNING_KEY", raising=False)
    with pytest.raises(ValueError, match="ARE_BUNDLE_SIGNING_KEY must be set in production"):
        create_app()


def test_absent_token_preserves_default_off_startup(monkeypatch):
    monkeypatch.delenv("ARE_PRODUCTION_SCAN_WIRING", raising=False)
    monkeypatch.delenv("ARE_BUNDLE_SIGNING_KEY", raising=False)
    monkeypatch.delenv("ARE_ALLOW_DEV_SIGNING_KEY", raising=False)
    assert create_app().state.scan_runtime is None


def test_invalid_token_fails_before_runtime_factory(monkeypatch):
    monkeypatch.setenv("ARE_PRODUCTION_SCAN_WIRING", "sometimes")
    with patch("api.scan_runtime_factory.make_default_production_scan_runtime") as factory:
        with pytest.raises(ValueError, match="ARE_PRODUCTION_SCAN_WIRING must be one of"):
            create_app()
    factory.assert_not_called()
