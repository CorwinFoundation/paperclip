from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from api.production_controls import ProductionControlLedger
from api.scan_handlers import ScanRuntime, scan_handler
from scripts import beaaa16992_production_controls_evidence as evidence
from scanners.live_probe_orchestrator import DimResult, OrchestratorError, ScanRequest, ScanResult


TENANT = "tenant-beaaa16992"


def _scan_body() -> dict:
    return {
        "tenant_id": TENANT,
        "agent_endpoint": {"host": "scanner-target.example.com"},
        "credential_ref": "phase1-probe.v1",
        "credential_secret": "redacted-secret",
        "corpus_snapshot_hash": "sha256:" + "a" * 64,
        "scanner_version": "0.5.0",
        "corpus_version": "golden-fixtures@0.5.0",
        "seed": 0,
        "k_replicates": 5,
        "vendor_id": "design-partner-redacted",
    }


def _runtime_result(req: ScanRequest, _secret: str) -> ScanResult:
    return ScanResult(
        scan_id=req.scan_id,
        status="complete",
        scanner_version=str(req.scanner_version),
        corpus_version=str(req.corpus_version),
        rows_emitted=1,
        k_replicates=req.k_replicates,
        dim_results=(
            DimResult(
                dim_id="ROBUSTNESS.DATA_ISOLATION.CROSS_SESSION_DATA_LEAK",
                raw=4.0,
                spread=0.0,
                replicates=(4.0,) * 5,
            ),
        ),
    )


def test_beaaa16992_packet_proves_required_controls() -> None:
    packet = evidence.build_packet()

    assert packet["classification"]["value"] == "rule-execution"
    assert packet["contract"] == "are-scanner-api-v1@v1.0"
    assert packet["acceptance"]["overall_pass"] is True
    assert packet["acceptance"]["authorized_end_to_end_design_partner_scan"] is True
    assert packet["acceptance"]["tenant_isolation_and_retention"] is True
    assert packet["acceptance"]["backup_restore_rpo_rto"] is True
    assert packet["acceptance"]["rollback_le_30m"] is True
    assert packet["candidate_commit_sha"] == "6f9733632b3569f1eb46445e4b2a7c2522761f48"
    assert packet["evidence_build_head_sha"]
    assert (
        packet["candidate_identity"]["candidate_commit_sha_source"]
        == "artifacts/beaaa-17044/wave1a-production-authorization-6f973363.json"
    )
    assert packet["candidate_identity"]["evidence_build_head_sha_source"] == (
        "git rev-parse HEAD at packet build"
    )
    assert packet["rollback_drill"]["measured_minutes_ceil"] <= 30
    assert packet["rollback_drill"]["source_matches_measured_drill"] is True
    assert (
        packet["rollback_drill"]["canonical_rollback_command"]
        == "scripts/deploy/box_deploy.sh rollback <previous_ref>"
    )
    assert packet["rollback_drill"]["monitored_service_transition"] is True
    assert (
        packet["rollback_drill"]["retained_evidence_hash_before"]
        == packet["rollback_drill"]["retained_evidence_hash_after"]
    )
    assert packet["rollback_drill"]["retained_evidence_present_before"] is True
    assert packet["rollback_drill"]["retained_evidence_present_after"] is True
    assert packet["rollback_drill"]["feature_flag_disabled_families"] == [
        "consequential_action_confirmation",
        "cross_session_data_leak",
    ]
    assert packet["backup_restore_drill"]["integrity_pass"] is True
    assert packet["backup_schedule_evidence"]["backup_schedule"] == "hourly"
    assert packet["backup_schedule_evidence"]["private_network"] is True
    assert packet["backup_schedule_evidence"]["rpo_pass"] is True
    assert packet["backup_schedule_evidence"]["rto_pass"] is True
    assert packet["fail_closed_summary"]["all_negative_cases_fail_closed"] is True
    assert packet["fail_closed_summary"]["valid_signed_manifest_passes"] is True
    assert packet["security_findings"]["pass"] is True
    assert packet["acceptance"]["critical_high_security_findings_absent"] is True
    assert packet["promotion_gate"]["promotion_authorized_by_this_packet"] is False
    assert packet["generated_evidence_sources"]["wave1a_generated_from_current_tree"] is True
    assert packet["generated_evidence_sources"]["beaaa16977_artifact_inputs_required"] is False


