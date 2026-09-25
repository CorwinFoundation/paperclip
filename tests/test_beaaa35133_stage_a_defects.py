"""BEAAA-35133: a receipt states the activation state in force when its case was
recorded, and one human approval authorises exactly one execution on one ledger.

Both defects were measured at master 726b221f. Defect A: ``EvidenceStore.packet``
recomputed the protection block at read time, so a pre-activation receipt began
claiming coverage the moment the demo was activated, and coverage that was in
force at purchase time disappeared when the terms expired. Defect B: replay.
"""

from __future__ import annotations

import json

import pytest

from stage_a_demo import activation as act
from stage_a_demo.approval import approve_mandate
from stage_a_demo.core import EvidenceStore, Mandate, SyntheticMerchantAdapter

VERSION = "program-35133"
FUTURE = "2099-01-01T00:00:00Z"
PAST = "2020-01-01T00:00:00Z"


def complete_records(expires_at: str) -> dict:
    return {
        "governing_legal_structure": {
            "record_id": "legal-1", "program_version": VERSION,
            "status": "in_good_standing", "entity_name": "BackBond Program Co",
            "jurisdiction": "Delaware, US",
        },
        "executed_backing_authority": {
            "record_id": "backing-1", "program_version": VERSION, "status": "executed",
            "counterparty": "Backing Counterparty", "executed_at": "2026-09-01T00:00:00Z",
            "document_sha256": "a" * 64,
        },
        "funded_financial_security": {
            "record_id": "funding-1", "program_version": VERSION, "status": "funded",
            "instrument": "segregated account", "funded_amount_cents": 5_000_000,
            "funded_at": "2026-09-02T00:00:00Z",
        },
        "approved_versioned_terms": {
            "record_id": "terms-1", "program_version": VERSION, "status": "approved",
            "terms_version": "terms-1.0.0", "approved_at": "2026-09-03T00:00:00Z",
            "expires_at": expires_at,
        },
        "named_claims_and_payment_humans": {
            "record_id": "humans-1", "program_version": VERSION, "status": "named",
            "claims_reviewer": "Claims Reviewer", "payment_approver": "Payment Approver",
        },
        "approved_consumer_disclosure_set": {
            "record_id": "disclosure-1", "program_version": VERSION, "status": "approved",
            "disclosure_set_id": "disclosure-set-1", "approved_at": "2026-09-04T00:00:00Z",
        },
    }


@pytest.fixture
def program(tmp_path, monkeypatch):
    """Drive the activation gate: call with a record set, or None for no records."""
    root = tmp_path / "activation-records"
    root.mkdir()
    monkeypatch.setenv(act.PROGRAM_VERSION_ENV, VERSION)
    monkeypatch.setenv(act.ACTIVATION_RECORDS_ENV, str(root))

    def set_state(records: dict | None) -> None:
        path = root / f"{VERSION}.json"
        if records is None:
            path.unlink(missing_ok=True)
        else:
            path.write_text(json.dumps(records))

    set_state(None)
    return set_state


class CountingMerchant(SyntheticMerchantAdapter):
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def purchase(self, **kwargs):
        self.calls.append(dict(kwargs))
        return super().purchase(**kwargs)


def buy(store, *, key, case_id=None, merchant=None, mandate=None, grant_id=None):
    case_id = case_id or key
    mandate = mandate or Mandate(f"m-{key}", "SKU-1", 100)
    if grant_id is None:
        grant_id = approve_mandate(store, case_id=case_id, mandate=mandate)
    packet = store.record_case(
        case_id=case_id,
        grant_id=grant_id,
        asserted_sku="SKU-1",
        asserted_amount_cents=100,
        idempotency_key=key,
        merchant=merchant or SyntheticMerchantAdapter(),
    )
    return packet, grant_id


# ── Defect A — the receipt states the state in force when the case was recorded ──

def test_a_pre_activation_receipt_never_gains_coverage_later(tmp_path, program):
    store = EvidenceStore(tmp_path / "ledger.db")
    packet, _ = buy(store, key="pre")
    assert packet["protection"]["protection_active"] is False

    program(complete_records(FUTURE))
    assert act.current_status().active is True

    assert store.packet("pre")["protection"]["protection_active"] is False
    store.close()

    reopened = EvidenceStore(tmp_path / "ledger.db")
    after = reopened.packet("pre")["protection"]
    reopened.close()
    assert after["protection_active"] is False
    assert after["statement"] == act.NO_COVERAGE_STATEMENT


def test_a_coverage_in_force_at_purchase_survives_expiry(tmp_path, program):
    program(complete_records(FUTURE))
    store = EvidenceStore(tmp_path / "ledger.db")
    packet, _ = buy(store, key="live")
    assert packet["protection"]["protection_active"] is True
    assert packet["protection"]["terms_version"] == "terms-1.0.0"

    program(complete_records(PAST))
    assert act.current_status().active is False

    store.close()
    reopened = EvidenceStore(tmp_path / "ledger.db")
    after = reopened.packet("live")["protection"]
    reopened.close()
    assert after["protection_active"] is True
    assert after["terms_version"] == "terms-1.0.0"
    assert after["reasons"] == []


