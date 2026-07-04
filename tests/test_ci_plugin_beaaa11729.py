"""BEAAA-11729 — OSS CI/CD plugin acceptance matrix.

The single acceptance criterion: this matrix is green against a mock scanner
harness, proving every exit-code path of the frozen WS-5 design.

======================================================  ====
row                                                     exit
======================================================  ====
1. score < fail_below_score → break build                 1
2. score >= threshold → pass                               0
3. infra error, retries exhausted → advisory (no red)     75
4. webhook HMAC mismatch → fail-closed                      4
5. endpoint self-identifies production (§6) → refuse        3
6. smoke tier below threshold → non-zero, NON-bindable      1
======================================================  ====

The mock transport is the plugin-facing scanner double: it speaks the
shipped request/response contract (``POST /scanner/v1/scans``,
``GET /scan/{id}``, WS-2 ``/.well-known/agent`` preflight) and can be driven
into each failure mode deterministically — no network, no real sleeps.
"""
from __future__ import annotations

import json

import pytest

from api.ci_scan_ext import sign_payload
from ci_plugin.client import Response, ScanTransportError
from ci_plugin.gate import (
    EXIT_GATE_FAIL,
    EXIT_HMAC_MISMATCH,
    EXIT_INFRA_ADVISORY,
    EXIT_PASS,
    EXIT_PRODUCTION_REFUSED,
    resolve_threshold,
)
from ci_plugin.runner import Inputs, run

NO_SLEEP = lambda _s: None  # noqa: E731 - deterministic, no wall-clock waits
SINK = lambda _m: None       # noqa: E731 - swallow logs in tests


class MockScanner:
    """In-process double for the shipped scanner routes + WS-2 preflight."""

    def __init__(
        self, *, environment="staging", score=None, band=None,
        policy_band_floor=None, scan_tier="full", scan_id="scan-abc123",
        fail_preflight=0, fail_submit=0, fail_poll=0, infra_kind="unreachable",
    ):
        self.environment = environment
        self.score = score
        self.band = band
        self.policy_band_floor = policy_band_floor
        self.scan_tier = scan_tier
        self.scan_id = scan_id
        self.fail_preflight = fail_preflight
        self.fail_submit = fail_submit
        self.fail_poll = fail_poll
        self.infra_kind = infra_kind
        self.calls = {"preflight": 0, "submit": 0, "poll": 0}

    def request(self, method, url, *, headers, body=None):
        if url.endswith("/.well-known/agent"):
            self.calls["preflight"] += 1
            if self.calls["preflight"] <= self.fail_preflight:
                raise ScanTransportError(self.infra_kind, "preflight down")
            return _json(200, {"environment": self.environment})
        if url.endswith("/scanner/v1/scans"):
            self.calls["submit"] += 1
            if self.calls["submit"] <= self.fail_submit:
                raise ScanTransportError(self.infra_kind, "submit down")
            return _json(202, {"scan_id": self.scan_id, "status": "accepted"})
        if "/scan/" in url:
            self.calls["poll"] += 1
            if self.calls["poll"] <= self.fail_poll:
                raise ScanTransportError(self.infra_kind, "poll down")
            env = {
                "scan_id": self.scan_id, "run_timestamp": "2026-07-04T00:00:00Z",
                "are_total": self.score, "band": self.band,
                "scan_tier": self.scan_tier,
            }
            if self.policy_band_floor is not None:
                env["policy_band_floor"] = self.policy_band_floor
            return _json(200, env)
        raise AssertionError(f"unexpected url {url}")


def _json(status, obj):
    return Response(status=status, body=json.dumps(obj).encode("utf-8"))


def _inputs(**kw):
    base = dict(
        agent_endpoint="https://agent.example.com",
        backbond_api_key="bb-key-SECRET",
        backbond_base_url="https://api.backbond.test",
        auth_token="agent-auth-SECRET",
    )
    base.update(kw)
    return Inputs(**base)


def _run(scanner, inputs):
    return run(inputs, transport=scanner, sleep=NO_SLEEP, log=SINK)


# ---------------------------------------------------------------------------
# The acceptance matrix.
# ---------------------------------------------------------------------------
def test_row1_score_below_fail_below_score_breaks_build():
    scanner = MockScanner(score=60.0, band="C")
    d = _run(scanner, _inputs(fail_below_score=72.0))
    assert d.exit_code == EXIT_GATE_FAIL
    assert d.exit_code != 0  # non-zero: break the build
    assert d.score == 60.0 and d.threshold == 72.0


def test_row2_score_at_or_above_threshold_passes():
    scanner = MockScanner(score=90.0, band="A")
    d = _run(scanner, _inputs(fail_below_score=72.0))
    assert d.exit_code == EXIT_PASS


