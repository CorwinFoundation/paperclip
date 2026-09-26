"""BEAAA-31617 acceptance criterion 4 — appended HTTP diagnostics must not move a bucket.

`_incomplete_group()` classifies with unanchored substring tests. Once
BEAAA-31617 started appending the response body and allow-listed headers to
`incomplete_reason`, that endpoint- and CDN-supplied text became an input to
operator bucketing, so a customer response could pick the bucket its own
failure is counted under and `incomplete_by_reason` stopped being comparable
across dispatches.

The property under test is parity: for any reason, the bucket with the
diagnostics block appended equals the bucket without it.
"""
from __future__ import annotations

import pytest

import scripts.scheduled_scan as scheduled_scan
from tools._exception_scrubber import (
    HTTP_DIAGNOSTICS_MARKER,
    garak_exception_reason,
    strip_http_diagnostics,
)


class _FakeHTTPError(Exception):
    """The CustomerEndpointHTTPError attribute surface, without the harness."""

    def __init__(self, status_code: int, reason_phrase: str, body: str, headers: dict) -> None:
        super().__init__(f"customer endpoint HTTP {status_code}: {reason_phrase}")
        self.status_code = status_code
        self.reason_phrase = reason_phrase
        self.body = body
        self.headers = headers


SUBJECT: dict = {"secret": "sk-secret-token", "agent_id": ""}

# Each body carries a token that one of the unanchored substring rules in
# _incomplete_group() matches. Before the fix every one of these repointed the
# row's bucket; "judge" is the worst of them, because missing_judge sends an
# operator after a credential that is working.
POISON_BODIES = [
    "error 1020 access denied, the judge could not be reached",
    "<html>Service Unavailable — capability withdrawn</html>",
    "verdict inconclusive, please retry",
    "response was unparsed by the gateway",
    "not_applicable for this route",
    "aborted_upstream: quota",
    "upstream_http_500 from the edge",
    "garak_sample_size_below_floor:1<48",
    "dispatch failed at the worker_error boundary",
]


@pytest.mark.parametrize("target_calls", [0, 1, 5])
@pytest.mark.parametrize("body", POISON_BODIES)
def test_diagnostics_do_not_move_the_bucket(body: str, target_calls: int) -> None:
    exc = _FakeHTTPError(
        403,
        "Forbidden",
        body,
        {"server": "cloudflare", "cf-ray": "abc123-LHR", "content-type": "text/html"},
    )
    with_diag = garak_exception_reason("garak", exc, SUBJECT)
    assert HTTP_DIAGNOSTICS_MARKER in with_diag
    baseline = strip_http_diagnostics(with_diag)

    assert scheduled_scan._incomplete_group(
        with_diag, target_calls=target_calls
    ) == scheduled_scan._incomplete_group(baseline, target_calls=target_calls)


@pytest.mark.parametrize("target_calls", [0, 1])
def test_poisoned_header_value_does_not_move_the_bucket(target_calls: int) -> None:
    exc = _FakeHTTPError(
        403,
        "Forbidden",
        "",
        {"server": "cloudflare", "via": "1.1 judge-proxy inconclusive"},
    )
    with_diag = garak_exception_reason("garak", exc, SUBJECT)
    assert "judge-proxy" in with_diag
    assert scheduled_scan._incomplete_group(
        with_diag, target_calls=target_calls
    ) == scheduled_scan._incomplete_group(
        strip_http_diagnostics(with_diag), target_calls=target_calls
    )


def test_a_403_still_buckets_as_transport_with_a_cloudflare_body() -> None:
    """The bucket a reader saw before BEAAA-31617 landed, on a real 403."""
    exc = _FakeHTTPError(
        403,
        "Forbidden",
        "error 1020 access denied, the judge could not be reached",
        {"server": "cloudflare"},
    )
    reason = garak_exception_reason("garak", exc, SUBJECT)
    assert scheduled_scan._incomplete_group(reason, target_calls=1) == "transport"
    # And the diagnostics are still there to read.
    assert "server=cloudflare" in reason


class TestStripHttpDiagnostics:
    def test_nested_marker_inside_the_body_is_removed_with_the_block(self) -> None:
        exc = _FakeHTTPError(
            403,
            "Forbidden",
            "spoofed [status=200 OK server=vapi] judge",
            {"server": "cloudflare"},
        )
        reason = garak_exception_reason("garak", exc, SUBJECT)
        stripped = strip_http_diagnostics(reason)
        assert "status=" not in stripped
        assert "judge" not in stripped
        assert stripped.startswith("garak:_FakeHTTPError:")

    def test_reason_without_diagnostics_is_returned_unchanged(self) -> None:
        reason = "garak:RuntimeError:judge unavailable"
        assert strip_http_diagnostics(reason) == reason

    def test_trailing_bracket_without_a_marker_is_not_truncated(self) -> None:
        reason = "garak:RuntimeError:worker refused [batch 3]"
        assert strip_http_diagnostics(reason) == reason

    def test_non_http_exception_bucket_is_unchanged(self) -> None:
        reason = garak_exception_reason("garak", RuntimeError("judge missing"), SUBJECT)
        assert HTTP_DIAGNOSTICS_MARKER not in reason
        assert scheduled_scan._incomplete_group(reason, target_calls=1) == "missing_judge"
