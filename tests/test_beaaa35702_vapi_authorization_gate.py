"""BEAAA-35702: the VAPI lanes fail closed without a written authorization.

The weekly `backbond-scan-vapi.timer` points at `https://api.vapi.ai`, a
partner's live production API. The BEAAA-32522 release boundary permits that
traffic only under a current written authorization. These tests assert the
refusal happens *before* any credential is read and before any HTTP client or
session object exists, because the exit code alone would not distinguish
"refused" from "dispatched and then failed".
"""

from __future__ import annotations

import importlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

scheduled_scan = importlib.import_module("scripts.scheduled_scan")


class _Tripwire(Exception):
    """Raised by anything that must not run once the gate has refused."""


@pytest.fixture(autouse=True)
def _clear_gate(monkeypatch):
    monkeypatch.delenv(scheduled_scan.VAPI_AUTH_REF_ENV, raising=False)
    monkeypatch.delenv(scheduled_scan.VAPI_AUTH_EXPIRES_ENV, raising=False)


@pytest.fixture
def no_traffic(monkeypatch):
    """Trip on the credential read and on every HTTP client constructor."""

    def _secrets_tripwire(*args, **kwargs):
        raise _Tripwire("credential environment was read")

    monkeypatch.setattr(scheduled_scan, "map_secret_environment", _secrets_tripwire)

    for module_name, attrs in (
        ("httpx", ("Client", "AsyncClient")),
        ("requests", ("Session",)),
        ("urllib.request", ("urlopen",)),
        ("http.client", ("HTTPConnection", "HTTPSConnection")),
    ):
        try:
            module = importlib.import_module(module_name)
        except ImportError:  # pragma: no cover - optional dependency
            continue
        for attr in attrs:
            if not hasattr(module, attr):
                continue

            def _client_tripwire(*args, _what=f"{module_name}.{attr}", **kwargs):
                raise _Tripwire(f"{_what} was constructed")

            monkeypatch.setattr(module, attr, _client_tripwire)


def _iso(delta: timedelta) -> str:
    return (datetime.now(timezone.utc) + delta).isoformat().replace("+00:00", "Z")


@pytest.mark.parametrize("job", sorted(scheduled_scan.VAPI_TRAFFIC_JOBS))
def test_missing_gate_refuses_before_any_traffic(job, no_traffic):
    with pytest.raises(scheduled_scan.VapiAuthorizationError) as excinfo:
        scheduled_scan.run_scheduled_scan(job)
    message = str(excinfo.value)
    assert scheduled_scan.VAPI_AUTH_REF_ENV in message
    assert scheduled_scan.VAPI_AUTH_EXPIRES_ENV in message
    assert "No VAPI traffic was sent." in message


def test_half_configured_gate_refuses(monkeypatch, no_traffic):
    monkeypatch.setenv(scheduled_scan.VAPI_AUTH_REF_ENV, "BEAAA-32522")
    with pytest.raises(scheduled_scan.VapiAuthorizationError) as excinfo:
        scheduled_scan.run_scheduled_scan("vapi")
    assert scheduled_scan.VAPI_AUTH_EXPIRES_ENV in str(excinfo.value)
    assert scheduled_scan.VAPI_AUTH_REF_ENV not in str(excinfo.value).split("Missing:")[1]


def test_expired_authorization_refuses(monkeypatch, no_traffic):
    monkeypatch.setenv(scheduled_scan.VAPI_AUTH_REF_ENV, "BEAAA-32522")
    monkeypatch.setenv(scheduled_scan.VAPI_AUTH_EXPIRES_ENV, _iso(timedelta(seconds=-1)))
    with pytest.raises(scheduled_scan.VapiAuthorizationError) as excinfo:
        scheduled_scan.run_scheduled_scan("vapi-garak")
    assert "expired at" in str(excinfo.value)
    assert "No VAPI traffic was sent." in str(excinfo.value)


def test_unparseable_expiry_refuses(monkeypatch, no_traffic):
    monkeypatch.setenv(scheduled_scan.VAPI_AUTH_REF_ENV, "BEAAA-32522")
    monkeypatch.setenv(scheduled_scan.VAPI_AUTH_EXPIRES_ENV, "next monday")
    with pytest.raises(scheduled_scan.VapiAuthorizationError) as excinfo:
        scheduled_scan.run_scheduled_scan("vapi")
    assert "ISO-8601" in str(excinfo.value)


def test_current_authorization_proceeds_to_dispatch(monkeypatch):
    """A ref plus a future expiry hands control to the normal dispatch path."""
    monkeypatch.setenv(scheduled_scan.VAPI_AUTH_REF_ENV, "BEAAA-32522")
    monkeypatch.setenv(scheduled_scan.VAPI_AUTH_EXPIRES_ENV, _iso(timedelta(days=1)))

    reached = []

    def _marker(*args, **kwargs):
        reached.append(True)
        raise _Tripwire("dispatch path entered")

    monkeypatch.setattr(scheduled_scan, "map_secret_environment", _marker)
    with pytest.raises(_Tripwire):
        scheduled_scan.run_scheduled_scan("vapi")
    assert reached == [True]


def test_zoo_job_is_unaffected_by_the_gate(monkeypatch, tmp_path):
    """No gate values present, and the zoo lane still runs."""
    expected = tmp_path / "manifest.json"

    def _zoo(
        *, config=None, limit_per_family=None, zoo_family=None, zoo_case=None
    ):
        return expected

    monkeypatch.setattr(scheduled_scan, "run_scheduled_zoo_scan", _zoo)
    monkeypatch.setattr(
        scheduled_scan,
        "map_secret_environment",
        lambda *a, **k: (_ for _ in ()).throw(_Tripwire("zoo read Vapi secrets")),
    )
    assert scheduled_scan.run_scheduled_scan("zoo") == expected
    assert scheduled_scan.require_vapi_authorization("zoo") is None


def test_authorization_record_is_carried_into_the_manifest(monkeypatch):
    monkeypatch.setenv(scheduled_scan.VAPI_AUTH_REF_ENV, "BEAAA-32522#authz-2026-09")
    monkeypatch.setenv(scheduled_scan.VAPI_AUTH_EXPIRES_ENV, "2099-01-01T00:00:00Z")
    record = scheduled_scan.require_vapi_authorization("vapi")
    assert record["authorization_ref"] == "BEAAA-32522#authz-2026-09"
    assert record["authorization_expires_at"] == "2099-01-01T00:00:00+00:00"
    assert record["gate"] == "BEAAA-35702"


def test_cli_exits_non_zero_when_the_gate_is_closed(monkeypatch, capsys):
    def _refuse(*args, **kwargs):
        raise scheduled_scan.VapiAuthorizationError("gate closed, no traffic")

    monkeypatch.setattr(scheduled_scan, "run_scheduled_scan", _refuse)
    assert scheduled_scan.main(["--job", "vapi"]) != 0
    assert "gate closed, no traffic" in capsys.readouterr().err
