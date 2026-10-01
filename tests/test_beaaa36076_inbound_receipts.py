from email.message import EmailMessage

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
    return user_id, token


def test_confirmed_sender_check_is_written_as_demo_receipt(store):
    user_id, _ = confirm(store)
    receipt = store.ingest(message())
    assert receipt.user_id == user_id
    assert receipt.kind == "check"
    assert receipt.mode == "DEMO"
    assert store.receipts_for("ari@example.com")[0]["body_text"] == "Merchant confirmation #42"


def test_forwarded_confirmation_is_accepted_and_classified(store):
    confirm(store)
    receipt = store.ingest(message(subject="Fwd: BackBond check"))
    assert receipt.kind == "forwarded_confirmation"


def test_unconfirmed_sender_and_unrelated_subject_fail_closed(store):
    store.begin_signup("ari@example.com")
    with pytest.raises(PermissionError, match="sender_not_confirmed"):
        store.ingest(message())
    confirm(store)
    with pytest.raises(ValueError, match="subject_not_supported"):
        store.ingest(message(subject="Your bank statement"))


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
    _, token = confirm(store)
    check = store.ingest(message(body=linked_check_body()))
    store.record_authenticated_answer("ari@example.com", token, check.receipt_id, "OK")
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
    _, token = confirm(store)
    check = store.ingest(message(body=linked_check_body()))
    store.record_authenticated_answer("ari@example.com", token, check.receipt_id, "OK")
    confirmation = store.ingest(message(subject="Fwd: Booking confirmed", body=body))
    assert store.link_confirmation(confirmation.receipt_id) == "unlinked"
    assert store.receipts_for("ari@example.com")[1]["link_state"] == "unlinked"


def test_multiple_matching_checks_are_ambiguous_and_stay_unlinked(store):
    _, token = confirm(store)
    first = store.ingest(message(body=linked_check_body("BB-LINK-1")))
    second = store.ingest(message(body=linked_check_body("BB-LINK-2")))
    store.record_authenticated_answer("ari@example.com", token, first.receipt_id, "OK")
    store.record_authenticated_answer("ari@example.com", token, second.receipt_id, "OK")
    confirmation = store.ingest(
        message(subject="Fwd: Booking confirmed", body=confirmation_body())
    )
    assert store.link_confirmation(confirmation.receipt_id) == "unlinked"


@pytest.mark.parametrize("answer", [None, "STOP"])
def test_email_answer_text_cannot_authorize_confirmation_link(store, answer):
    _, token = confirm(store)
    check = store.ingest(message(body=linked_check_body()))
    if answer:
        store.record_authenticated_answer("ari@example.com", token, check.receipt_id, answer)
    confirmation = store.ingest(
        message(subject="Fwd: Booking confirmed", body=confirmation_body())
    )
    assert store.link_confirmation(confirmation.receipt_id) == "unlinked"
def test_authenticated_answer_is_immutable_and_retry_idempotent(store):
    _, token = confirm(store)
    check = store.ingest(message(body=linked_check_body()))
    first = store.record_authenticated_answer("ari@example.com", token, check.receipt_id, "OK")
    assert store.record_authenticated_answer(
        "ari@example.com", token, check.receipt_id, "OK"
    ) == first
    with pytest.raises(ValueError, match="answer_already_recorded"):
        store.record_authenticated_answer("ari@example.com", token, check.receipt_id, "STOP")


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
