"""BEAAA-38253 — starter result rows survive the persisted-score envelope.

Production 856945f3 delivered the external envelope (current_kickoff_row has
score_json) without ``result_rows``; only the no-score fallback carried them.
These tests run with a nonempty score_json through GET projection and the
terminal webhook enqueue, then sign the enqueued payload.
"""

from __future__ import annotations

import hashlib
import hmac
import json

from api.ci_scan_ext import build_signed_delivery
from api.scan_handlers import ScanRuntime, _customer_result_response
from scanners.live_probe_orchestrator import ScanResult

ROWS = tuple(
    {
        "test_id": f"T{i:03d}",
        "family": "prompt_injection",
        "prompt": f"probe {i}",
        "reply": "no",
        "verdict": "passed",
    }
    for i in range(100)
)


class _Runtime:
    def __init__(self, outbox=None) -> None:
        self._webhook_outbox = outbox
        self._webhook_cfgs = {
            "scan-1": {"url": "https://intake.example/qa/v1/scanner-webhook", "secret_ref": "wh"}
        }

    def current_kickoff_row(self, scan_id):
        return {
            "score_json": {
                "are_total": 70,
                "band": "C",
                "subscores": {},
                "scanner_version": "0.4.1",
                "corpus_version": "0.2.0",
                "seed": 42,
                "model_versions": {},
                "evidence_refs": [],
                "confidence": 0.8,
                "uncovered_dimensions": [],
            }
        }


class _Outbox:
    def __init__(self) -> None:
        self.payloads = []

    def enqueue(self, *, idempotency_key, url, secret_ref, payload) -> None:
        self.payloads.append(payload)


def _result(rows=ROWS) -> ScanResult:
    return ScanResult(
        scan_id="scan-1",
        status="complete",
        scanner_version="0.4.1",
        corpus_version="0.2.0",
        rows_emitted=100,
        starter_result_rows=rows,
    )


def test_persisted_envelope_carries_opted_in_rows():
    out = _customer_result_response("scan-1", _result(), _Runtime())
    assert out["are_total"] == 70  # persisted-score branch, not the fallback
    assert len(out["result_rows"]) == 100
    assert {r["test_id"] for r in out["result_rows"]} == {r["test_id"] for r in ROWS}


def test_persisted_envelope_without_opt_in_is_summary_only():
    out = _customer_result_response("scan-1", _result(rows=()), _Runtime())
    assert out["are_total"] == 70
    assert "result_rows" not in out


def test_terminal_webhook_enqueues_signed_rows_on_persisted_branch():
    outbox = _Outbox()
    runtime = _Runtime(outbox)
    ScanRuntime._maybe_deliver_terminal_webhook(runtime, "scan-1", _result())

    assert len(outbox.payloads) == 1
    payload = outbox.payloads[0]
    assert payload["are_total"] == 70
    assert len(payload["result_rows"]) == 100

    headers, body = build_signed_delivery(payload, "wh", lambda ref: "s3cret")
    assert len(json.loads(body)["result_rows"]) == 100
    expected = hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
    assert any(expected in value for value in headers.values())