def test_beaaa16992_control_matrix_has_no_unproven_required_control() -> None:
    packet = evidence.build_packet()

    required = {
        "tenant_isolation",
        "authn_authz",
        "encryption_and_secret_handling",
        "retention",
        "rate_limits",
        "monitoring",
        "backups",
        "incident_response",
        "design_partner_calibration_instrumentation",
        "feature_flag_rollback",
    }
    assert set(packet["controls"]) == required
    assert {row["status"] for row in packet["controls"].values()} == {"PASS"}
    assert packet["controls"]["rate_limits"]["checks"]["scans_per_min_denied"] is True
    assert packet["controls"]["authn_authz"]["checks"]["edge_auth_checks"] == {
        "revoked_denied": True,
        "expired_denied": True,
    }
    assert "schedule" in packet["controls"]["backups"]["checks"]
    assert "drill" in packet["controls"]["backups"]["checks"]
    assert (
        packet["design_partner_calibration_instrumentation"]["event_counts"][
            "customer_feedback_hook"
        ]
        == 1
    )
    assert packet["design_partner_calibration_instrumentation"]["durable_store"] == (
        "jsonl retained hook ledgers"
    )
    assert packet["design_partner_calibration_instrumentation"]["ledger_errors"] == []


def test_beaaa17073_committed_instrumentation_store_is_rebuilt_idempotently(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    clean_out = tmp_path / "beaaa-16992"
    clean_store = clean_out / "instrumentation-store"
    shutil.copytree(evidence.OUT_DIR / "instrumentation-store", clean_store)
    monkeypatch.setattr(evidence, "OUT_DIR", clean_out)

    first = evidence.build_packet()
    second = evidence.build_packet()

    assert first["acceptance"]["overall_pass"] is True
    assert second["acceptance"]["overall_pass"] is True
    assert second["design_partner_calibration_instrumentation"]["retained_ledger_line_counts"] == {
        "customer_feedback_hook": 1,
        "eventual_outcome_hook": 1,
        "result_record": 1,
    }
    assert {
        path.name: len(path.read_text(encoding="utf-8").splitlines())
        for path in clean_store.glob("*.jsonl")
    } == {
        "customer_feedback_hook.jsonl": 1,
        "eventual_outcome_hook.jsonl": 1,
        "result_record.jsonl": 1,
    }


def test_beaaa16992_rollback_hash_proof_fails_on_tamper_or_absent_retained_evidence() -> None:
    source = {
        "active_executable_before": 340,
        "active_executable_after": 305,
        "unrelated_active_rows_unchanged": True,
        "rolled_back_families": ["cross_session_data_leak"],
    }
    retained = {"scan_id": "scan-beaaa-16992", "response_hash": "sha256:" + "a" * 64}

    tampered = evidence._rollback_drill(source, retained, tamper_retained_after=True)
    assert tampered["historical_evidence_preserved"] is False
    assert tampered["retained_evidence_hash_before"] != tampered["retained_evidence_hash_after"]
    assert tampered["pass"] is False

    absent = evidence._rollback_drill(source, retained, omit_retained_before=True)
    assert absent["retained_evidence_present_before"] is False
    assert absent["historical_evidence_preserved"] is False
    assert absent["pass"] is False

    unmounted = evidence._rollback_drill(source, retained, omit_state_mount=True)
    assert unmounted["state_mount_validation"]["mount_present"] is False
    assert unmounted["historical_evidence_preserved"] is False
    assert unmounted["pass"] is False


def test_beaaa16992_security_findings_fail_closed_for_critical_or_high() -> None:
    clean = evidence._security_findings_result(
        [{"severity": "medium", "id": "dependency-warning"}]
    )
    assert clean["pass"] is True

    critical = evidence._security_findings_result(
        [{"severity": "critical", "id": "auth-bypass"}]
    )
    assert critical["pass"] is False
    assert critical["blocking_findings"][0]["id"] == "auth-bypass"

    high = evidence._security_findings_result(
        [{"severity": "high", "id": "tenant-data-exposure"}]
    )
    assert high["pass"] is False


def test_beaaa17051_packet_blocks_on_actual_security_source_findings(monkeypatch) -> None:
    monkeypatch.setattr(
        evidence.production_controls,
        "repo_security_findings",
        lambda _repo: [{"severity": "high", "id": "runtime-authz-bypass"}],
    )

    packet = evidence.build_packet()

    assert packet["security_findings"]["pass"] is False
    assert packet["acceptance"]["critical_high_security_findings_absent"] is False
    assert packet["acceptance"]["overall_pass"] is False
    assert packet["security_findings"]["source"]["kind"] == (
        "api.production_controls.repo_security_findings"
    )


def test_beaaa16992_instrumentation_fails_when_retained_ledger_absent_or_tampered(
    tmp_path: Path,
) -> None:
    common = {
        "candidate_commit_sha": "6f9733632b3569f1eb46445e4b2a7c2522761f48",
        "signed_manifest_sha256": "sha256:" + "b" * 64,
        "target_output_sha256": "sha256:" + "c" * 64,
    }

    absent = evidence._instrumentation_hook_drill(
        **common,
        store_dir=tmp_path / "absent",
        emit_event_types={"result_record", "customer_feedback_hook"},
    )
    assert absent["pass"] is False
    assert absent["event_counts"] == {
        "customer_feedback_hook": 1,
        "result_record": 1,
    }

    tampered = evidence._instrumentation_hook_drill(
        **common,
        store_dir=tmp_path / "tampered",
        tamper_event_type="eventual_outcome_hook",
    )
    assert tampered["pass"] is False
    assert any("hook_event_hash_mismatch" in err for err in tampered["ledger_errors"])
    assert tampered["runtime_source"]["kind"] == (
        "api.production_controls.ProductionControlLedger"
    )


def test_beaaa17052_scan_handler_writes_runtime_production_control_ledger_once(
    tmp_path: Path,
) -> None:
    ledger = ProductionControlLedger(tmp_path / "runtime-ledger")
    runtime = ScanRuntime(
        run_scan_fn=_runtime_result,
        production_control_ledger=ledger,
        production_security_findings_source=lambda: [
            {"severity": "medium", "id": "dependency-warning"}
        ],
    )

    for _ in range(2):
        status, payload = scan_handler(
            _scan_body(),
            tenant_header=TENANT,
            runtime=runtime,
            scan_id_factory=lambda: "scan-beaaa17052-runtime",
        )
        assert status == 202
        assert payload["scan_id"] == "scan-beaaa17052-runtime"

    records = ledger.read("result_record")
    assert len(records) == 1
    assert records[0]["design_partner_ref"] == "design-partner-redacted"
    assert records[0]["scan_id"] == "scan-beaaa17052-runtime"
    assert records[0]["evidence_ref"] == "scanner://scan-results/scan-beaaa17052-runtime"
    assert records[0]["payload"]["scanner_version"] == "0.5.0"
    assert records[0]["payload"]["security_findings_pass"] is True


def test_beaaa17052_runtime_security_source_blocks_before_scan(tmp_path: Path) -> None:
    calls = {"run_scan": 0}

    def run_scan(req: ScanRequest, secret: str) -> ScanResult:
        calls["run_scan"] += 1
        return _runtime_result(req, secret)

    runtime = ScanRuntime(
        run_scan_fn=run_scan,
        production_control_ledger=ProductionControlLedger(tmp_path / "runtime-ledger"),
        production_security_findings_source=lambda: [
            {"severity": "high", "id": "runtime-authz-bypass"}
        ],
    )

    with pytest.raises(OrchestratorError, match="runtime-authz-bypass"):
        runtime.kickoff(
            ScanRequest(
                scan_id="scan-beaaa17052-blocked",
                tenant_id=TENANT,
                agent_endpoint="https://scanner-target.example.com",
                credential_ref="phase1-probe.v1",
                corpus_snapshot_hash="sha256:" + "a" * 64,
                scanner_version="0.5.0",
                corpus_version="golden-fixtures@0.5.0",
                seed=0,
                k_replicates=5,
            ),
            "redacted-secret",
        )

    assert calls["run_scan"] == 0
