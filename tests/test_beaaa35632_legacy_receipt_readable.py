"""BEAAA-35632: a receipt captured before the deadline gate must stay readable.

QA round-nine Q5/Q6 on BEAAA-34396. The capture gate added by BEAAA-35480 lives
in ``ProposedAction.__post_init__``, and ``EvidenceStore.receipt_for`` and
``claim_for`` both rebuild a ProposedAction on the *read* path. So a row written
before that gate landed could not be read at all: both entry points raised
``UnorderableCancellationDeadline`` out of the read. The user could not see
their own receipt and the claim could not be assessed.

The fix is not to loosen the gate. Capture still refuses. Reading returns the
receipt with ``cancellation_deadline_not_orderable`` recorded against it.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from stage_a_demo.core import EvidenceStore
from stage_a_demo.receipt import (
    ExecutedOutcome,
    ProposedAction,
    UnorderableCancellationDeadline,
    action_from_stored_terms,
)

UNZONED = "2026-10-11T12:00:00"
ZONED = "2026-10-11T12:00:00Z"

PRE_GATE_TERMS = {
    "description": "cancel the Rome booking",
    "merchant": "hotel-roma",
    "amount_cents": 0,
    "deposit_cents": 12000,
    "cancellation_terms": "free cancellation until the deadline",
    "cancellation_deadline": UNZONED,
    "terms_captured_at": "2026-10-01T09:00:00Z",
    "terms_evidence_ref": "email-4411",
    "is_cancellation_obligation": True,
}


def _pre_gate_row(path, task_id="task-legacy"):
    """Write the receipt bytes a pre-35480 database holds.

    Written through sqlite3 directly because no public path can build these
    terms any more - which is the point: the gate works, and the rows it would
    now refuse are already on disk.
    """
    receipt = {
        "receipt_id": f"r-{task_id}",
        "task_id": task_id,
        "proposed_action": PRE_GATE_TERMS,
        "answer": "OK",
        "checked_at": "2026-10-01T09:00:00Z",
        "answered_at": "2026-10-01T09:01:00Z",
        "recorded_at": "2026-10-01T09:01:05Z",
    }
    store = EvidenceStore(path)
    store.db.execute(
        "INSERT INTO approval_receipts (task_id, receipt_json, recorded_at) VALUES (?, ?, ?)",
        (task_id, json.dumps(receipt), receipt["recorded_at"]),
    )
    store.db.commit()
    return store


@pytest.fixture()
def store(tmp_path):
    store = _pre_gate_row(tmp_path / "legacy.sqlite3")
    try:
        yield store
    finally:
        store.close()


def test_the_capture_gate_still_refuses(store):
    """Q1 to Q4 keep holding. This fix must not reopen capture."""
    with pytest.raises(UnorderableCancellationDeadline):
        ProposedAction(**PRE_GATE_TERMS)


def test_receipt_for_returns_the_receipt_instead_of_raising(store):
    receipt = store.receipt_for("task-legacy")
    assert receipt is not None
    action = receipt.proposed_action
    # Read exactly as written: no offset invented to make it order.
    assert action.cancellation_deadline == UNZONED
    assert action.deadline_instant is None
    assert action.deposit_cents == 12000
    assert action.terms_evidence_ref == "email-4411"


def test_the_defect_is_named_on_the_read_path(store):
    """Q6: deadline_capture_defect is reachable again, and only here."""
    action = store.receipt_for("task-legacy").proposed_action
    assert action.deadline_capture_defect == "cancellation_deadline_not_orderable"
    assert action.capture_defects() == ["cancellation_deadline_not_orderable"]
    assert action.terms_evidence()["capture_defect"] == "cancellation_deadline_not_orderable"


def test_claim_for_settles_and_records_the_defect_as_ours(store):
    review = store.claim_for("task-legacy")
    assert "cancellation_deadline_not_orderable" in review["record_defects"]
    assert review["cancellation_terms_evidence"]["capture_defect"] == (
        "cancellation_deadline_not_orderable"
    )
    # Settled, not raised, and not silently covered on a defect of ours.
    assert review["claim"]
    assert review["outcome_state"] == "UNKNOWN/INCOMPLETE"
    assert review["covered"] is False


def test_the_defect_is_recorded_once_when_the_obligation_path_reads_it(store):
    """The obligation path names the same defect; it must not double-report it."""
    store.record_outcome(
        "task-legacy",
        ExecutedOutcome(
            description="cancel the Rome booking",
            merchant="hotel-roma",
            amount_cents=0,
            evidence_ref="merchant-9001",
            performed=False,
            observed_at="2026-10-11T18:00:00Z",
        ),
        executed_at="2026-10-11T18:00:00Z",
    )
    review = store.claim_for("task-legacy")
    assert review["record_defects"].count("cancellation_deadline_not_orderable") == 1
    # An orderable observation past a deadline read at UTC is still a loss: a
    # deadline we failed to write down properly never turns a loss into nothing.
    assert review["outcome_state"] == "NOT_PERFORMED"
    assert review["covered"] is True
    assert review["claim"] == "covered_missed_cancellation_deadline"


def test_a_clean_receipt_carries_no_capture_defect(tmp_path):
    store = EvidenceStore(tmp_path / "clean.sqlite3")
    try:
        action = action_from_stored_terms({**PRE_GATE_TERMS, "cancellation_deadline": ZONED})
        assert action.deadline_instant is not None
        assert action.capture_defects() == []
    finally:
        store.close()


def test_the_reader_is_not_a_bypass_for_anything_constructible():
    """Terms the gate accepts go through the gate, unchanged."""
    accepted = {**PRE_GATE_TERMS, "cancellation_deadline": ZONED}
    assert action_from_stored_terms(accepted) == ProposedAction(**accepted)
    # An offset with its evidence ref is the documented remedy, not a bypass.
    remedied = {
        **PRE_GATE_TERMS,
        "cancellation_deadline_utc_offset_minutes": 120,
        "cancellation_deadline_offset_evidence_ref": "terms-page-2",
    }
    assert action_from_stored_terms(remedied) == ProposedAction(**remedied)
    assert action_from_stored_terms(remedied).deadline_capture_defect is None


def test_the_reader_refuses_bytes_that_are_not_these_terms():
    with pytest.raises(TypeError):
        action_from_stored_terms({**PRE_GATE_TERMS, "surprise": 1})
    missing = {k: v for k, v in PRE_GATE_TERMS.items() if k != "merchant"}
    with pytest.raises(TypeError):
        action_from_stored_terms(missing)


def test_a_pre_gate_database_survives_a_restart(tmp_path):
    """The reachable shape: written to a relative file, read after the upgrade."""
    path = tmp_path / "restart.sqlite3"
    _pre_gate_row(path).close()
    reopened = EvidenceStore(path)
    try:
        assert reopened.receipt_for("task-legacy") is not None
        assert reopened.claim_for("task-legacy")["record_defects"] == [
            "cancellation_deadline_not_orderable"
        ]
    finally:
        reopened.close()
