from email.message import EmailMessage
from email.utils import format_datetime
from datetime import datetime, timezone

import pytest

from stage_b_pilot.inbound import InboundStore


def mail(sender, subject, body="", *, sent_at=None):
    item = EmailMessage()
    item["From"], item["To"], item["Subject"] = sender, "check@inbound.backbond.ai", subject
    if sent_at is not None:
        item["Date"] = format_datetime(sent_at)
    item.set_content(body)
    return item.as_bytes()


def check_body(ref="BB-1"):
    return (f"Check ID: {ref}\nMerchant: Example Hotel\nAmount: USD 25.00\n"
            "Window start: 2026-10-01T10:00:00Z\nWindow end: 2026-10-01T12:00:00Z")


def confirmation_body():
    return "Merchant: Example Hotel\nAmount: USD 25.00\nDate: 2026-10-01T11:00:00Z"


@pytest.fixture
def store(tmp_path):
    opened = InboundStore(tmp_path / "inbound.sqlite3")
    _, token = opened.begin_signup("user@example.com")
    opened.confirm_signup("user@example.com", token)
    opened.configure_agent_recipient("user@example.com", token, "agent@example.com")
    yield opened
    opened.close()


@pytest.mark.parametrize("answer,expected", [("YES", "linked"), ("NO", "answer_no")])
def test_agent_answer_correlates_by_reference_and_controls_booking_flag(store, answer, expected):
    check = store.ingest(mail("user@example.com", "BackBond check", check_body()))
    reported_at = datetime(2026, 10, 1, 10, 30, tzinfo=timezone.utc)
    answer_receipt = store.ingest(
        mail("agent@example.com", f"BackBond answer BB-1: {answer}", sent_at=reported_at)
    )
    assert answer_receipt.kind == "agent_answer"
    stored = store.connection.execute(
        "SELECT * FROM agent_email_answers WHERE answer_receipt_id=?",
        (answer_receipt.receipt_id,),
    ).fetchone()
    assert stored["answer_reported_at"] == "2026-10-01T10:30:00Z"
    assert stored["received_at"] == answer_receipt.received_at
    assert stored["channel"] == "agent_report"
    assert stored["authorship"] == "agent_reported"
    projected = store.receipts_for("user@example.com")[0]["agent_report"]
    assert projected["answer"] == answer
    assert projected["channel"] == "agent_report"
    confirmation = store.ingest(mail("user@example.com", "Fwd: Booking confirmed", confirmation_body()))
    assert store.link_confirmation(confirmation.receipt_id) == expected
    if answer == "NO":
        assert store.cancellation_reminder_payload(confirmation.receipt_id) == {
            "agent_recipient": "agent@example.com", "reference": "BB-1", "reason": "answer_no"
        }


def test_booking_without_reported_answer_is_flagged_for_cancellation_reminder(store):
    store.ingest(mail("user@example.com", "BackBond check", check_body()))
    confirmation = store.ingest(mail("user@example.com", "Fwd: Booking confirmed", confirmation_body()))
    assert store.link_confirmation(confirmation.receipt_id) == "answer_missing"
    assert store.cancellation_reminder_payload(confirmation.receipt_id)["agent_recipient"] == "agent@example.com"


def test_answer_received_after_confirmation_is_late_and_reminder_tracks_delivery(store):
    store.ingest(mail("user@example.com", "BackBond check", check_body()))
    confirmation = store.ingest(mail("user@example.com", "Fwd: Booking confirmed", confirmation_body()))
    store.ingest(mail(
        "agent@example.com", "BackBond answer BB-1: YES",
        sent_at=datetime(2026, 10, 1, 10, 30, tzinfo=timezone.utc),
    ))
    assert store.link_confirmation(confirmation.receipt_id) == "answer_late"
    store.record_reminder_delivery(confirmation.receipt_id, "delivery-1")
    stored = store.connection.execute(
        "SELECT reminder_delivery_id FROM booking_flags WHERE confirmation_receipt_id=?",
        (confirmation.receipt_id,),
    ).fetchone()
    assert stored["reminder_delivery_id"] == "delivery-1"


def test_unregistered_sender_cannot_report_agent_answer(store):
    store.ingest(mail("user@example.com", "BackBond check", check_body()))
    with pytest.raises(PermissionError, match="sender_not_confirmed"):
        store.ingest(mail("attacker@example.com", "BackBond answer BB-1: YES"))
