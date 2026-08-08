from __future__ import annotations

from dataclasses import asdict, replace
from pathlib import Path
import sqlite3
import sys

import pytest

from post_bind_ingest.evidence_tier import (
    EvidenceTierError, VerifiedSource, assign_evidence_tier, hub_view,
    rate_multiplier_interface, reject_reserved_client_fields,
)

LOG_TRACKER_SRC = Path(__file__).resolve().parents[2] / "log-tracker" / "src"
sys.path.insert(0, str(LOG_TRACKER_SRC))
from log_tracker.evidence_tier import append_assignment, list_assignments  # noqa: E402


def source(source_class="endpoint_adr", surfaces=("coding",), **changes):
    base = VerifiedSource(
        tenant_id="tenant-a", source_instance_id="source-a", source_class=source_class,
        supported_surfaces=surfaces,
        source_pins={"driver_version":"1", "collector_version":"1", "mapping_version":"1",
                     "evidence_profile_version":"1", "signing_identity_ref":"spiffe://tenant-a/collector",
                     "source_commit":"abc", "binary_hash":"sha256:123"},
        identity_verified=True, signature_valid=True, hash_chain_valid=True,
        redaction_fail_closed=True, replay_idempotency_valid=True, continuity_verified=True,
        silence_distinguished_from_zero=True, coverage_bounded=True, class_specific_verified=True,
        verification_snapshot_hash="sha256:snapshot", verification_window_start="2026-07-01T00:00:00Z",
        verification_window_end="2026-08-01T00:00:00Z",
        registered_agent_id="agent-a",
    )
    return replace(base, **changes)


def assign(sources=None, surfaces=("coding",), **changes):
    args = dict(tenant_id="tenant-a", agent_id="agent-a", term_id="term-a",
                required_surfaces=surfaces, verified_sources=sources or [source()],
                as_of="2026-08-01T00:00:00Z",
                expected_pins={"driver_version":"1", "collector_version":"1", "mapping_version":"1",
                               "evidence_profile_version":"1", "signing_identity_ref":"spiffe://tenant-a/collector"})
    args.update(changes)
    return assign_evidence_tier(**args)


@pytest.mark.parametrize("field", ["tier", "evidence_tier", "source_class", "basis_codes", "policy_version", "override_id"])
def test_spoofed_assignment_fields_are_rejected(field):
    with pytest.raises(EvidenceTierError, match="client_evidence_tier_forbidden"):
        reject_reserved_client_fields({field: "T4"})


@pytest.mark.parametrize("source_class,expected", [
    ("in_code_otel", "T1"), ("gateway_derived", "T2"), ("platform_native", "T2"),
    ("endpoint_adr", "T3"), ("kernel_runtime", "T2"), ("semantic_plus_kernel", "T4"),
    ("unknown", "T0"),
])
def test_verified_source_mapping_and_unknown_fail_closed(source_class, expected):
    assert assign([source(source_class=source_class)]).agent_tier == expected


def test_partial_coverage_uses_lowest_material_surface_and_retains_matrix():
    result = assign([source(surfaces=("coding",))], surfaces=("coding", "voice"))
    assert result.agent_tier == "T0"
    assert {row.surface: row.tier for row in result.surface_assignments} == {"coding": "T3", "voice": "T0"}


@pytest.mark.parametrize("change,code", [
    ({"identity_verified":False}, "identity_failed"), ({"hash_chain_valid":False}, "tamper_failed"),
    ({"redaction_fail_closed":False}, "redaction_fail_open"), ({"continuity_verified":False}, "continuity_failed"),
    ({"stale":True}, "stale_source_pin"), ({"silence_distinguished_from_zero":False}, "silence_zero_ambiguous"),
])
def test_failures_downgrade_and_record_basis(change, code):
    result = assign([source(**change)])
    assert result.agent_tier == "T0" and result.status == "downgraded"
    assert code in result.surface_assignments[0].basis_codes


def test_exception_never_upgrades_and_expires():
    failed = source(identity_verified=False)
    active = assign([failed], exception_id="exc-1", exception_expires_at="2026-08-02T00:00:00Z")
    expired = assign([failed], exception_id="exc-1", exception_expires_at="2026-08-01T00:00:00Z")
    assert active.agent_tier == "T0" and active.status == "exception_active"
    assert expired.agent_tier == "T0" and expired.exception_id is None


def test_tenant_isolation_at_assignment_hub_and_log_tracker():
    with pytest.raises(EvidenceTierError, match="cross_tenant_source_forbidden"):
        assign([replace(source(), tenant_id="tenant-b")])
    result = assign()
    with pytest.raises(EvidenceTierError, match="cross_tenant_assignment_forbidden"):
        hub_view(result, requesting_tenant_id="tenant-b")
    conn = sqlite3.connect(":memory:")
    migration = Path(__file__).resolve().parents[2] / "log-tracker" / "migrations" / "0002_evidence_tier_assignment_v1.sql"
    conn.executescript(migration.read_text())
    append_assignment(conn, asdict(result))
    assert len(list_assignments(conn, tenant_id="tenant-a", agent_id="agent-a")) == 1
    assert list_assignments(conn, tenant_id="tenant-b", agent_id="agent-a") == []
    with pytest.raises(sqlite3.IntegrityError, match="append_only_update_forbidden"):
        conn.execute("UPDATE evidence_tier_events SET agent_tier='T4'")


