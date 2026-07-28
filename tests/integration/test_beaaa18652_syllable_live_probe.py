"""BEAAA-18652: retained Syllable transport on the current live-probe path.

Rule-execution under scanner-implementation-supervision-v1@v1.1. This test
does not activate a rubric point or treat the historical Path B score as a
current BackBond Score.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Iterator

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.bundle import BundleStore, FileBundleStorage
from api.scan_log import ScanLog
from api.scan_runtime_factory import (
    _production_score_envelope_factory,
    build_v0_corpus_tarball,
    make_production_scan_runtime,
    v0_corpus_snapshot_hash,
)
from harness.customer_runner import _SyllableCustomerAgentRunner
from harness.runner import SyllableRunner
from scanners.customer_agent_walker import customer_agent_probe_run_fn
from scanners.score_runtime import ScoreRuntime


ROOT = Path(__file__).resolve().parents[2]
SCANNER_VERSION = "0.5.0"
CORPUS_VERSION = "0.1.0"
SEED = 42


class _ShowcaseHandler(BaseHTTPRequestHandler):
    calls = 0

    def do_POST(self) -> None:  # noqa: N802
        type(self).calls += 1
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length) or b"{}")
        if self.path.endswith("/start_session"):
            body = {"id": f"session-{type(self).calls}"}
        elif self.path.endswith("/send_message") and payload.get("session_id"):
            body = {
                "message": {
                    "message": (
                        "I cannot fulfill that request. Contact fixture@example.com "
                        "or 415-555-0100."
                    )
                }
            }
        else:
            self.send_error(404)
            return
        raw = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *_args: Any) -> None:
        return


@pytest.fixture
def showcase() -> Iterator[str]:
    _ShowcaseHandler.calls = 0
    server = HTTPServer(("127.0.0.1", 0), _ShowcaseHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _runner(base_url: str) -> _SyllableCustomerAgentRunner:
    runner = _SyllableCustomerAgentRunner(
        base_url=base_url,
        client_id_key="public-showcase-fixture",
        host_key="https://syllable.ai",
    )
    assert isinstance(runner, SyllableRunner)
    return runner


def test_syllable_smoke_then_seeded_n5_current_route(
    tmp_path: Path, showcase: str
) -> None:
    # Bounded, one-case, explicitly non-scoring transport smoke.
    smoke = _runner(showcase)
    assert smoke.complete("transport smoke only")
    assert _ShowcaseHandler.calls == 2

    corpus_path = build_v0_corpus_tarball(tmp_path / "corpus")
    corpus_hash = v0_corpus_snapshot_hash(corpus_path)
    envelopes: dict[str, dict[str, Any]] = {}
    runtime = make_production_scan_runtime(
        corpus_tarball_path=corpus_path,
        runner_factory=lambda _endpoint, _secret: _runner(showcase),
        probe_run_fn=customer_agent_probe_run_fn,
        score_runtime=ScoreRuntime.from_rubric_path(ROOT / "rubric" / "rubric.yaml"),
        score_envelope_registry=envelopes,
    )
    runtime._scan_log = ScanLog(str(tmp_path / "scan-log.sqlite3"))
    runtime._score_envelope_factory = _production_score_envelope_factory(
        [runtime], envelopes
    )
    bundle_dir = tmp_path / "bundles"
    bundles = BundleStore(FileBundleStorage(bundle_dir))
    client = TestClient(create_app(scan_runtime=runtime, bundles=bundles))

    results = []
    for index in range(5):
        agent_id = f"syllable-showcase-{index}"
        runtime.register_prior_scan(agent_id)
        response = client.post(
            "/scanner/v1/rescans",
            headers={"X-Tenant-Id": "tenant-beaaa18652"},
            json={
                "tenant_id": "tenant-beaaa18652",
                "agent_id": agent_id,
                "agent_endpoint": {"host": "apichat.syllable.cloud"},
                "credential_ref": "public-showcase",
                "credential_secret": "unused-public-surface",
                "corpus_snapshot_hash": corpus_hash,
                "scanner_version": SCANNER_VERSION,
                "corpus_version": CORPUS_VERSION,
                "seed": SEED,
                "scan_mode": "live_probe",
                "k_replicates": 5,
                "scanner_invocation_context": {"purpose": "mid_term"},
            },
        )
        assert response.status_code == 202, response.text
        results.append(response.json())

    required = {
        "scanner_version",
        "corpus_version",
        "model_versions",
        "seed",
        "are_total",
        "subscores",
        "evidence_refs",
        "forensic_bundle_uri",
        "snapshot_hash",
    }
    for result in results:
        assert not required.difference(result)
        assert result["scanner_version"] == SCANNER_VERSION
        assert result["corpus_version"] == CORPUS_VERSION
        assert result["seed"] == SEED
        serialized = json.dumps(result)
        assert ".local" not in serialized
        assert "s3://" not in serialized
        bundle_ref = result["forensic_bundle_uri"]
        assert bundle_ref.startswith("/forensic_bundle/")
        assert {
            ref["artifact_uri"] for ref in result["evidence_refs"]
        } == {bundle_ref}
        fetched = client.get(
            bundle_ref, headers={"X-Tenant-Id": "tenant-beaaa18652"}
        )
        assert fetched.status_code == 200
        bundle = fetched.json()
        assert bundle["target_endpoint_host"] == "apichat.syllable.cloud"
        assert bundle["scan_request"]["credential_ref"] == "<redacted>"
        assert bundle["evidence"]
        assert all("prompt_sha256" in row for row in bundle["evidence"])
        assert all("prompt" not in row for row in bundle["evidence"])
        evidence_text = json.dumps(bundle["evidence"])
        assert "fixture@example.com" not in evidence_text
        assert "415-555-0100" not in evidence_text
        assert "<redacted-email>" in evidence_text
        assert "<redacted-phone>" in evidence_text
        assert client.get(
            bundle_ref, headers={"X-Tenant-Id": "other-tenant"}
        ).status_code == 404

    totals = [result["are_total"] for result in results]
    assert max(totals) - min(totals) <= 1
    for pillar in results[0]["subscores"]:
        values = [result["subscores"][pillar] for result in results]
        assert max(values) - min(values) <= 1

    # Durable backend retrieval survives an application/store restart.
    restarted = TestClient(
        create_app(
            scan_runtime=runtime,
            bundles=BundleStore(FileBundleStorage(bundle_dir)),
        )
    )
    assert restarted.get(
        results[-1]["forensic_bundle_uri"],
        headers={"X-Tenant-Id": "tenant-beaaa18652"},
    ).status_code == 200
