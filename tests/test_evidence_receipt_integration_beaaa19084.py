"""BEAAA-19084 exact issuance -> persistence -> dual-delivery seam."""
from __future__ import annotations

import pytest

from api.ci_scan_ext import ci_plugin_result
from api.evidence_receipt import FEATURE_FLAG
from api.evidence_receipts import issue_and_persist
from api.scan_handlers import ScanRuntime, _customer_result_response
from api.scan_log import ScanLog, ScanLogAppendOnly
from scanners.live_probe_orchestrator import ScanResult
from tests.test_evidence_receipts_beaaa18819 import service, submission


def test_issued_receipt_persists_and_projects_identically(monkeypatch):
    scan_id = "scan-1"
    agent_id = "agent-1"
    run_timestamp = "2026-07-29T08:00:00Z"
    score = {
        "are_total": 84,
        "band": "B",
        "subscores": {},
        "scanner_version": "0.5.0",
        "corpus_version": "0.2.0",
        "seed": 0,
        "model_versions": {},
        "evidence_refs": [],
        "confidence": 0.9,
        "uncovered_dimensions": [],
    }
    result = ScanResult(
        scan_id=scan_id,
        status="completed",
        scanner_version="0.5.0",
        corpus_version="0.2.0",
        rows_emitted=1,
    )
    receipt_service, _, _, _, identity = service()

    with ScanLog() as scan_log:
        scan_log.append_scan(
            agent_id=agent_id,
            run_timestamp=run_timestamp,
            scan_id=scan_id,
            scanner_version="0.5.0",
            corpus_version="0.2.0",
            score_json=score,
            forensic_bundle_uri="/forensic_bundle/opaque",
        )
        issued = issue_and_persist(
            submission(scan_id=scan_id),
            identity=identity,
            service=receipt_service,
            projection_store=scan_log,
            agent_id=agent_id,
            run_timestamp=run_timestamp,
        )
        with pytest.raises(ScanLogAppendOnly):
            scan_log.attach_evidence_receipt(
                agent_id=agent_id,
                run_timestamp=run_timestamp,
                receipt=issued,
            )
        runtime = ScanRuntime(
            run_scan_fn=lambda request, secret: result,
            scan_log=scan_log,
        )
        runtime._kickoff_keys[scan_id] = (agent_id, run_timestamp)

        monkeypatch.delenv(FEATURE_FLAG, raising=False)
        webhook_off = _customer_result_response(scan_id, result, runtime)
        ci_off = ci_plugin_result(
            scan_id=scan_id,
            dim_labels={},
            dim_raws={},
            band="B",
            disposition="gate_pass",
            dashboard_base_url="https://dash.example",
            evidence_receipt=issued,
        )
        assert "evidence_receipt" not in webhook_off
        assert "evidence_receipt" not in ci_off

        monkeypatch.setenv(FEATURE_FLAG, "1")
        webhook_on = _customer_result_response(scan_id, result, runtime)
        ci_on = ci_plugin_result(
            scan_id=scan_id,
            dim_labels={},
            dim_raws={},
            band="B",
            disposition="gate_pass",
            dashboard_base_url="https://dash.example",
            evidence_receipt=issued,
        )
        assert webhook_on["evidence_receipt"] == issued
        assert ci_on["evidence_receipt"] == issued
        assert {key: webhook_on[key] for key in webhook_off} == webhook_off
        assert {key: ci_on[key] for key in ci_off} == ci_off
