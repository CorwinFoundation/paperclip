"""BEAAA-31617 — Non-2xx customer responses carry diagnostics in incomplete_reason.

Verifies:
- status line, allow-listed headers, and bounded body appear in the reason string
- credentials (bearer token, UUID) are scrubbed from body and header values
- _incomplete_group() bucket is unchanged (status-line prefix still matches)
- non-CustomerEndpointHTTPError exceptions are unaffected
"""
from tools._exception_scrubber import garak_exception_reason as _garak_exception_reason
from tools._exception_scrubber import http_error_diagnostics as _http_error_diagnostics


# ---------------------------------------------------------------------------
# A minimal CustomerEndpointHTTPError-shaped exception for unit tests.
# The real class lives in harness/customer_runner.py; we replicate its
# attribute surface without importing the full harness.
# ---------------------------------------------------------------------------

class _FakeHTTPError(Exception):
    def __init__(self, status_code: int, reason_phrase: str, body: str, headers: dict) -> None:
        super().__init__(f"customer endpoint HTTP {status_code}: {reason_phrase}")
        self.status_code = status_code
        self.reason_phrase = reason_phrase
        self.body = body
        self.headers = headers


SUBJECT: dict = {"secret": "sk-secret-token", "agent_id": ""}


class TestHttpErrorDiagnostics:
    def _cloudflare_exc(self, body: str = "<html>Cloudflare block</html>") -> _FakeHTTPError:
        return _FakeHTTPError(
            403,
            "Forbidden",
            body,
            {
                "server": "cloudflare",
                "cf-ray": "abc123-LHR",
                "content-type": "text/html; charset=UTF-8",
                "x-custom-secret": "should-not-appear",
            },
        )

    def test_status_line_present(self) -> None:
        reason = _garak_exception_reason("", self._cloudflare_exc(), SUBJECT)
        assert "status=403 Forbidden" in reason

    def test_allowlisted_headers_present(self) -> None:
        reason = _garak_exception_reason("", self._cloudflare_exc(), SUBJECT)
        assert "server=cloudflare" in reason
        assert "cf-ray=abc123-LHR" in reason
        assert "content-type=text/html" in reason

    def test_non_allowlisted_header_absent(self) -> None:
        reason = _garak_exception_reason("", self._cloudflare_exc(), SUBJECT)
        assert "x-custom-secret" not in reason
        assert "should-not-appear" not in reason

    def test_body_prefix_present(self) -> None:
        reason = _garak_exception_reason("", self._cloudflare_exc(), SUBJECT)
        assert "Cloudflare block" in reason

    def test_body_truncated_at_cap(self) -> None:
        long_body = "X" * 500
        exc = _FakeHTTPError(403, "Forbidden", long_body, {})
        diag = _http_error_diagnostics(exc, SUBJECT)
        # body value in diag should be capped at 200 chars
        assert len(diag) < 500

    def test_bearer_token_scrubbed_from_body(self) -> None:
        exc = _FakeHTTPError(403, "Forbidden", "Bearer sk-secret-token was rejected", {})
        reason = _garak_exception_reason("", exc, SUBJECT)
        assert "sk-secret-token" not in reason
        assert "[REDACTED]" in reason

    def test_uuid_scrubbed_from_body(self) -> None:
        exc = _FakeHTTPError(403, "Forbidden", "agent 12345678-1234-1234-8000-123456789abc denied", {})
        reason = _garak_exception_reason("", exc, SUBJECT)
        assert "12345678-1234-1234-8000-123456789abc" not in reason

    def test_secret_scrubbed_from_header_value(self) -> None:
        exc = _FakeHTTPError(
            403, "Forbidden", "",
            {"server": "sk-secret-token-proxy"},
        )
        reason = _garak_exception_reason("", exc, {"secret": "sk-secret-token", "agent_id": ""})
        assert "sk-secret-token" not in reason

    def test_incomplete_group_prefix_unchanged(self) -> None:
        """The base class name must remain so _incomplete_group() buckets correctly."""
        exc = self._cloudflare_exc()
        reason = _garak_exception_reason("", exc, SUBJECT)
        # Base is the exception class name; diagnostics are appended in brackets
        assert reason.startswith("_FakeHTTPError")

    def test_non_http_exception_unaffected(self) -> None:
        exc = ValueError("some garak probe error")
        reason = _garak_exception_reason("native_probe_interface", exc, SUBJECT)
        assert "[" not in reason
        assert "status=" not in reason
        assert reason.startswith("native_probe_interface:ValueError")

    def test_empty_body_no_body_field(self) -> None:
        exc = _FakeHTTPError(502, "Bad Gateway", "", {"via": "1.1 proxy"})
        reason = _garak_exception_reason("", exc, SUBJECT)
        assert "via=1.1 proxy" in reason
        assert "body=" not in reason
