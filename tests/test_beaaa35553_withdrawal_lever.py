"""BEAAA-35553: the withdrawal lever reaches Path 1, and stops at its owner.

QA round-seven probes on BEAAA-34396 (Z2/Z3/Z5) found the lever covered one
half of Stage A. `revoke_approval` existed on the mandate path and held there,
but a Path 1 receipt never writes an `approvals` row, so a user who answered OK
and then changed their mind had nothing to pull: `execute_approved_task` still
went to the merchant on 48000 cents at risk. The lever that did exist was also
writable across the tenant boundary, because `approvals` had no owner column
where `cases` gained one for BEAAA-35357 A9 and `approval_receipts` gained one
for BEAAA-35578.

Two stores on one file throughout, the shape those findings live in. Controls
are here for the reason they are there: a store that refused every write would
pass the isolation legs and be useless.
"""
from __future__ import annotations

import pytest

from stage_a_demo.approval import approve_mandate
from stage_a_demo.core import EvidenceStore, Mandate, SyntheticMerchantAdapter
from stage_a_demo.receipt import (
    ANSWER_OK,
    ApprovalReceipt,
    CheckNotApproved,
    ExecutedOutcome,
    ProposedAction,
)

ESTER = ProposedAction(
    description="Hotel Ester, 3 nights, 12-15 Oct, 2 guests",
    merchant="bookings.example",
    amount_cents=48_000,
    deposit_cents=12_000,
    cancellation_terms="Deposit non-refundable after 24h; balance refundable to 7 days before arrival",
)
AS_APPROVED = ExecutedOutcome(
    description=ESTER.description,
    merchant=ESTER.merchant,
    amount_cents=ESTER.amount_cents,
    evidence_ref="merchant-confirmation",
)
WITHDRAWN_AT = "2026-09-24T01:15:00Z"
EXECUTED_AT = "2026-09-24T02:00:00Z"


class CountingMerchant(SyntheticMerchantAdapter):
    def __init__(self) -> None:
        self.calls: list[str] = []

    def purchase(self, *, case_id, sku, amount_cents):
        self.calls.append(case_id)
        return super().purchase(case_id=case_id, sku=sku, amount_cents=amount_cents)


def _receipt(task_id: str) -> ApprovalReceipt:
    return ApprovalReceipt(
        receipt_id=f"receipt-{task_id}",
        task_id=task_id,
        proposed_action=ESTER,
        answer=ANSWER_OK,
        checked_at="2026-09-24T01:07:00Z",
        answered_at="2026-09-24T01:09:00Z",
        recorded_at="2026-09-24T01:09:05Z",
    )


@pytest.fixture()
def db(tmp_path):
    return tmp_path / "evidence.sqlite3"


def _owner(db, task_id: str, tenant: str = "acme") -> EvidenceStore:
    store = EvidenceStore(db, tenant=tenant)
    store.record_pre_execution_receipt(_receipt(task_id))
    return store


# ---------------------------------------------------------------------------
# Z2: Path 1 has a lever, and it is read before the merchant call.
# ---------------------------------------------------------------------------


def test_z2_withdrawing_a_path1_receipt_stops_execution(db):
    """The probe's own sequence: OK recorded, mind changed, nothing executes."""
    store = _owner(db, "task-z2")
    merchant = CountingMerchant()

    assert store.withdraw_receipt("task-z2", withdrawn_at=WITHDRAWN_AT) is True
    with pytest.raises(CheckNotApproved, match="withdrawn"):
        store.execute_approved_task("task-z2", merchant=merchant, executed_at=EXECUTED_AT)

    assert merchant.calls == []
    claim = store.claim_for("task-z2")
    assert claim["withdrawn_at"] == WITHDRAWN_AT
    assert claim["outcome"] is None
    store.close()


def test_z2_control_an_unwithdrawn_receipt_still_executes(db):
    """Control. The lever must refuse the withdrawn task and only that one."""
    store = _owner(db, "task-z2c")
    merchant = CountingMerchant()

    claim = store.execute_approved_task("task-z2c", merchant=merchant, executed_at=EXECUTED_AT)

    assert merchant.calls == ["task-z2c"]
    assert claim["claim"] == "not_covered_user_approved_this"
    assert claim["withdrawn_at"] is None
    store.close()


