"""BEAAA-35012: fail-closed activation gate and truthful no-coverage states."""

from __future__ import annotations

import inspect
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from stage_a_demo import activation as act
from stage_a_demo import ui
from stage_a_demo.approval import build_issuer
from stage_a_demo.approval import approve_mandate
from stage_a_demo.core import (
    EvidenceStore,
    HumanApprovalIssuer,
    Mandate,
    SyntheticMerchantAdapter,
)

VERSION = "program-v1"
NOW = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)
LATER = (NOW + timedelta(days=365)).isoformat().replace("+00:00", "Z")


def complete_records() -> dict[str, dict[str, object]]:
    return {
        "governing_legal_structure": {
            "record_id": "legal-1",
            "program_version": VERSION,
            "status": "in_good_standing",
            "entity_name": "BackBond Program Co",
            "jurisdiction": "Delaware, US",
        },
        "executed_backing_authority": {
            "record_id": "backing-1",
            "program_version": VERSION,
            "status": "executed",
            "counterparty": "Backing Counterparty",
            "executed_at": "2026-09-01T00:00:00Z",
            "document_sha256": "a" * 64,
        },
        "funded_financial_security": {
            "record_id": "funding-1",
            "program_version": VERSION,
            "status": "funded",
            "instrument": "segregated account",
            "funded_amount_cents": 50_000_00,
            "funded_at": "2026-09-02T00:00:00Z",
        },
        "approved_versioned_terms": {
            "record_id": "terms-1",
            "program_version": VERSION,
            "status": "approved",
            "terms_version": "terms-1.0.0",
            "approved_at": "2026-09-03T00:00:00Z",
            "expires_at": LATER,
        },
        "named_claims_and_payment_humans": {
            "record_id": "humans-1",
            "program_version": VERSION,
            "status": "named",
            "claims_reviewer": "Named Reviewer",
            "payment_approver": "Named Approver",
        },
        "approved_consumer_disclosure_set": {
            "record_id": "disclosures-1",
            "program_version": VERSION,
            "status": "approved",
            "disclosure_set_id": "disclosures-1.0.0",
            "approved_at": "2026-09-04T00:00:00Z",
        },
    }


def registry_with(records: dict[str, object]) -> act.MappingRegistry:
    return act.MappingRegistry({VERSION: records})


def active_status() -> act.ActivationStatus:
    return act.check_activation(VERSION, registry_with(complete_records()), now=NOW)


# 1. All records present -> active.


def test_all_records_present_is_active():
    status = active_status()
    assert status.active is True
    assert status.missing_records == ()
    assert status.reasons == ()
    assert status.activation_record_id
    assert status.terms_version == "terms-1.0.0"
    assert status.statement != act.NO_COVERAGE_STATEMENT


# 2. Each record individually missing -> not active.


@pytest.mark.parametrize("key", act.REQUIRED_RECORD_KEYS)
def test_each_missing_record_is_not_active(key):
    records = complete_records()
    del records[key]
    status = act.check_activation(VERSION, registry_with(records), now=NOW)
    assert status.active is False
    assert status.missing_records == (key,)
    assert f"{key}:missing" in status.reasons
    assert status.statement == act.NO_COVERAGE_STATEMENT
    assert status.activation_record_id is None


@pytest.mark.parametrize("key", act.REQUIRED_RECORD_KEYS)
def test_each_malformed_record_is_not_active(key):
    records = complete_records()
    records[key] = "not-a-record"
    status = act.check_activation(VERSION, registry_with(records), now=NOW)
    assert status.active is False
    assert f"{key}:malformed" in status.reasons
    assert status.statement == act.NO_COVERAGE_STATEMENT


@pytest.mark.parametrize("key", act.REQUIRED_RECORD_KEYS)
def test_record_for_another_program_version_is_not_active(key):
    records = complete_records()
    records[key] = {**records[key], "program_version": "program-v2"}
    status = act.check_activation(VERSION, registry_with(records), now=NOW)
    assert status.active is False
    assert f"{key}:program_version_mismatch" in status.reasons


def test_unfunded_financial_security_is_not_active():
    records = complete_records()
    records["funded_financial_security"] = {
        **records["funded_financial_security"],
        "funded_amount_cents": 0,
    }
    status = act.check_activation(VERSION, registry_with(records), now=NOW)
    assert status.active is False
    assert "funded_financial_security:not_funded" in status.reasons


