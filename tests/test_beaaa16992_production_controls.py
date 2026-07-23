from __future__ import annotations

from scripts import beaaa16992_production_controls_evidence as evidence


def test_beaaa16992_packet_proves_required_controls() -> None:
    packet = evidence.build_packet()

    assert packet["classification"]["value"] == "rule-execution"
    assert packet["contract"] == "are-scanner-api-v1@v1.0"
    assert packet["acceptance"]["overall_pass"] is True
    assert packet["acceptance"]["authorized_end_to_end_design_partner_scan"] is True
    assert packet["acceptance"]["tenant_isolation_and_retention"] is True
    assert packet["acceptance"]["backup_restore_rpo_rto"] is True
    assert packet["acceptance"]["rollback_le_30m"] is True
    assert packet["candidate_commit_sha"] == packet["evidence_build_head_sha"]
    assert (
        packet["candidate_identity"]["candidate_commit_sha_source"]
        == "git rev-parse HEAD at packet build"
    )
    assert packet["rollback_drill"]["measured_minutes_ceil"] <= 30
    assert packet["rollback_drill"]["source_matches_measured_drill"] is True
    assert packet["backup_restore_drill"]["integrity_pass"] is True
    assert packet["backup_restore_drill"]["observed_recovery_point_lag_s"] <= 3600
    assert packet["fail_closed_summary"]["all_negative_cases_fail_closed"] is True
    assert packet["fail_closed_summary"]["valid_signed_manifest_passes"] is True
    assert packet["promotion_gate"]["promotion_authorized_by_this_packet"] is False


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
    assert (
        packet["design_partner_calibration_instrumentation"]["event_counts"][
            "customer_feedback_hook"
        ]
        == 1
    )