def test_z2_nothing_to_withdraw_reports_false(db):
    """The answers the mandate lever gives: unknown task, already withdrawn."""
    store = _owner(db, "task-z2d")

    assert store.withdraw_receipt("task-invented", withdrawn_at=WITHDRAWN_AT) is False
    assert store.withdraw_receipt("task-z2d", withdrawn_at=WITHDRAWN_AT) is True
    assert store.withdraw_receipt("task-z2d", withdrawn_at="2026-09-24T01:20:00Z") is False
    assert store.withdrawn_at("task-z2d") == WITHDRAWN_AT
    store.close()


def test_z2_a_withdrawal_instant_is_required(db):
    store = _owner(db, "task-z2e")
    with pytest.raises(ValueError):
        store.withdraw_receipt("task-z2e", withdrawn_at="   ")
    assert store.withdrawn_at("task-z2e") is None
    store.close()


def test_z4_withdrawal_never_unwinds_a_completed_purchase(db):
    """Z4 parity with the mandate lever. The money is gone; the record says so."""
    store = _owner(db, "task-z4")
    merchant = CountingMerchant()
    settled = store.execute_approved_task("task-z4", merchant=merchant, executed_at=EXECUTED_AT)

    assert store.withdraw_receipt("task-z4", withdrawn_at="2026-09-24T03:00:00Z") is False

    after = store.claim_for("task-z4")
    assert after["withdrawn_at"] is None
    assert after["claim"] == settled["claim"] == "not_covered_user_approved_this"
    assert after["covered"] is False
    assert merchant.calls == ["task-z4"]
    store.close()


def test_z4_withdrawal_loses_to_a_claimed_execution_slot(db):
    """The slot BEAAA-35579 claims before the merchant is the cut-off.

    Once a caller holds the slot the purchase may already be in flight, so a
    withdrawal cannot be honoured even though no outcome is recorded yet.
    """
    store = _owner(db, "task-z4b")

    class Withdrawing(SyntheticMerchantAdapter):
        def purchase(self, *, case_id, sku, amount_cents):
            # Inside the merchant call: the slot is claimed, nothing settled.
            self.mid_flight = store.withdraw_receipt(case_id, withdrawn_at="2026-09-24T02:00:01Z")
            return super().purchase(case_id=case_id, sku=sku, amount_cents=amount_cents)

    merchant = Withdrawing()
    store.execute_approved_task("task-z4b", merchant=merchant, executed_at=EXECUTED_AT)

    assert merchant.mid_flight is False
    assert store.claim_for("task-z4b")["withdrawn_at"] is None
    store.close()


# ---------------------------------------------------------------------------
# Z5: when it executed anyway, the verdict is the withdrawal.
# ---------------------------------------------------------------------------


def test_z5_executing_against_a_withdrawal_is_a_covered_event(db):
    """The charge landed through a path that is not ours. The claim must not
    read `not_covered_user_approved_this` on an approval the user took back."""
    store = _owner(db, "task-z5")
    assert store.withdraw_receipt("task-z5", withdrawn_at=WITHDRAWN_AT) is True

    claim = store.record_outcome("task-z5", AS_APPROVED, executed_at=EXECUTED_AT)

    assert claim["claim"] == "covered_executed_against_withdrawal"
    assert claim["covered"] is True
    assert claim["withdrawn_at"] == WITHDRAWN_AT
    assert claim["outcome_state"] == "MATCH"
    store.close()


def test_z5_a_withdrawal_with_no_independent_evidence_is_still_incomplete(db):
    """The withdrawal does not manufacture a settlement out of a self-report."""
    store = _owner(db, "task-z5b")
    store.withdraw_receipt("task-z5b", withdrawn_at=WITHDRAWN_AT)

    claim = store.record_outcome(
        "task-z5b",
        ExecutedOutcome(
            description=ESTER.description,
            merchant=ESTER.merchant,
            amount_cents=ESTER.amount_cents,
            evidence_ref="self-reported",
            independent=False,
        ),
        executed_at=EXECUTED_AT,
    )

    assert claim["claim"] == "incomplete_no_independent_evidence"
    assert claim["covered"] is False
    assert claim["withdrawn_at"] == WITHDRAWN_AT
    store.close()


# ---------------------------------------------------------------------------
# Z3: the mandate lever stops at the owner of the grant.
# ---------------------------------------------------------------------------