# 3. Expired terms version -> not active.


def test_expired_terms_version_is_not_active():
    records = complete_records()
    records["approved_versioned_terms"] = {
        **records["approved_versioned_terms"],
        "expires_at": "2026-09-22T00:00:00Z",
    }
    status = act.check_activation(VERSION, registry_with(records), now=NOW)
    assert status.active is False
    assert "approved_versioned_terms:expired" in status.reasons
    assert status.statement == act.NO_COVERAGE_STATEMENT


def test_terms_without_expiry_is_not_active():
    records = complete_records()
    terms = dict(records["approved_versioned_terms"])
    terms.pop("expires_at")
    records["approved_versioned_terms"] = terms
    status = act.check_activation(VERSION, registry_with(records), now=NOW)
    assert status.active is False
    assert "approved_versioned_terms:missing_field:expires_at" in status.reasons


def test_unparseable_expiry_is_not_active():
    records = complete_records()
    records["approved_versioned_terms"] = {
        **records["approved_versioned_terms"],
        "expires_at": "whenever",
    }
    status = act.check_activation(VERSION, registry_with(records), now=NOW)
    assert status.active is False
    assert "approved_versioned_terms:malformed_expiry" in status.reasons


# 4. Check throws -> not active.


def test_registry_raising_is_not_active():
    class ExplodingRegistry(act.RecordRegistry):
        def records(self, program_version):
            raise RuntimeError("record store unavailable")

    status = act.check_activation(VERSION, ExplodingRegistry(), now=NOW)
    assert status.active is False
    assert status.reasons == ("activation_check_error",)
    assert status.statement == act.NO_COVERAGE_STATEMENT
    assert status.missing_records == act.REQUIRED_RECORD_KEYS


def test_absent_registry_is_not_active():
    status = act.check_activation(VERSION, None, now=NOW)
    assert status.active is False
    assert status.reasons == ("registry:absent",)


def test_non_mapping_registry_payload_is_not_active():
    class ListRegistry(act.RecordRegistry):
        def records(self, program_version):
            return ["governing_legal_structure"]

    status = act.check_activation(VERSION, ListRegistry(), now=NOW)
    assert status.active is False
    assert status.reasons == ("registry:malformed",)


def test_blank_program_version_is_not_active():
    status = act.check_activation("", registry_with(complete_records()), now=NOW)
    assert status.active is False
    assert status.statement == act.NO_COVERAGE_STATEMENT


def test_missing_records_file_is_not_active(tmp_path):
    status = act.check_activation(VERSION, act.FileRegistry(str(tmp_path)), now=NOW)
    assert status.active is False
    assert status.statement == act.NO_COVERAGE_STATEMENT


def test_file_registry_reads_a_complete_record_set(tmp_path):
    (tmp_path / f"{VERSION}.json").write_text(json.dumps(complete_records()))
    status = act.check_activation(VERSION, act.FileRegistry(str(tmp_path)), now=NOW)
    assert status.active is True


def test_corrupt_records_file_is_not_active(tmp_path):
    (tmp_path / f"{VERSION}.json").write_text("{not json")
    status = act.check_activation(VERSION, act.FileRegistry(str(tmp_path)), now=NOW)
    assert status.active is False
    assert status.reasons == ("activation_check_error",)


# 5. The free period cannot start without an activation record.


def test_free_period_starts_only_on_an_activation_record():
    period = act.start_free_period(active_status(), act.FREE_PERIOD_TRIGGER, started_at=NOW)
    assert period is not None
    assert period.activation_record_id == active_status().activation_record_id
    assert period.started_at == NOW


@pytest.mark.parametrize(
    "event", ["interest_list_signup", "connection_failed", "demo_completed", ""]
)
def test_non_activation_events_do_not_start_the_free_period(event):
    assert act.start_free_period(active_status(), event, started_at=NOW) is None


def test_free_period_does_not_start_without_activation():
    inactive = act.check_activation(VERSION, act.MappingRegistry({}), now=NOW)
    assert inactive.active is False
    assert act.start_free_period(inactive, act.FREE_PERIOD_TRIGGER, started_at=NOW) is None


