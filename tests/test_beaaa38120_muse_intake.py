from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import format_datetime

import pytest

from stage_b_pilot.inbound import InboundStore
from stage_b_pilot.receipt_page import project_receipts, render_receipts


def mail(sender, body, *, when):
    message = EmailMessage()
    message["From"] = sender
    message["To"] = "check@inbound.backbond.ai"
    message["Subject"] = "BackBond check"
    message["Date"] = format_datetime(when)
    message.set_content(body)
    return message.as_bytes()


def test_muse_same_subject_answer_is_normalized_isolated_and_rendered(tmp_path):
    store = InboundStore(tmp_path / "muse.sqlite3")
    _, muse_token = store.begin_signup("muse@example.com", agent_label="Muse")
    store.confirm_signup("muse@example.com", muse_token)
    _, instinct_token = store.begin_signup("instinct@example.com")
    store.confirm_signup("instinct@example.com", instinct_token)
    check_time = datetime(2026, 10, 4, 10, 0, tzinfo=timezone.utc)
    answer_time = datetime(2026, 10, 4, 10, 5, tzinfo=timezone.utc)
    store.ingest(mail("muse@example.com", "Check ID: MUSE-1\nProposed action: Demo only", when=check_time))
    answer = store.ingest(mail("muse@example.com", "Check ID: MUSE-1\nAnswer: OK", when=answer_time))
    assert answer.kind == "agent_answer"
    muse_rows = store.receipts_for("muse@example.com")
    assert muse_rows[0]["agent_label"] == "Muse"
    assert muse_rows[0]["agent_report"]["answer"] == "YES"
    page = render_receipts(project_receipts(muse_rows))
    assert "<dt>Agent</dt><dd>Muse</dd>" in page
    assert "<dt>Reported by your agent</dt><dd>YES</dd>" in page
    assert "2026-10-04T10:05:00Z" in page
    assert store.receipts_for("instinct@example.com") == []
    with pytest.raises(ValueError, match="answer_check_not_found"):
        store.ingest(mail("instinct@example.com", "Check ID: MUSE-1\nAnswer: STOP", when=answer_time))


@pytest.mark.parametrize(("raw", "normalized"), [("STOP", "NO"), ("YES", "YES"), ("NO", "NO")])
def test_muse_answer_vocabulary(raw, normalized, tmp_path):
    store = InboundStore(tmp_path / f"{raw}.sqlite3")
    _, token = store.begin_signup("muse@example.com", agent_label="Muse")
    store.confirm_signup("muse@example.com", token)
    when = datetime(2026, 10, 4, 10, 0, tzinfo=timezone.utc)
    store.ingest(mail("muse@example.com", "Check ID: MUSE-2", when=when))
    store.ingest(mail("muse@example.com", f"Check ID: MUSE-2\nAnswer: {raw}", when=when))
    assert store.receipts_for("muse@example.com")[0]["agent_report"]["answer"] == normalized