def test_row3_infra_error_exhausted_is_neutral_advisory():
    # Submit always fails with a transport error → retries exhausted → 75.
    scanner = MockScanner(fail_submit=99)
    d = _run(scanner, _inputs(fail_below_score=72.0, max_infra_retries=2))
    assert d.exit_code == EXIT_INFRA_ADVISORY
    assert d.advisory is True
    assert d.exit_code != EXIT_GATE_FAIL  # a BackBond outage does NOT red the build
    assert scanner.calls["submit"] == 3  # initial + 2 retries


def test_row3b_infra_error_recovers_within_retries():
    # Two transient failures then success → real score gate, no advisory.
    scanner = MockScanner(score=90.0, band="A", fail_submit=2)
    d = _run(scanner, _inputs(fail_below_score=72.0, max_infra_retries=3))
    assert d.exit_code == EXIT_PASS
    assert scanner.calls["submit"] == 3


def test_row4_webhook_hmac_mismatch_fails_closed():
    scanner = MockScanner(score=95.0, band="A")  # score would PASS if trusted
    body = {"scan_id": "scan-abc123", "status": "accepted"}
    tampered = sign_payload("WRONG-secret", body)  # signed with the wrong key
    d = _run(scanner, _inputs(
        fail_below_score=72.0, webhook_secret="right-secret-SECRET",
        webhook_body=body, webhook_signature=tampered,
    ))
    assert d.exit_code == EXIT_HMAC_MISMATCH
    assert d.exit_code != EXIT_PASS  # never trusted the score


def test_row4b_webhook_valid_signature_is_trusted():
    scanner = MockScanner(score=95.0, band="A")
    body = {"scan_id": "scan-abc123", "status": "accepted"}
    good = sign_payload("right-secret-SECRET", body)
    d = _run(scanner, _inputs(
        fail_below_score=72.0, webhook_secret="right-secret-SECRET",
        webhook_body=body, webhook_signature=good,
    ))
    assert d.exit_code == EXIT_PASS


def test_row5_production_self_id_is_refused():
    scanner = MockScanner(environment="production", score=95.0, band="A")
    d = _run(scanner, _inputs(fail_below_score=72.0))
    assert d.exit_code == EXIT_PRODUCTION_REFUSED
    assert scanner.calls["submit"] == 0  # refused BEFORE any scan submitted


def test_row6_smoke_tier_below_threshold_is_nonbindable():
    # No override; gate uses the vendor policy band floor (API-lookup source).
    scanner = MockScanner(score=50.0, band="D", scan_tier="smoke",
                          policy_band_floor=70.0)
    d = _run(scanner, _inputs(scan_tier="smoke"))
    assert d.exit_code == EXIT_GATE_FAIL      # non-zero
    assert d.bindable is False                # NON-bindable, never feeds UW pricing
    assert d.advisory is True


# ---------------------------------------------------------------------------
# Supporting unit tests.
# ---------------------------------------------------------------------------
def test_threshold_override_is_stricter_only():
    # Override higher than policy → override wins (tighter bar).
    assert resolve_threshold(80.0, 70.0) == 80.0
    # Override LOWER than policy → policy floor holds (cannot loosen).
    assert resolve_threshold(60.0, 70.0) == 70.0
    # Only one source present.
    assert resolve_threshold(None, 70.0) == 70.0
    assert resolve_threshold(80.0, None) == 80.0
    assert resolve_threshold(None, None) is None


def test_policy_band_floor_derived_from_band_letter_when_no_numeric():
    # Vendor contracted band 'B' → floor 70; score 68 fails.
    scanner = MockScanner(score=68.0, band="C")
    # Inject a contracted policy band letter (no numeric floor).
    def transport(method, url, *, headers, body=None):
        resp = scanner.request(method, url, headers=headers, body=body)
        if "/scan/" in url and not url.endswith("/.well-known/agent"):
            obj = resp.json()
            obj["policy_band"] = "B"
            return _json(200, obj)
        return resp

    class T:
        request = staticmethod(transport)

    d = run(_inputs(), transport=T(), sleep=NO_SLEEP, log=SINK)
    assert d.threshold == 70.0
    assert d.exit_code == EXIT_GATE_FAIL


def test_secrets_never_logged():
    scanner = MockScanner(score=90.0, band="A")
    logs: list[str] = []
    inp = _inputs(fail_below_score=72.0)
    run(inp, transport=scanner, sleep=NO_SLEEP, log=logs.append)
    joined = "\n".join(logs)
    assert inp.backbond_api_key not in joined
    assert (inp.auth_token or "x") not in joined
    assert "SECRET" not in joined  # our test secrets all carry this marker


def test_un_gateable_scan_is_advisory_not_a_pass():
    # No override and no policy floor → cannot gate → advisory, never a pass.
    scanner = MockScanner(score=90.0, band="A")  # no policy_band_floor
    d = _run(scanner, _inputs())
    assert d.exit_code == EXIT_INFRA_ADVISORY
    assert d.exit_code != EXIT_PASS


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
