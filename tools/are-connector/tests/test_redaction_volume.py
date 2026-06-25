"""BEAAA-5751 R-3: redact under concurrent stream load.

rubric_point_id: ROBUSTNESS.REDACTION_VOLUME
Leakage classes tested (canon SECRET_PATTERNS + PII_PATTERNS):
  - aws_key: AKIA[0-9A-Z]{16} (AWS access key ID)
  - email, phone (PII_PATTERNS)

Scenario source:
  - OS-Harm data-exfiltration family (upstream dataset pin: v0.2)
  - AgentDojo exfil paths (upstream version: v0.1.35,
    git tag: v0.1.35, commit: a75aba7631d3ca5fb7ab938965c97ead2f9ff84b)

Pins: scanner_version=0.4.1, corpus_version=0.2.0
"""
from __future__ import annotations

import json
import random
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from are_connector.http import EgressAllowlist, call_agent
from are_connector.redaction import PII_PATTERNS, SECRET_PATTERNS
from are_connector.telemetry import RedactionPolicy, redact_agent_output

RUBRIC_POINT_ID = "ROBUSTNESS.REDACTION_VOLUME"
SCANNER_VERSION = "0.4.1"
CORPUS_VERSION = "0.2.0"

# Known-bad: triggers aws_key (AKIA pattern) + email + phone
LEAKY_RESPONSE = (
    "access_key=AKIAIOSFODNN7EXAMPLE "
    "user=qa-tester@example.net "
    "phone=+1-303-555-0199"
)
CLEAN_RESPONSE = "disclaimer: no pii class in this payload."


def _server_body(is_bad: bool) -> bytes:
    text = LEAKY_RESPONSE if is_bad else CLEAN_RESPONSE
    payload = json.dumps({"response": text}, separators=(",", ":")).encode("utf-8")
    return payload


def _start_stream_server(body: bytes) -> tuple[ThreadingHTTPServer, threading.Thread]:
    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, *_a: object) -> None:
            return

        def do_POST(self) -> None:  # noqa: N802 — stdlib API
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _build_patterns() -> dict[str, re.Pattern[str]]:
    pii_by_class = {name: pattern for name, pattern in PII_PATTERNS}
    # AWS access key ID (AKIA prefix) — always present in canon SECRET_PATTERNS
    aws_key = next(
        (p for p in SECRET_PATTERNS if "AKIA" in p.pattern),
        None,
    )
    assert aws_key is not None, "AKIA access-key pattern not found in SECRET_PATTERNS"
    email = pii_by_class["email"]
    phone = pii_by_class["phone"]
    return {"aws_key": aws_key, "email": email, "phone": phone}


_LEAK_PATTERNS = _build_patterns()
_POLICY = RedactionPolicy.trusted_for_tests()


def _stream_output(
    endpoint: str,
    results: list[object],
    *,
    allow_bad: bool,
) -> None:
    try:
        allowlist = EgressAllowlist(endpoint)
        result = call_agent(endpoint, {"ping": 1}, allowlist, timeout=5)
        raw_text = str(result.get("response", ""))
        redacted, _ = redact_agent_output(raw_text, _POLICY)
        leaks = {name: bool(p.search(raw_text)) for name, p in _LEAK_PATTERNS.items()}
        if allow_bad:
            assert all(leaks.values()), (
                f"known-bad fixture must expose all leakage classes; got {leaks!r}"
            )
        else:
            assert not any(leaks.values()), (
                f"known-good fixture must not contain leakage classes; got {leaks!r}"
            )
        # After redaction, no class should survive (all are supported in canon).
        redacted_leaks = {name: bool(p.search(redacted)) for name, p in _LEAK_PATTERNS.items()}
        assert not any(redacted_leaks.values()), (
            f"leak detected after redaction: {redacted_leaks!r}"
        )
        results.append(None)
    except Exception as exc:
        results.append(exc)


@pytest.mark.parametrize(
    ("stream_count", "allow_bad"),
    [
        (1, False),
        (1, True),
        (5, False),
        (5, True),
        (20, False),
        (20, True),
    ],
    ids=["n1-clean", "n1-bad", "n5-clean", "n5-bad", "n20-clean", "n20-bad"],
)
def test_redaction_holds_under_n_concurrent_streams(
    monkeypatch: pytest.MonkeyPatch,
    stream_count: int,
    allow_bad: bool,
) -> None:
    """Known-good and known-bad fixtures at N=1/5/20 concurrent streams.

    Known-good: clean response — no leakage class present before or after redaction.
    Known-bad: leaky response — all leakage classes present before redaction,
    none survive after redaction.
    """
    monkeypatch.setenv("TEMP", "0")
    monkeypatch.setenv("ALLOW_PLAINTEXT_AGENT_ENDPOINT", "1")
    random.seed(0)

    server, thread = _start_stream_server(_server_body(is_bad=allow_bad))
    try:
        endpoint = f"http://127.0.0.1:{server.server_port}"
        results: list[object] = []
        threads = [
            threading.Thread(
                target=_stream_output,
                args=(endpoint, results),
                kwargs={"allow_bad": allow_bad},
            )
            for _ in range(stream_count)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(results) == stream_count
        assert all(item is None for item in results), results
    finally:
        server.shutdown()
        thread.join(timeout=1)
        server.server_close()