def test_hub_versions_pins_limitations_and_fixed_in_force_multiplier():
    result = assign()
    hub = hub_view(result, requesting_tenant_id="tenant-a")
    assert hub["policy_version"] == "evidence-tier-assignment/1.0"
    assert hub["surface_assignments"][0]["source_pins"]["source_commit"] == "abc"
    assert hub["surface_assignments"][0]["limitations"]
    rate = rate_multiplier_interface(result, term_id="term-a", bound_current_term_multiplier=1.25,
                                     as_of="2026-08-01T13:00:00Z")
    assert rate["current_term_multiplier"] == 1.25
    assert rate["next_policy_candidate"] == {"status":"shadow_only", "agent_tier":"T3"}
    assert hub["freshness_state"] == "current"
    assert hub["continuity_state"] == "verified"


@pytest.mark.parametrize("changes,code", [
    ({"source_pins": {}}, "source_pins_missing"),
    ({"source_pins": {"driver_version":"wrong"}}, "source_pin_mismatch:driver_version"),
    ({"verification_snapshot_hash":""}, "verification_snapshot_missing"),
    ({"verification_window_start":"2030-01-01T00:00:00Z", "verification_window_end":"2030-02-01T00:00:00Z"}, "verification_window_future"),
    ({"verification_window_start":"2025-01-01T00:00:00Z", "verification_window_end":"2025-02-01T00:00:00Z"}, "verification_window_stale"),
])
def test_registered_pins_and_freshness_fail_closed(changes, code):
    result = assign([source(**changes)])
    assert result.agent_tier == "T0"
    assert any(item.startswith(code) for item in result.surface_assignments[0].basis_codes)


def test_missing_tenant_pin_registration_reproduces_qa_call_and_fails_closed():
    with pytest.raises(EvidenceTierError, match="^tenant_pin_registration_missing$"):
        assign_evidence_tier(
            tenant_id="tenant-a", agent_id="agent-a", term_id="term-a",
            required_surfaces=("coding",), verified_sources=(source(),),
            expected_pins={}, as_of="2026-08-01T00:00:00Z",
        )


def test_registered_source_agent_mismatch_fails_closed():
    with pytest.raises(EvidenceTierError, match="^cross_agent_source_forbidden$"):
        assign([replace(source(), registered_agent_id="agent-b")])


def test_audit_contract_rejects_malformed_and_delete_is_immutable():
    conn = sqlite3.connect(":memory:")
    migration = Path(__file__).resolve().parents[2] / "log-tracker" / "migrations" / "0002_evidence_tier_assignment_v1.sql"
    conn.executescript(migration.read_text())
    event = asdict(assign())
    append_assignment(conn, event)
    broken = dict(event); broken["event_id"] = "second"; broken["approval_ref"] = ""
    with pytest.raises(ValueError, match="evidence_tier_field_invalid:approval_ref"):
        append_assignment(conn, broken)
    with pytest.raises(sqlite3.IntegrityError, match="append_only_delete_forbidden"):
        conn.execute("DELETE FROM evidence_tier_events")


@pytest.mark.parametrize("event_type,before,extra,error", [
    ("assignment", "T0", {}, "evidence_tier_assignment_before_forbidden"),
    ("downgrade", None, {}, "evidence_tier_downgrade_transition_invalid"),
    ("downgrade", "T2", {}, "evidence_tier_downgrade_transition_invalid"),
    ("restoration", None, {}, "evidence_tier_restoration_transition_invalid"),
    ("restoration", "T4", {}, "evidence_tier_restoration_transition_invalid"),
    ("exception", "T2", {}, "evidence_tier_exception_id_required"),
    ("override", "T2", {}, "evidence_tier_override_id_required"),
])
def test_audit_event_specific_invalid_transitions_reject_before_append(event_type, before, extra, error):
    conn = sqlite3.connect(":memory:")
    migration = Path(__file__).resolve().parents[2] / "log-tracker" / "migrations" / "0002_evidence_tier_assignment_v1.sql"
    conn.executescript(migration.read_text())
    event = asdict(assign())
    event.update(event_type=event_type, before_tier=before, **extra)
    with pytest.raises(ValueError, match=f"^{error}$"):
        append_assignment(conn, event)
    assert conn.execute("SELECT COUNT(*) FROM evidence_tier_events").fetchone()[0] == 0


def test_audit_payload_contains_no_raw_content():
    payload = str(assign().audit_payload()).lower()
    for forbidden in ("prompt", "response", "transcript", "tool_arguments", "credential"):
        assert forbidden not in payload
