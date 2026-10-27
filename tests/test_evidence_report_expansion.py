"""Default dispatch, persistence and public evidence inventory contracts."""
from copy import deepcopy
from threading import Event

import pytest

from api.full_scan_executor import FullScanJobStore
from api.full_scan_job_persistence import FullScanJobPersistence
from api.scan_handlers import ScanRuntime, _external_result_envelope
from api.scan_log import ScanLog
from harness.runner import MockRunner
from report_card.requirements import render_requirements
from scanners.live_probe_orchestrator import ScanRequest
from scripts.run_operational_controls import FixtureLogCapture
from standard_requirement_report import build_standard_requirement_report
from tools.framework_workers import build_live_framework_workers
from tools.operational_scan import registry_rows
from tools.active_test_registry import build_registry
from requirement_assessment import load_registry
from tools.registered_case_executor import ScanBudget


def test_candidate_associations_resolve_to_registered_families_or_explicit_legacy_concepts():
    families = {row["test_id"] for row in build_registry()}
    registry = load_registry()
    legacy = set(registry["legacy_mapping_concepts"])
    for framework in registry["frameworks"]:
        for control in framework["controls"]:
            unresolved = set(control["candidate_test_ids"]) - families
            assert unresolved == set(control["legacy_candidate_test_ids"])
            assert unresolved <= legacy


def test_quality_cases_complete_normal_async_dispatch_and_survive_restart(tmp_path, monkeypatch):
    monkeypatch.setenv("ARE_BUNDLE_SIGNING_KEY", "evidence-contract-key")
    def forbidden(_): pytest.fail("a model or legacy walker was invoked")
    def workers(request, secret):
        return build_live_framework_workers(request, secret, evidence_root=tmp_path / "evidence",
            budget=ScanBudget(max_target_calls=0, max_judge_calls=0),
            subject_factory=lambda *_: MockRunner("target", forbidden),
            judge_factory=lambda: MockRunner("judge", forbidden),
            operational_log_capture_factory=lambda _: FixtureLogCapture("reviewer-A"))
    db = tmp_path / "jobs.sqlite3"
    runtime = ScanRuntime(run_scan_fn=forbidden,
        full_scan_registry_rows=[r for r in registry_rows() if r["test_id"] == "operational_logging_quality"],
        full_scan_workers_factory=workers, full_scan_job_store=FullScanJobStore(persistence=FullScanJobPersistence(db)),
        scan_log=ScanLog(tmp_path / "scans.sqlite3"),
        score_envelope_factory=lambda *a: {"are_total": 72, "band": "B", "evidence_refs": [],
                                          "run_timestamp": "2026-10-27T12:00:00+00:00"})
    request = ScanRequest(scan_id="scan-quality", tenant_id="tenant-A", agent_id="agent-A",
        agent_endpoint="https://target.example", credential_ref="ephemeral", corpus_snapshot_hash="a" * 64,
        scanner_version="test", corpus_version="test", seed=42)
    done = Event()
    runtime.kickoff(request, "secret", agent_id="agent-A", on_terminal=done.set)
    assert done.wait(15)
    result = runtime.fetch(request.scan_id)
    assert result.status == "complete"
    manifest = result.full_suite_manifest
    assert manifest["counts"] == {"expected": 5, "evidence_rows": 5, "missing_required": 0}
    assert all(r["target_calls"] == r["judge_calls"] == 0 and len(r["sha256"]) == 64
               and r["diagnostic_measurements"] for r in manifest["evidence_rows"])
    restored = FullScanJobStore(persistence=FullScanJobPersistence(db)).status(manifest["job_id"])
    assert [r.to_manifest_row() for r in restored.evidence_rows] == manifest["evidence_rows"]
    persisted = runtime.current_kickoff_row(request.scan_id)["score_json"]
    report = _external_result_envelope(result, persisted)
    assert report["are_total"] == 72
    assert report["requirements_evidence"]["evidence_inventory"]["with_reported_digest"] == 5
    html = render_requirements(persisted)
    assert "OPS-ALERT-SELECTIVITY" in html and "false_alert_count: 0" in html
    assert "operational-log-quality@1" in html


def test_existing_resilience_and_oversight_families_are_visible_in_both_frameworks():
    families = ["idempotency_retry_double_execution", "checkpoint_restore_integrity",
                "tool_schema_drift_partial_failure", "concurrent_shared_state_isolation"]
    report = build_standard_requirement_report({}, manifest={"evidence_rows": [
        {"test_id": f, "probe_id": f + ".case", "verdict": "fail", "evidence_ref": "evidence://" + f}
        for f in families]})
    for fid, cid in [("eu-ai-act", "ART15"), ("iso-iec-42001", "A.6.2.4")]:
        framework = next(f for f in report["frameworks"] if f["framework_id"] == fid)
        control = next(c for c in framework["controls"] if c["control_id"] == cid)
        assert set(o["test_id"] for o in control["scan_observations"]) >= set(families)
        assert control["technical_status"] == "not evaluated" and control["requirement_conclusion"] == "not assessed"
        assert framework["mapping_version"].endswith("@2")
        assert "hallucination" not in control["unobserved_test_ids"]
        assert "hallucination" in control["unresolved_mapping_concepts"]


def test_inventory_filters_raw_data_and_keeps_distinct_observations_of_same_case():
    source = {"scan_id": "scan", "evidence_refs": [
        {"test_id": "operational_logging_quality", "case_id": "OPS-LOG-PAYLOAD", "artifact_uri": "evidence://one",
         "verdict": "pass", "sha256": "x", "trace": "private-secret", "diagnostic_measurements": {
             "payload_errors": 0, "identity_errors": True, "secret": "private-secret"}},
        {"test_id": "operational_logging_quality", "case_id": "OPS-LOG-PAYLOAD", "artifact_uri": "evidence://two", "verdict": "fail"}]}
    before = deepcopy(source)
    report = build_standard_requirement_report(source)
    inventory = report["evidence_inventory"]
    assert source == before
    assert inventory["reported_case_count"] == 1 and inventory["reported_observation_count"] == 2
    assert inventory["with_reported_digest"] == 0
    assert inventory["artifacts"][0]["diagnostic_measurements"] == {"payload_errors": 0}
    assert "private-secret" not in str(report)
    inventory["artifacts"][0]["method"] = '<script>unsafe</script>'
    html = render_requirements({"requirements_evidence": report})
    assert "<script>" not in html and "&lt;script&gt;unsafe&lt;/script&gt;" in html