def test_z3_a_foreign_tenant_cannot_revoke_a_grant(db):
    """The probe's measurement: globex revoked acme's unspent grant and got
    True, and acme's own record_case then refused. Denial across the boundary."""
    acme = EvidenceStore(db, tenant="acme")
    grant_id = approve_mandate(acme, case_id="case-z3", mandate=Mandate("m-z3", "SKU-1", 100))

    globex = EvidenceStore(db, tenant="globex")
    assert globex.revoke_approval(grant_id) is False

    merchant = CountingMerchant()
    packet = acme.record_case(
        case_id="case-z3", grant_id=grant_id, asserted_sku="SKU-1",
        asserted_amount_cents=100, idempotency_key="k-z3", merchant=merchant,
    )
    assert packet["case_id"] == "case-z3"
    assert merchant.calls == ["case-z3"]
    globex.close()
    acme.close()


def test_z3_control_the_owner_can_still_revoke(db):
    """Control. Scoping the write must not disable the lever for its owner."""
    acme = EvidenceStore(db, tenant="acme")
    grant_id = approve_mandate(acme, case_id="case-z3c", mandate=Mandate("m-z3c", "SKU-1", 100))

    assert acme.revoke_approval(grant_id) is True
    with pytest.raises(PermissionError, match="revoked"):
        acme.record_case(
            case_id="case-z3c", grant_id=grant_id, asserted_sku="SKU-1",
            asserted_amount_cents=100, idempotency_key="k-z3c", merchant=CountingMerchant(),
        )
    acme.close()


def test_z3_the_new_path1_lever_is_scoped_too(db):
    """The Path 1 lever must not re-open Z3 on the path it was added to.

    It answers a foreign owner the way every other write on the receipt
    surface does since BEAAA-35578: a refusal, not a False.
    """
    acme = _owner(db, "task-z3p")
    globex = EvidenceStore(db, tenant="globex")

    with pytest.raises(PermissionError, match="another owner"):
        globex.withdraw_receipt("task-z3p", withdrawn_at=WITHDRAWN_AT)

    merchant = CountingMerchant()
    claim = acme.execute_approved_task("task-z3p", merchant=merchant, executed_at=EXECUTED_AT)
    assert merchant.calls == ["task-z3p"]
    assert claim["withdrawn_at"] is None
    globex.close()
    acme.close()


# ---------------------------------------------------------------------------
# Z2 on the probe's own terms: one lever, either id.
# ---------------------------------------------------------------------------


def test_z2_revoke_approval_reaches_a_path1_task_id(db):
    """A user knows the id they were given, not which table recorded it."""
    store = _owner(db, "task-z2f")
    merchant = CountingMerchant()

    assert store.revoke_approval("task-z2f") is True
    with pytest.raises(CheckNotApproved, match="withdrawn"):
        store.execute_approved_task("task-z2f", merchant=merchant, executed_at=EXECUTED_AT)

    assert merchant.calls == []
    store.close()


def test_z2_revoke_approval_reaches_a_path1_receipt_id(db):
    store = _owner(db, "task-z2g")
    merchant = CountingMerchant()

    assert store.revoke_approval("receipt-task-z2g") is True
    with pytest.raises(CheckNotApproved, match="withdrawn"):
        store.execute_approved_task("task-z2g", merchant=merchant, executed_at=EXECUTED_AT)

    assert merchant.calls == []
    store.close()


def test_z2_revoke_approval_still_answers_false_for_an_invented_id(db):
    """Control. The fallback must not turn every unknown id into a True."""
    store = _owner(db, "task-z2h")
    assert store.revoke_approval("neither-a-grant-nor-a-receipt") is False
    assert store.withdrawn_at("task-z2h") is None
    store.close()


def test_z3_revoke_approval_does_not_reach_a_foreign_receipt(db):
    """The Path 1 fallback must not become the cross-tenant write Z3 was."""
    acme = _owner(db, "task-z3q")
    globex = EvidenceStore(db, tenant="globex")

    assert globex.revoke_approval("task-z3q") is False
    assert globex.revoke_approval("receipt-task-z3q") is False

    merchant = CountingMerchant()
    acme.execute_approved_task("task-z3q", merchant=merchant, executed_at=EXECUTED_AT)
    assert merchant.calls == ["task-z3q"]
    globex.close()
    acme.close()
