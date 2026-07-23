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
    assert packet["rollback_drill"]["total_operator_minutes"] <= 30
    assert packet["backup_restore_drill"]["integrity_pass"] is True
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
