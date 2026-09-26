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


def test_sender_identity_comes_from_normalized_from_address(store):
    confirm(store)
    receipt = store.ingest(message(sender="Ari <ARI@EXAMPLE.COM>"))
    assert receipt.sender_address == "ari@example.com"
