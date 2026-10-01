from email import policy
from email.message import EmailMessage
from email.parser import BytesParser

import pytest

from stage_b_pilot.inbound import InboundStore


def message(sender="ari@example.com", subject="BackBond check", body="Merchant confirmation #42"):
    item = EmailMessage()
    item["From"] = sender
    item["To"] = "check@inbound.backbond.ai"
    item["Subject"] = subject
    item.set_content(body)
    return item.as_bytes()


@pytest.fixture
def store(tmp_path):
    opened = InboundStore(tmp_path / "inbound.sqlite3")
    yield opened
    opened.close()


def confirm(store, address="ari@example.com"):
    user_id, token = store.begin_signup(address)
    assert store.confirm_signup(address, token) == user_id
    return user_id


def test_confirmed_sender_check_is_written_as_demo_receipt(store):
    user_id = confirm(store)
    receipt = store.ingest(message())
    assert receipt.user_id == user_id
    assert receipt.kind == "check"
    assert receipt.mode == "DEMO"
    assert store.receipts_for("ari@example.com")[0]["body_text"] == "Merchant confirmation #42"


def test_forwarded_confirmation_is_accepted_and_classified(store):
    confirm(store)
    receipt = store.ingest(message(subject="Fwd: BackBond check"))
    assert receipt.kind == "forwarded_confirmation"
    assert receipt.link_state == "unlinked"
    assert receipt.dkim_result == "missing"


def test_unconfirmed_sender_and_unrelated_subject_fail_closed(store):
    store.begin_signup("ari@example.com")
    with pytest.raises(PermissionError, match="sender_not_confirmed"):
        store.ingest(message())
    confirm(store)
    with pytest.raises(ValueError, match="subject_not_supported"):
        store.ingest(message(subject="Your bank statement"))


def test_automatic_forward_accepts_arbitrary_merchant_subject(store):
    confirm(store)
    receipt = store.ingest(message(subject="Fwd: Your booking at Example Hotel"))
    assert receipt.kind == "forwarded_confirmation"
    assert receipt.link_state == "unlinked"


def test_eml_original_and_dkim_result_are_persisted_without_public_raw_bytes(tmp_path):
    opened = InboundStore(tmp_path / "inbound.sqlite3", dkim_verifier=lambda _: True)
    confirm(opened)
    inner = EmailMessage(policy=policy.SMTP)
    inner["From"] = "Merchant <confirm@merchant.example>"
    inner["To"] = "ari@example.com"
    inner["Subject"] = "Booking confirmed"
    inner["DKIM-Signature"] = "v=1; d=merchant.example; s=mail; b=abc"
    inner.set_content("Merchant: Example Hotel\nAmount: USD 25.00")
    outer = EmailMessage(policy=policy.SMTP)
    outer["From"] = "ari@example.com"
    outer["To"] = "check@inbound.backbond.ai"
    outer["Subject"] = "Fwd: Booking confirmed"
    outer.set_content("Automatically forwarded")
    outer.add_attachment(
        BytesParser(policy=policy.SMTP).parsebytes(inner.as_bytes()), filename="booking.eml"
    )
    receipt = opened.ingest(outer.as_bytes())
    assert receipt.dkim_result == "verified"
    assert receipt.dkim_signing_domain == "merchant.example"
    stored = opened.connection.execute(
        "SELECT raw_original FROM inbound_receipts WHERE receipt_id=?", (receipt.receipt_id,)
    ).fetchone()[0]
    assert BytesParser(policy=policy.default).parsebytes(stored)["Subject"] == "Booking confirmed"
    assert "raw_original" not in opened.receipts_for("ari@example.com")[0]
    opened.close()


def test_provider_retry_is_idempotent(store):
    confirm(store)
    raw = message()
    assert store.ingest(raw).receipt_id == store.ingest(raw).receipt_id
    assert len(store.receipts_for("ari@example.com")) == 1


def test_explicit_check_id_is_persisted_and_retry_safe(store):
    confirm(store)
    raw = message(body="Check ID: BB-42\nProposed action: Reserve a lamp")
    assert store.ingest(raw).correlation_key == "BB-42"
    assert store.ingest(raw).correlation_key == "BB-42"
    assert len(store.receipts_for("ari@example.com")) == 1


