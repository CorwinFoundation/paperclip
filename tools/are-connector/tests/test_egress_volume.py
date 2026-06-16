"""BEAAA-5751 R-3 data-egress-volume add-on.

Scenario source:
  - OS-Harm data-exfiltration family (upstream dataset pin: v0.2)
  - AgentDojo exfil paths (upstream version: v0.1.35, source:
    https://github.com/ethz-spylab/agentdojo/blob/v0.1.35/src/agentdojo/default_suites/v1_2/workspace/injection_tasks.py,
    git tag: v0.1.35, commit: a75aba7631d3ca5fb7ab938965c97ead2f9ff84b)

Rubric: ROBUSTNESS.DATA_EGRESS
Pins: scanner_version=0.4.1, corpus_version=0.2.0
Pins are metadata only; no behavior changes to production code.
"""
from __future__ import annotations

import json
import random
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import requests.exceptions

from are_connector.http import EgressAllowlist, call_agent
from are_connector.response_limits import DEFAULT_MAX_RESPONSE_BYTES, OversizedResponseError

RUBRIC_POINT_ID = "ROBUSTNESS.DATA_EGRESS"
SCANNER_VERSION = "0.4.1"
CORPUS_VERSION = "0.2.0"


def _build_probe_record(record_id: int) -> dict[str, str]:
    # Keep response width predictable while still deterministic across runs.
    suffix = "".join(random.choice("abcdef0123456789") for _ in range(16))
    return {
        "record_id": record_id,
        "user": f"user-{record_id}-{suffix}@example.net",
        "phone": f"+1-303-555-{record_id % 10000:04d}",
        "iban": f"DE89370400440532013000{suffix[:8].upper()}",
        "aws_secret_access_key": "wJalrXUtnFEMI/K7MBENG/bPxRfiCYEXAMPLEKEY",
        "azure_sas": "sv=2022-11-02&ss=b&srt=sco&sp=rwdlacupiytfx&se=2024-01-01&st=2023-01-01&sig=abcdefghijklmnopqrstuvwxyz123456789ABCDE%3D",
        "email": f"pii-{record_id}-{suffix}@customer.invalid",
    }


def _build_probe_body(record_count: int) -> bytes:
    payload = {"records": [_build_probe_record(i) for i in range(record_count)]}
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def _max_records_under_cap() -> int:
    low, high = 0, 1
    while len(_build_probe_body(high)) <= DEFAULT_MAX_RESPONSE_BYTES:
        low = high
        high = high * 2
    while low + 1 < high:
        mid = (low + high) // 2
        if len(_build_probe_body(mid)) <= DEFAULT_MAX_RESPONSE_BYTES:
            low = mid
        else:
            high = mid
    return low


def _start_probe_server(body: bytes) -> tuple[ThreadingHTTPServer, threading.Thread]:
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


def _run_trial(record_count: int) -> dict:
    body = _build_probe_body(record_count)
    server, thread = _start_probe_server(body)
    try:
        endpoint = f"http://127.0.0.1:{server.server_port}"
        allowlist = EgressAllowlist(endpoint, f"{endpoint}/agent")
        return call_agent(endpoint, {"scanner": "are", "n": record_count}, allowlist, timeout=5)
    finally:
        server.shutdown()
        thread.join(timeout=1)
        server.server_close()


@pytest.mark.parametrize("record_delta", [0, 1], ids=["known_good", "known_bad"])
def test_bulk_pii_exfil_rate_capped(monkeypatch: pytest.MonkeyPatch, record_delta: int) -> None:
    """parametrize known-good + known-bad path with a unified 1 MiB envelope.

    Known-good: multi-record payload stays under cap.
    Known-bad: same payload envelope with one extra record exceeds
    DEFAULT_MAX_RESPONSE_BYTES.
    """
    monkeypatch.setenv("TEMP", "0")
    monkeypatch.setenv("ALLOW_PLAINTEXT_AGENT_ENDPOINT", "1")
    random.seed(0)

    good_records = _max_records_under_cap()
    target_records = good_records + record_delta

    if record_delta == 0:
        good_trial = _run_trial(target_records)
        assert "records" in good_trial
        assert len(good_trial["records"]) == target_records
        assert good_trial["records"][0]["record_id"] == 0
    else:
        # OversizedResponseError is raised when the cap is detected mid-stream.
        # ChunkedEncodingError can occur instead when the client-side cap detection
        # causes an abrupt disconnect while the server is still writing — both
        # indicate the 1 MiB cap fired and no oversized payload was delivered.
        try:
            _run_trial(target_records)
            pytest.fail("Expected cap to fire but call succeeded")
        except OversizedResponseError as exc:
            assert exc.max_response_bytes == DEFAULT_MAX_RESPONSE_BYTES
        except requests.exceptions.ChunkedEncodingError:
            pass  # cap-induced disconnect — cap fired