def test_free_period_does_not_start_from_a_forged_active_status():
    forged = act.ActivationStatus(
        active=True, program_version=VERSION, statement="anything", activation_record_id=None
    )
    assert act.start_free_period(forged, act.FREE_PERIOD_TRIGGER, started_at=NOW) is None


# Surfaces read from the single check and never infer coverage.


@pytest.mark.parametrize("surface", ["screen", "receipt", "api", "referral"])
def test_surface_state_is_not_active_without_records(surface):
    block = act.surface_protection_state(surface, VERSION, act.MappingRegistry({}), now=NOW)
    assert block["protection_active"] is False
    assert block["statement"] == act.NO_COVERAGE_STATEMENT
    assert block["surface"] == surface


def test_surface_state_takes_no_inferrable_input():
    parameters = set(inspect.signature(act.surface_protection_state).parameters)
    assert parameters == {"surface", "program_version", "registry", "now"}
    for forbidden in ("scan_result", "receipt", "policy_id", "subscription"):
        with pytest.raises(TypeError):
            act.surface_protection_state(
                "receipt", VERSION, act.MappingRegistry({}), **{forbidden: "coverage"}
            )


def test_referral_disclosure_never_implies_coverage():
    inactive = act.check_activation(VERSION, act.MappingRegistry({}), now=NOW)
    assert act.referral_disclosure(inactive) == act.NO_COVERAGE_STATEMENT
    assert act.referral_disclosure(active_status()) == active_status().statement


def test_no_coverage_string_is_defined_once():
    literal = "No protection, reimbursement or coverage is active."
    sources = [p for p in Path("stage_a_demo").glob("*.py") if literal in p.read_text()]
    assert [p.name for p in sources] == ["activation.py"]


def test_no_coverage_string_is_exact():
    assert (
        act.NO_COVERAGE_STATEMENT
        == "Demo or observation only. No protection, reimbursement or coverage is active."
    )


def test_receipt_packet_states_protection_from_the_gate(tmp_path, monkeypatch):
    monkeypatch.setenv(act.ACTIVATION_RECORDS_ENV, str(tmp_path / "absent"))
    mandate = Mandate("mandate-gate-v1", "SKU-LAMP", 4800)
    store = EvidenceStore(tmp_path / "evidence.db")
    grant = approve_mandate(store, case_id="gate", mandate=mandate)
    packet = store.record_case(
        case_id="gate",
        grant_id=grant,
        asserted_sku="SKU-LAMP",
        asserted_amount_cents=4800,
        idempotency_key="idem-gate",
        merchant=SyntheticMerchantAdapter(),
    )
    store.close()
    assert packet["status"] == "MATCH"
    assert packet["protection"]["protection_active"] is False
    assert packet["protection"]["statement"] == act.NO_COVERAGE_STATEMENT


def test_demo_surfaces_state_no_coverage(tmp_path, monkeypatch):
    monkeypatch.setenv(act.ACTIVATION_RECORDS_ENV, str(tmp_path / "absent"))
    db = tmp_path / "ui.db"
    grant = ui.approve_case("correct", db, issuer=build_issuer())["grant"]
    receipt = ui.execute_case("correct", db, grant)
    assert receipt["protection"]["protection_active"] is False
    assert receipt["protection"]["statement"] == act.NO_COVERAGE_STATEMENT
    # BEAAA-35453 A13: reading the receipt delivers no shareable note. The
    # note is built only when the user asks for one, and it carries the same
    # protection statement.
    assert receipt["invitation"] is None
    shared = ui.invitation_decision("correct", "create", db)
    assert set(shared["payload"]) == {
        "brand",
        "mode",
        "category",
        "completed",
        "invitation_code",
    }
    assert shared["protection"] == act.NO_COVERAGE_STATEMENT
    for action in ("create", "keep_private", "done"):
        assert (
            ui.invitation_transition("MATCH", action)["protection"]
            == act.NO_COVERAGE_STATEMENT
        )
    assert (
        ui.invitation_transition("MISMATCH", "create")["protection"]
        == act.NO_COVERAGE_STATEMENT
    )
    assert act.NO_COVERAGE_STATEMENT in ui.render_page()
