"""Integration-suite test seam for loopback-bound customer endpoints.

BEAAA-11672: BEAAA-11611 ("harden agent endpoint egress scope") removed the
IP-literal exemption in ``harness.egress._AllowlistTransport._enforce_resolved_address``
so that an agent endpoint declared as a loopback IP literal is now rejected by
``_is_forbidden_resolved_ip`` (loopback is a forbidden resolved range). That is
the correct *production* behaviour — the Prober must never pivot into its own
loopback surface.

The customer-runner integration tests, however, stand up real short-lived HTTP
servers on ``127.0.0.1`` / ``127.0.0.2`` and drive the *actual* runner against
them to exercise compression-bomb caps, streaming size caps, redirect
rejection, and the walker round trip. The runner builds its own internal
``ScopedEgressAllowlist`` with no injection point, so those tests cannot reach
their own fixtures once loopback literals are forbidden.

This autouse fixture patches ONLY the test process: it permits loopback
addresses (127.0.0.0/8 and ::1) through ``_is_forbidden_resolved_ip`` while
delegating every other address (private, link-local, multicast, unspecified,
reserved) to the unmodified guard. No production code is changed; the egress
hardening ships intact. The SSRF semantics the egress test actually asserts —
outright 3xx redirect rejection, sentinel-never-reached — are unaffected, since
redirects are still never followed.
"""
from __future__ import annotations

import ipaddress

import pytest

from harness import egress


def _permits_loopback(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return ip.is_loopback


@pytest.fixture(autouse=True)
def _allow_loopback_test_endpoints(monkeypatch: pytest.MonkeyPatch) -> None:
    original = egress._is_forbidden_resolved_ip

    def _guard(address: str) -> bool:
        if _permits_loopback(address):
            return False
        return original(address)

    monkeypatch.setattr(egress, "_is_forbidden_resolved_ip", _guard)