def test_a_case_with_no_recorded_state_reads_as_no_coverage(tmp_path, program):
    program(complete_records(FUTURE))
    store = EvidenceStore(tmp_path / "ledger.db")
    buy(store, key="legacy")
    store.db.execute("UPDATE cases SET protection_json = NULL")
    store.db.commit()
    block = store.packet("legacy")["protection"]
    store.close()
    assert block["protection_active"] is False
    assert block["statement"] == act.NO_COVERAGE_STATEMENT
    assert block["reasons"] == ["protection_state_not_recorded"]


def test_a_ledger_written_before_the_column_existed_still_opens(tmp_path, program):
    """An older ledger file has no protection_json column at all."""
    import sqlite3

    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript(
        """
        CREATE TABLE cases (
          case_id TEXT PRIMARY KEY, mode TEXT NOT NULL, mandate_json TEXT NOT NULL,
          approval_json TEXT NOT NULL, assertion_json TEXT NOT NULL,
          merchant_json TEXT, status TEXT NOT NULL, mismatch_json TEXT NOT NULL
        );
        INSERT INTO cases VALUES ('old', 'demo', '{}', '{}', '{}', NULL, 'MATCH', '[]');
        """
    )
    old.commit()
    old.close()

    program(complete_records(FUTURE))
    store = EvidenceStore(path)
    block = store.packet("old")["protection"]
    store.close()
    assert block["protection_active"] is False
    assert block["reasons"] == ["protection_state_not_recorded"]


# ── Defect B — one approval, one execution, on one ledger ──────────────────────

def test_b_one_approval_authorises_one_execution(tmp_path, program):
    """One approval buys one execution, and a recorded case cannot be re-run.

    BEAAA-34389 A5 added an append-only rule on cases that fires *before* the
    grant is consumed, so a same-case retry is now refused by that rule rather
    than by the spent grant. Both invariants are asserted here: the refusal,
    and the grant's spent state read directly off the store. The spent-grant
    refusal message itself still has direct coverage in
    ``test_b_a_grant_is_bound_to_the_ledger_that_recorded_it``, which replays
    onto a second ledger where no case row exists to preempt it, and in the
    abuse harness A7, whose losing racers are refused for exactly that reason.
    """
    store = EvidenceStore(tmp_path / "ledger.db")
    merchant = CountingMerchant()
    mandate = Mandate("m-replay", "SKU-1", 100)
    _, grant_id = buy(store, key="replay-0", case_id="replay", merchant=merchant, mandate=mandate)

    # The one approval is spent by the one execution it authorised.
    assert store._approval_preview(grant_id, "replay") is None

    for n in range(1, 4):
        with pytest.raises(PermissionError, match="already recorded"):
            buy(store, key=f"replay-{n}", case_id="replay", merchant=merchant,
                mandate=mandate, grant_id=grant_id)

    assert len(merchant.calls) == 1
    assert sum(call["amount_cents"] for call in merchant.calls) == 100
    assert store.case_count() == 1
    store.close()


def test_b_a_grant_is_bound_to_the_ledger_that_recorded_it(tmp_path, program):
    first = EvidenceStore(tmp_path / "first.db")
    second = EvidenceStore(tmp_path / "second.db")
    merchant = CountingMerchant()
    mandate = Mandate("m-cross", "SKU-1", 100)
    _, grant_id = buy(first, key="k-first", case_id="cross", merchant=merchant, mandate=mandate)

    with pytest.raises(PermissionError, match="no unspent human approval grant"):
        second.record_case(
            case_id="cross", grant_id=grant_id, asserted_sku="SKU-1",
            asserted_amount_cents=100, idempotency_key="k-second", merchant=merchant,
        )

    assert len(merchant.calls) == 1
    assert second.case_count() == 0
    first.close()
    second.close()


def test_b_a_second_grant_still_cannot_reach_the_merchant_twice_on_one_key(tmp_path, program):
    """Per-key idempotency holds independently of the approval check."""
    store = EvidenceStore(tmp_path / "ledger.db")
    merchant = CountingMerchant()
    mandate = Mandate("m-retry", "SKU-1", 100)
    packet, _ = buy(store, key="retry", case_id="retry", merchant=merchant, mandate=mandate)

    again, _ = buy(store, key="retry", case_id="retry", merchant=merchant, mandate=mandate)
    assert again["case_id"] == packet["case_id"]
    assert len(merchant.calls) == 1
    assert store.execution_count("retry") == 1
    store.close()


def test_b_a_human_can_revoke_an_unspent_approval(tmp_path, program):
    store = EvidenceStore(tmp_path / "ledger.db")
    merchant = CountingMerchant()
    mandate = Mandate("m-revoke", "SKU-1", 100)
    grant_id = approve_mandate(store, case_id="revoked", mandate=mandate)

    assert store.revoke_approval(grant_id) is True
    with pytest.raises(PermissionError, match="revoked"):
        store.record_case(
            case_id="revoked", grant_id=grant_id, asserted_sku="SKU-1",
            asserted_amount_cents=100, idempotency_key="k-revoked", merchant=merchant,
        )

    assert merchant.calls == []
    assert store.case_count() == 0
    assert store.revoke_approval(grant_id) is False
    store.close()


def test_b_revoking_a_spent_approval_reports_too_late(tmp_path, program):
    store = EvidenceStore(tmp_path / "ledger.db")
    _, grant_id = buy(store, key="spent")
    assert store.revoke_approval(grant_id) is False
    assert store.revoke_approval("grant-invented") is False
    store.close()
