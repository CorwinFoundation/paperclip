"""BEAAA-42388: an ``auth: none`` scan must not fail on its empty credential.

The scan API accepts ``auth: {type: none}`` with an empty
``credential_secret`` (and restart recovery re-queues exactly those scans),
but the runtime used to call ``vault.register(ref, "")``, which raises
``CredentialError``. Live, every credential-free scan failed with
``scan_worker_error:CredentialError`` and 0 tests (scan 42567721, 2026-10-20).
"""
from __future__ import annotations

import pytest

from harness.credentials import CredentialError, CredentialVault
from scanners.live_probe_orchestrator import (
    CredentialResolveError,
    endpoint_requires_no_credential,
    register_scan_credential,
    resolve_scan_secret,
)

NO_AUTH = {"url": "https://agent.example/v1/chat/completions", "auth": {"type": "none"}}
BEARER = {"url": "https://agent.example/v1/chat/completions", "auth": {"type": "bearer"}}


@pytest.mark.parametrize("config, expected", [
    (NO_AUTH, True),
    (BEARER, False),
    ({"url": "https://x"}, False),
    (None, False),
    ({"auth": "none"}, False),
])
def test_only_an_explicit_auth_none_endpoint_is_credential_free(config, expected):
    assert endpoint_requires_no_credential(config) is expected


def test_auth_none_with_empty_secret_registers_nothing_and_resolves_empty():
    vault = CredentialVault()
    register_scan_credential(vault, "qa-38869-restart-fixture", "", NO_AUTH)
    assert vault.resolve("qa-38869-restart-fixture") is None
    assert resolve_scan_secret(vault, "qa-38869-restart-fixture", NO_AUTH) == ""


def test_credentialed_endpoint_with_empty_secret_still_fails_closed():
    vault = CredentialVault()
    with pytest.raises(CredentialError):
        register_scan_credential(vault, "cust-ref", "", BEARER)
    with pytest.raises(CredentialResolveError):
        resolve_scan_secret(vault, "cust-ref", BEARER)


def test_real_secret_is_registered_and_resolved_for_any_auth_type():
    for config in (BEARER, NO_AUTH):
        vault = CredentialVault()
        register_scan_credential(vault, "cust-ref", "s3cr3t-value", config)
        assert resolve_scan_secret(vault, "cust-ref", config) == "s3cr3t-value"
