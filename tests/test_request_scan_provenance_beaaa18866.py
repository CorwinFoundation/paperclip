"""BEAAA-18866 request-to-scan provenance index contract tests."""
from __future__ import annotations

import tempfile
import unittest
from unittest import mock
from pathlib import Path

from fastapi.testclient import TestClient

from api.app import create_app
from api.bundle import BundleStore
from api.request_scan_provenance import (
    ProvenanceConflict,
    RequestScanProvenanceIndex,
)
from api.scan_handlers import ScanRuntime
from rubric.scoring import Rubric
from scanners.live_probe_orchestrator import ScanRequest, ScanResult

REPO = Path(__file__).resolve().parents[1]


class RequestScanProvenanceIndexTests(unittest.TestCase):
    def test_request_reference_is_immutable_and_survives_reopen(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "scanner.sqlite3"
            index = RequestScanProvenanceIndex(path)
            index.bind(
                tenant_id="tenant-a",
                source_issue_id="BEAAA-18861",
                request_id="request-7",
                scan_id="scan-a",
            )
            index.close()

            reopened = RequestScanProvenanceIndex(path)
            self.assertEqual(
                reopened.resolve_scan_id(
                    tenant_id="tenant-a",
                    source_issue_id="BEAAA-18861",
                    request_id="request-7",
                ),
                "scan-a",
            )
            with self.assertRaises(ProvenanceConflict):
                reopened.bind(
                    tenant_id="tenant-a",
                    source_issue_id="BEAAA-18861",
                    request_id="request-7",
                    scan_id="scan-b",
                )

    def test_cross_tenant_and_missing_tenant_fail_closed(self) -> None:
        index = RequestScanProvenanceIndex(":memory:")
        index.bind(
            tenant_id="tenant-a",
            source_issue_id="BEAAA-18861",
            request_id="request-7",
            scan_id="scan-a",
        )
        self.assertIsNone(
            index.resolve_scan_id(
                tenant_id="tenant-b",
                source_issue_id="BEAAA-18861",
                request_id="request-7",
            )
        )

    def test_internal_lookup_returns_only_audit_fields(self) -> None:
        index = RequestScanProvenanceIndex(":memory:")
        index.bind(
            tenant_id="tenant-a",
            source_issue_id="BEAAA-18861",
            request_id="request-7",
            scan_id="scan-a",
        )
        raw = {
            "scan_id": "scan-a",
            "status": "completed",
            "terminal_at": "2026-07-29T10:00:00Z",
            "are_total": 86,
            "band": "A",
            "scanner_version": "0.5.0",
            "corpus_version": "2026-07-29",
            "corpus_snapshot_hash": "a" * 64,
            "model_versions": {"judge": "pinned"},
            "seed": 0,
            "evidence_refs": ["scanner://bundle/1"],
            "credential_secret": "must-not-leak",
        }
        payload = index.lookup(
            tenant_id="tenant-a",
            source_issue_id="BEAAA-18861",
            request_id="request-7",
            scan_loader=lambda scan_id: raw if scan_id == "scan-a" else None,
        )
        self.assertEqual(
            set(payload or {}),
            {
                "scan_id",
                "status",
                "terminal_at",
                "backbond_score",
                "band",
                "scanner_version",
                "corpus_version",
                "corpus_snapshot_hash",
                "model_versions",
                "seed",
                "evidence_refs",
            },
        )
        self.assertEqual((payload or {})["backbond_score"], 86)

    def test_internal_route_requires_both_auth_layers_and_hides_cross_tenant(self) -> None:
        tenant_a, tenant_b = "tenant-a", "tenant-b"
        scan_id = "scan-a"

        def run(req: ScanRequest, secret: str) -> ScanResult:
            return ScanResult(
                scan_id=req.scan_id,
                status="complete",
                scanner_version=req.scanner_version,
                corpus_version=req.corpus_version,
                rows_emitted=1,
                k_replicates=5,
            )

        runtime = ScanRuntime(run_scan_fn=run)
        runtime.kickoff(
            ScanRequest(
                scan_id=scan_id,
                tenant_id=tenant_a,
                agent_endpoint="https://agent.example",
                credential_ref="cred",
                corpus_snapshot_hash="sha256:" + "a" * 64,
                scanner_version="0.5.0",
                corpus_version="corpus-1",
                seed=0,
                k_replicates=5,
            ),
            "secret",
        )
        index = RequestScanProvenanceIndex(":memory:")
        index.bind(
            tenant_id=tenant_a,
            source_issue_id="BEAAA-18861",
            request_id="request-7",
            scan_id=scan_id,
        )
        env = {
            "ARE_API_AUTH_TOKENS_JSON": (
                f'{{"tok-a":"{tenant_a}","tok-b":"{tenant_b}"}}'
            ),
            "ARE_INTERNAL_PROVENANCE_TOKEN": "internal-only",
        }
        with mock.patch.dict("os.environ", env, clear=False):
            client = TestClient(
                create_app(
                    rubric=Rubric.load(REPO / "rubric" / "rubric.yaml"),
                    bundles=BundleStore(),
                    scan_runtime=runtime,
                    request_scan_provenance=index,
                )
            )
            path = (
                "/internal/scanner/v1/request-scan-provenance"
                "?source_issue_id=BEAAA-18861&request_id=request-7"
            )
            self.assertEqual(client.get(path).status_code, 401)
            self.assertEqual(
                client.get(
                    path, headers={"Authorization": "Bearer tok-a"}
                ).status_code,
                401,
            )
            allowed = client.get(
                path,
                headers={
                    "Authorization": "Bearer tok-a",
                    "X-Internal-Provenance-Key": "internal-only",
                },
            )
            self.assertEqual(allowed.status_code, 200, allowed.text)
            cross = client.get(
                path,
                headers={
                    "Authorization": "Bearer tok-b",
                    "X-Internal-Provenance-Key": "internal-only",
                },
            )
            self.assertEqual(cross.status_code, 404, cross.text)
        self.assertIsNone(
            index.resolve_scan_id(
                tenant_id=None,
                source_issue_id="BEAAA-18861",
                request_id="request-7",
            )
        )


if __name__ == "__main__":
    unittest.main()