def test_legacy_test_reference_is_parsed_for_ingest_and_legacy_null_rows(store):
    confirm(store)
    receipt = store.ingest(message(body="Test reference: BB-LEGACY-01\nProposed action: Test"))
    assert receipt.correlation_key == "BB-LEGACY-01"
    store.connection.execute(
        "UPDATE inbound_receipts SET correlation_key=NULL WHERE receipt_id=?", (receipt.receipt_id,)
    )
    store.connection.commit()
    assert store.receipts_for("ari@example.com")[0]["correlation_key"] == "BB-LEGACY-01"


def test_conflicting_lines_and_reused_check_ids_fail_closed(store):
    confirm(store)
    with pytest.raises(ValueError, match="correlation_identifier_conflict"):
        store.ingest(message(body="Check ID: BB-ONE\nTest reference: BB-TWO"))
    raw = message(body="Test reference: BB-REUSE\nProposed action: First")
    store.ingest(raw)
    assert store.ingest(raw).correlation_key == "BB-REUSE"
    with pytest.raises(ValueError, match="check_id_reused"):
        store.ingest(message(body="Test reference: BB-REUSE\nProposed action: Second"))


def test_sender_identity_comes_from_normalized_from_address(store):
    confirm(store)
    receipt = store.ingest(message(sender="Ari <ARI@EXAMPLE.COM>"))
    assert receipt.sender_address == "ari@example.com"


def linked_check_body(reference="BB-LINK-1", merchant="Example Hotel", amount="USD 25.00"):
    return (
        f"Check ID: {reference}\nMerchant: {merchant}\nAmount: {amount}\n"
        "Window start: 2026-10-01T10:00:00Z\n"
        "Window end: 2026-10-01T12:00:00Z\nYour answer: Approved"
    )


def confirmation_body(merchant="Example Hotel", amount="USD 25.00", date="2026-10-01T11:00:00Z"):
    return f"Merchant: {merchant}\nAmount: {amount}\nDate: {date}"


def test_confirmation_links_only_one_approved_check_by_merchant_date_and_amount(store):
    confirm(store)
    check = store.ingest(message(body=linked_check_body()))
    confirmation = store.ingest(
        message(subject="Fwd: Booking confirmed", body=confirmation_body())
    )
    assert store.link_confirmation(confirmation.receipt_id) == "linked"
    row = store.receipts_for("ari@example.com")[1]
    assert row["link_state"] == "linked"
    assert row["linked_check_receipt_id"] == check.receipt_id


@pytest.mark.parametrize(
    "body",
    [
        confirmation_body(merchant="Other Hotel"),
        confirmation_body(amount="USD 26.00"),
        confirmation_body(date="2026-10-01T13:00:00Z"),
    ],
)
def test_nonmatching_confirmation_stays_unlinked(store, body):
    confirm(store)
    store.ingest(message(body=linked_check_body()))
    confirmation = store.ingest(message(subject="Fwd: Booking confirmed", body=body))
    assert store.link_confirmation(confirmation.receipt_id) == "unlinked"
    assert store.receipts_for("ari@example.com")[1]["link_state"] == "unlinked"


def test_multiple_matching_checks_are_ambiguous_and_stay_unlinked(store):
    confirm(store)
    store.ingest(message(body=linked_check_body("BB-LINK-1")))
    store.ingest(message(body=linked_check_body("BB-LINK-2")))
    confirmation = store.ingest(
        message(subject="Fwd: Booking confirmed", body=confirmation_body())
    )
    assert store.link_confirmation(confirmation.receipt_id) == "unlinked"


def test_user_can_classify_unlinked_confirmation_as_own_or_skipped_check(store):
    confirm(store)
    own = store.ingest(message(subject="Fwd: Own booking", body=confirmation_body()))
    assert store.classify_unlinked("ari@example.com", own.receipt_id, "own_booking") == "own_booking"
    skipped = store.ingest(
        message(subject="Fwd: Agent booking", body=confirmation_body(amount="USD 30.00"))
    )
    assert (
        store.classify_unlinked("ari@example.com", skipped.receipt_id, "agent_booking")
        == "skipped_check"
    )
    with pytest.raises(ValueError, match="confirmation_already_linked"):
        store.classify_unlinked("ari@example.com", skipped.receipt_id, "own_booking")
