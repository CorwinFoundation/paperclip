"""BEAAA-553 local-echo smoke for the production ``ScanRuntime`` wiring.

Verifies AC#3 of BEAAA-553: ``POST /scan`` against a throwaway local test
endpoint returns 202 with ``scan_id``; ``GET /scan/{scan_id}`` returns 200
with a valid v1.1.0 payload carrying ``incomplete: false``, ``dim_results``
non-empty, ``scanner_version``, ``corpus_version``, ``k_replicates``,
``live_probe_variance_band``, ``spread_per_dim``, ``pillar_spread``,
``total_spread``.

The "throwaway local test endpoint" is a stdlib ``http.server`` echo bound
to ``127.0.0.1:9999`` for the lifetime of the smoke. It is never actually
called by the synthetic probe walker (``probe_run_fn`` for v0 yields
synthetic rows without touching the network), but its existence makes the
scenario faithful to a customer agent endpoint: the API edge accepts the
hostname, the egress scope allows it, no traffic flows.

Run:

    cd projects/are-scanner
    PYTHONPATH=. python3 scripts/beaaa553_local_echo_smoke.py

Writes ``artifacts/beaaa-553/local_echo_smoke.json`` with the captured
``post`` + ``get`` payloads + summary.
"""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from api.app import create_app
from api.scan_runtime_factory import (
    build_v0_corpus_tarball,
    make_default_production_scan_runtime,
    v0_corpus_snapshot_hash,
)


ECHO_HOST = "127.0.0.1"
ECHO_PORT = 9999
ARTIFACT_PATH = Path(__file__).resolve().parents[1] / "artifacts" / "beaaa-553" / "local_echo_smoke.json"


class _EchoHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: ARG002
        return  # silence stdlib server's stderr noise

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length else b""
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        body = b'{"ok":true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _start_echo_server() -> tuple[ThreadingHTTPServer, threading.Thread]:
    server = ThreadingHTTPServer((ECHO_HOST, ECHO_PORT), _EchoHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _required_fields(payload: dict[str, Any]) -> dict[str, bool]:
    return {
        "scanner_version": "scanner_version" in payload,
        "corpus_version": "corpus_version" in payload,
        "k_replicates": "k_replicates" in payload,
        # ``live_probe_variance_band`` lands on the UW payload only (Δ4)
        "live_probe_variance_band_on_uw": True,  # filled below
        "spread_per_dim": "spread_per_dim" in payload,
        "pillar_spread": "pillar_spread" in payload,
        "total_spread": "total_spread" in payload,
        "incomplete_false": payload.get("incomplete") is False,
        "dim_results_non_empty": bool(payload.get("dim_results")),
    }


def run() -> dict[str, Any]:
    server, _thread = _start_echo_server()
    try:
        # Build a runtime explicitly so the smoke is self-contained and the
        # corpus hash is known to the request body without relying on the
        # env-var branch.
        runtime = make_default_production_scan_runtime()
        app = create_app(scan_runtime=runtime)
        client = TestClient(app)

        corpus_path = build_v0_corpus_tarball()
        corpus_hash = v0_corpus_snapshot_hash(corpus_path)

        body = {
            "tenant_id": "smoke-tenant",
            "agent_endpoint": {"host": ECHO_HOST},
            "credential_ref": "phase1-probe.v1",
            "credential_secret": "throwaway-token-do-not-use",
            "corpus_snapshot_hash": corpus_hash,
            "scanner_version": "0.3.0+T-CLOSE",
            "corpus_version": "0.1.0",
            "seed": 42,
            "scan_mode": "live_probe",
            "k_replicates": 5,
            "requested_pillars": ["GOVERNANCE", "ROBUSTNESS", "BIAS_FAIRNESS", "OPS"],
        }

        post_started = time.monotonic()
        post_resp = client.post(
            "/scan",
            json=body,
            headers={"X-Tenant-Id": "smoke-tenant"},
        )
        post_elapsed = time.monotonic() - post_started
        post_payload = post_resp.json()
        assert post_resp.status_code == 202, f"POST status={post_resp.status_code} body={post_payload!r}"
        scan_id = post_payload["scan_id"]

        # GET — customer-facing payload (Δ4 strips live_probe_variance_band).
        get_customer = client.get(f"/scan/{scan_id}")
        get_customer_payload = get_customer.json()
        assert get_customer.status_code == 200, (
            f"GET (customer) status={get_customer.status_code} body={get_customer_payload!r}"
        )

        # GET — UW payload retains live_probe_variance_band.
        get_uw = client.get(f"/scan/{scan_id}", params={"audience": "uw"})
        get_uw_payload = get_uw.json()
        assert get_uw.status_code == 200, (
            f"GET (uw) status={get_uw.status_code} body={get_uw_payload!r}"
        )

        checks = _required_fields(get_customer_payload)
        checks["live_probe_variance_band_on_uw"] = (
            get_uw_payload.get("live_probe_variance_band") == 2
        )
        checks["live_probe_variance_band_absent_from_customer"] = (
            "live_probe_variance_band" not in get_customer_payload
        )

        summary = {
            "scan_id": scan_id,
            "post_status": post_resp.status_code,
            "post_payload": post_payload,
            "post_elapsed_seconds": round(post_elapsed, 3),
            "get_customer_status": get_customer.status_code,
            "get_customer_payload": get_customer_payload,
            "get_uw_status": get_uw.status_code,
            "get_uw_payload": get_uw_payload,
            "required_field_checks": checks,
            "all_checks_passed": all(checks.values()),
            "corpus_snapshot_hash": corpus_hash,
            "agent_endpoint": f"https://{ECHO_HOST}",
            "echo_server_port": ECHO_PORT,
            "scanner_version": body["scanner_version"],
            "corpus_version": body["corpus_version"],
            "k_replicates": body["k_replicates"],
        }
        return summary
    finally:
        server.shutdown()
        server.server_close()


def main() -> int:
    summary = run()
    ARTIFACT_PATH.parent.mkdir(parents=True, exist_ok=True)
    ARTIFACT_PATH.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(f"wrote {ARTIFACT_PATH}")
    print(json.dumps(summary["required_field_checks"], indent=2, sort_keys=True))
    print(f"all_checks_passed={summary['all_checks_passed']}")
    return 0 if summary["all_checks_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
