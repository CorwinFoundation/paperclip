"""BEAAA-6159 - Log Tracker v0 scanner-run write hook.

Rule-execution per scanner-implementation-supervision-v1@v1.1: additive
write hook under existing BackBond Scanner output rules. The hook consumes
the finalized `are-scanner-api-v1@v1.0` score envelope and must not mutate
the `/scans` response shape or scoring fields.
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
LOG_TRACKER_SRC = REPO / "tools" / "log-tracker" / "src"
if str(LOG_TRACKER_SRC) not in sys.path:
    sys.path.insert(0, str(LOG_TRACKER_SRC))

from api.scan_handlers import ScanRuntime, scan_handler
from api.scan_log import ScanLog
from log_tracker import ArtifactUriUnreachable, LogTracker
from scanners.live_probe_orchestrator import (
    LIVE_PROBE_VARIANCE_BAND,
    DimResult,
    ScanRequest,
    ScanResult,
)


AGENT_ID = "agent-log-tracker"
TENANT = "tenant-001"
RUN_TIMESTAMP = "2026-06-17T10:00:00Z"

SCORE_ENVELOPE = {
    "are_total": 73,
    "subscores": {
        "GOVERNANCE": 18,
        "ROBUSTNESS": 19,
        "BIAS_FAIRNESS": 17,
        "OPS": 19,
    },
    "band": "B",
    "confidence": 0.92,
    "evidence_refs": [
        {
            "rubric_point_id": "ROB.PI.MULTI_TURN",
            "test_id": "t-1",
            "artifact_uri": "https://evidence.local/1",
            "verdict": "pass",
            "weight": 0.5,
        },
        {
            "rubric_point_id": "OPS.COVERAGE.DEFERRED",
            "test_id": "N/A",
            "artifact_uri": "",
            "verdict": "n/a",
            "weight": 0.0,
        },
    ],
    "uncovered_dimensions": [],
    "scanner_version": "0.4.1",
    "corpus_version": "0.2.0",
    "rubric_version": "1.2.0",
    "model_versions": {"primary": "m-1", "judges": ["j-1"]},
    "seed": 42,
    "run_timestamp": RUN_TIMESTAMP,
}


def _result(scan_id: str) -> ScanResult:
    return ScanResult(
        scan_id=scan_id,
        status="complete",
        scanner_version="0.4.1",
        corpus_version="0.2.0",
        rows_emitted=10,
        k_replicates=5,
        live_probe_variance_band=LIVE_PROBE_VARIANCE_BAND,
        dim_results=(
            DimResult(
                dim_id="P1.1",
                raw=4.0,
                spread=0.0,
                replicates=(4.0, 4.0, 4.0, 4.0, 4.0),
            ),
        ),
        spread_per_dim={"P1.1": 0.0},
        pillar_spread={"OPS": 0.0},
        total_spread=0.0,
        incomplete=False,
        incomplete_reason=None,
    )


def _body() -> dict:
    return {
        "tenant_id": TENANT,
        "agent_id": AGENT_ID,
        "agent_endpoint": {"host": "agent.customer.example.com"},
        "credential_ref": "phase1-probe.v1",
        "credential_secret": "scoped-token-abc",
        "corpus_snapshot_hash": "sha256:" + "a" * 64,
        "scanner_version": "0.4.1",
        "corpus_version": "0.2.0",
        "seed": 42,
        "scan_mode": "live_probe",
        "k_replicates": 5,
        "requested_pillars": ["OPS"],
    }


def _runtime(*, tracker: LogTracker | None) -> ScanRuntime:
    def run_scan_fn(scan_request: ScanRequest, secret: str) -> ScanResult:
        return _result(scan_request.scan_id)

    return ScanRuntime(
        run_scan_fn=run_scan_fn,
        scan_log=ScanLog(),
        scanner_run_tracker=tracker,
        run_timestamp_factory=lambda: RUN_TIMESTAMP,
        forensic_bundle_uri_factory=lambda aid, sid: f"s3://bundles/{aid}/{sid}.json",
        score_envelope_factory=lambda result, aid, ts: copy.deepcopy(SCORE_ENVELOPE),
    )


def test_log_tracker_commits_scanner_run_and_full_evidence_refs():
    tracker = LogTracker(artifact_uri_resolver=lambda uri: uri.startswith("https://"))

    envelope = tracker.emit_scanner_run_completed(
        agent_id=AGENT_ID,
        scan_id="scan-1",
        run_timestamp=RUN_TIMESTAMP,
        score_json=SCORE_ENVELOPE,
    )

    row = tracker.get_scanner_run("scan-1")
    refs = tracker.list_evidence_refs("scan-1")

    assert row is not None
    assert row["tracker_envelope"]["event_kind"] == "scanner_run.completed"
    assert row["tracker_envelope"]["contract_ref"] == "are-scanner-api-v1@v1.0"
    assert envelope["evidence_refs"] == SCORE_ENVELOPE["evidence_refs"]
    assert [r["ref_json"] for r in refs] == SCORE_ENVELOPE["evidence_refs"]
    assert row["tracker_envelope"]["are_total"] == SCORE_ENVELOPE["are_total"]
    assert row["tracker_envelope"]["subscores"] == SCORE_ENVELOPE["subscores"]


def test_unreachable_artifact_uri_rolls_back_scanner_run_insert():
    tracker = LogTracker(artifact_uri_resolver=lambda uri: "bad" not in uri)
    score = copy.deepcopy(SCORE_ENVELOPE)
    score["evidence_refs"][0]["artifact_uri"] = "https://evidence.local/bad"

    with pytest.raises(ArtifactUriUnreachable):
        tracker.emit_scanner_run_completed(
            agent_id=AGENT_ID,
            scan_id="scan-bad",
            run_timestamp=RUN_TIMESTAMP,
            score_json=score,
        )

    assert tracker.get_scanner_run("scan-bad") is None
    assert tracker.list_evidence_refs("scan-bad") == []


def test_scan_runtime_write_hook_preserves_scan_response_shape():
    baseline_status, baseline_payload = scan_handler(
        _body(),
        tenant_header=TENANT,
        runtime=_runtime(tracker=None),
        scan_id_factory=lambda: "scan-hook",
    )
    tracker = LogTracker(artifact_uri_resolver=lambda uri: True)
    hooked_status, hooked_payload = scan_handler(
        _body(),
        tenant_header=TENANT,
        runtime=_runtime(tracker=tracker),
        scan_id_factory=lambda: "scan-hook",
    )

    assert hooked_status == baseline_status == 202
    assert hooked_payload == baseline_payload
    row = tracker.get_scanner_run("scan-hook")
    assert row is not None
    assert row["tracker_envelope"]["scan_id"] == "scan-hook"
    assert row["tracker_envelope"]["evidence_refs"] == SCORE_ENVELOPE["evidence_refs"]
