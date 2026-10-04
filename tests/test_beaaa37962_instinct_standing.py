from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
import hashlib
import hmac
import json
import threading
import time
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pytest

from stage_b_pilot.instinct_standing import EVENT_KEYS, SCHEMA_VERSION, StandingReportStore, render_instruction
from stage_b_pilot.inbound import InboundStore
from stage_b_pilot.history_import import HistoryImportStore
from stage_b_pilot.mailgun import make_handler
from stage_b_pilot.receipt_page import render_history


NOW = datetime(2026, 10, 3, 16, 0, tzinfo=timezone.utc)


def report(key="fictional-request-001", trigger="request_completed"):
    event = {field: "unknown" for field in EVENT_KEYS}
    event.update(category="fictional_booking", proposed_external_action="research only",
                 actual_external_action="none", money_at_risk="USD 0",
                 outcome="fictional options returned", evidence_origin="agent_reported",
                 synthetic=True)
    return {"schema_version": SCHEMA_VERSION, "deduplication_key": key,
            "trigger": trigger, "event": event}


def test_one_link_instruction_and_recurring_deduplicated_receipts(tmp_path):
    store = StandingReportStore(tmp_path / "standing.sqlite3", now=lambda: NOW)
    grant = store.issue("account-1", "ia5p01@mail.instinct.com")
    page = render_instruction(grant["token"], grant["expires_at"],
                              "ia5p01@mail.instinct.com", "https://scanner.backbond.ai/stage-b-demo",
                              "history+fictional@inbound.backbond.ai", "2026-10-03T17:00:00Z")
    assert grant["token"] in page and "multi-row onboarding history email" in page
    assert "approval_status" in page and "END INSTINCT HISTORY" in page
    assert "full message bodies" in page and "No polling" in page
    assert "deduplication_key" in page
    first = store.ingest(grant["token"], "ia5p01@mail.instinct.com", report())
    duplicate = store.ingest(grant["token"], "ia5p01@mail.instinct.com", report())
    assert first["state"] == "received" and first["review"] == "staff_required"
    assert duplicate == {"state": "duplicate", "report_id": first["report_id"], "review": "staff_required"}


def test_minimal_schema_sender_scope_expiry_and_revocation(tmp_path):
    clock = [NOW]
    store = StandingReportStore(tmp_path / "standing.sqlite3", now=lambda: clock[0])
    grant = store.issue("account-1", "ia5p01@mail.instinct.com", ttl=timedelta(minutes=5))
    with pytest.raises(PermissionError, match="sender_mismatch"):
        store.ingest(grant["token"], "someone@example.com", report())
    excessive = report(); excessive["event"]["full_message_body"] = "forbidden"
    with pytest.raises(ValueError, match="event_keys_invalid"):
        store.ingest(grant["token"], "ia5p01@mail.instinct.com", excessive)
    assert store.grants_for("account-1")[0]["grant_id"] == grant["grant_id"]
    store.revoke("account-1", grant["grant_id"])
    with pytest.raises(PermissionError, match="revoked_or_invalid"):
        store.ingest(grant["token"], "ia5p01@mail.instinct.com", report("request-2"))
    other = store.issue("account-1", "ia5p01@mail.instinct.com", ttl=timedelta(minutes=5))
    clock[0] += timedelta(minutes=6)
    with pytest.raises(PermissionError, match="expired"):
        store.ingest(other["token"], "ia5p01@mail.instinct.com", report("request-3"))


def test_account_bound_followup_reply_correlation_duplicate_expiry_and_cross_account(tmp_path):
    clock = [NOW]
    store = StandingReportStore(tmp_path / "standing.sqlite3", now=lambda: clock[0])
    first = store.issue("account-1", "ia5p01@mail.instinct.com", followup_enabled=True)
    second = store.issue("account-2", "other@mail.instinct.com", followup_enabled=True)
    followup = store.issue_followup("account-1", first["grant_id"],
                                    "What evidence supports the fictional hotel outcome?",
                                    ttl=timedelta(minutes=5))
    page = store.followup_page(first["token"])
    assert page["polling_enabled"] is True and page["authorization_required"] is False
    assert page["requests"][0]["request_id"] == followup["request_id"]
    reply = {"request_id": followup["request_id"],
             "answer": "Agent-reported fictional comparison result.",
             "evidence_origin": "agent_reported", "synthetic": True}
    with pytest.raises(PermissionError, match="cross_account"):
        store.reply_followup(second["token"], followup["request_id"], reply)
    assert store.reply_followup(first["token"], followup["request_id"], reply)["state"] == "received"
    assert store.reply_followup(first["token"], followup["request_id"], reply)["state"] == "duplicate"
    conflicting = {**reply, "answer": "Different answer must not overwrite the first."}
    with pytest.raises(ValueError, match="reply_conflict"):
        store.reply_followup(first["token"], followup["request_id"], conflicting)
    expiring = store.issue_followup("account-1", first["grant_id"], "Second question",
                                    ttl=timedelta(minutes=1))
    clock[0] += timedelta(minutes=2)
    with pytest.raises(PermissionError, match="expired"):
        store.reply_followup(first["token"], expiring["request_id"], reply)


def test_stage_b_routes_grant_instruction_email_receipt_revoke_and_missing(tmp_path):
    inbound_path = tmp_path / "inbound.sqlite3"
    standing_path = tmp_path / "standing.sqlite3"
    inbound = InboundStore(inbound_path)
    _, enrollment_token = inbound.begin_signup("user@example.com")
    inbound.confirm_signup("user@example.com", enrollment_token)
    inbound.configure_agent_recipient("user@example.com", enrollment_token,
                                      "ia5p01@mail.instinct.com")
    user_id = inbound.authenticate("user@example.com", enrollment_token)
    inbound.close()
    signing_key = "standing-mailgun-signing-key"
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(
        str(inbound_path), signing_key, standing_db_path=str(standing_path),
        standing_synthetic_enabled=True, history_db_path=str(tmp_path / "history.sqlite3"),
        history_key=b"history-encryption-key-32-bytes!!", history_synthetic_enabled=True,
        public_base="https://scanner.test/stage-b-demo",
    ))
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    base = f"http://127.0.0.1:{server.server_port}"

    def authenticated(path, fields):
        request = Request(base + path, data=json.dumps({"email": "user@example.com", **fields}).encode(),
                          headers={"Content-Type": "application/json",
                                   "Authorization": f"Bearer {enrollment_token}"})
        with urlopen(request, timeout=5) as response:
            return json.loads(response.read())

    try:
        issued = authenticated("/instinct/grant", {"consent": True, "synthetic": True,
                                                    "followup_consent": True})
        assert issued["synthetic"] is True and "instruction_url" in issued
        assert issued["history_address"].startswith("history+")
        with urlopen(base + "/instinct/instruction/" + issued["token"], timeout=5) as response:
            page = response.read().decode()
        assert issued["address"] in page and issued["history_address"] in page
        assert "approval_status" in page and "END INSTINCT HISTORY" in page
        assert "email delivery does not show webhook HTTP responses" in page
        assert "not an observed Instinct capability" in page
        assert "/instinct/followups/" in page and "no more than hourly" in page
        assert "request_id, answer, evidence_origin, and synthetic" in page

        followup = authenticated("/instinct/followups", {
            "grant_id": issued["grant_id"],
            "question": "What evidence supports the fictional outcome?",
        })
        with urlopen(base + "/instinct/followups/" + issued["token"], timeout=5) as response:
            assert response.headers["Cache-Control"] == "no-store"
            assert response.headers["Referrer-Policy"] == "no-referrer"
            queue = json.loads(response.read())
        assert queue["polling_enabled"] is True
        assert queue["requests"][0]["request_id"] == followup["request_id"]
        assert followup["reply_address"] == queue["requests"][0]["reply_address"]
        assert followup["reply_address"].startswith("history+followup-")
        reply_message = EmailMessage()
        reply_message["From"] = "ia5p01@mail.instinct.com"
        reply_message["To"] = followup["reply_address"]
        reply_message.set_content(json.dumps({"request_id": followup["request_id"],
                                               "answer": "Agent-reported fictional evidence.",
                                               "evidence_origin": "agent_reported", "synthetic": True}))
        timestamp, webhook_token = str(int(time.time())), "followup-route-test"
        signature = hmac.new(signing_key.encode(), (timestamp + webhook_token).encode(),
                             hashlib.sha256).hexdigest()
        followup_fields = {"timestamp": timestamp, "token": webhook_token, "signature": signature,
                           "recipient": followup["reply_address"], "body-mime": reply_message.as_string()}
        reply_request = Request(base + "/history/mailgun/mime",
                                data=urlencode(followup_fields).encode(),
                                headers={"Content-Type": "application/x-www-form-urlencoded"})
        with urlopen(reply_request, timeout=5) as response:
            assert json.loads(response.read())["state"] == "received"
        unsigned = dict(followup_fields); unsigned["signature"] = "bad"
        with pytest.raises(HTTPError) as rejected_reply:
            urlopen(Request(base + "/history/mailgun/mime", data=urlencode(unsigned).encode(),
                            headers={"Content-Type": "application/x-www-form-urlencoded"}), timeout=5)
        assert rejected_reply.value.code == 401

        message = EmailMessage()
        message["From"] = "ia5p01@mail.instinct.com"
        message["To"] = issued["address"]
        message["Subject"] = "BackBond fictional request report"
        message.set_content(json.dumps(report()))
        timestamp, webhook_token = str(int(time.time())), "route-test"
        signature = hmac.new(signing_key.encode(), (timestamp + webhook_token).encode(),
                             hashlib.sha256).hexdigest()
        fields = {"timestamp": timestamp, "token": webhook_token, "signature": signature,
                  "recipient": issued["address"], "body-mime": message.as_string()}
        request = Request(base + "/history/mailgun/mime", data=urlencode(fields).encode(),
                          headers={"Content-Type": "application/x-www-form-urlencoded"})
        with urlopen(request, timeout=5) as response:
            assert json.loads(response.read())["state"] == "received"
        mismatched = dict(fields)
        mismatched["recipient"] = "history+standing-different@inbound.backbond.ai"
        request = Request(base + "/history/mailgun/mime", data=urlencode(mismatched).encode(),
                          headers={"Content-Type": "application/x-www-form-urlencoded"})
        with pytest.raises(HTTPError) as rejected:
            urlopen(request, timeout=5)
        assert rejected.value.code == 401
        state = authenticated("/instinct/reports", {})
        assert state["reports"][0]["review"] == "staff_required"
        assert state["followups"][0]["state"] == "replied"
        assert state["followups"][0]["answer"] == "Agent-reported fictional evidence."
        assert state["grants"][0]["grant_id"] == issued["grant_id"]
        private_page = render_history({"onboarding": {}, "support": {}, "imports": [],
                                       "synthetic_enabled": True}, "user@example.com", "csrf-test",
                                      standing=state)
        assert issued["grant_id"] in private_page and issued["token"] not in private_page
        assert "90 days" in private_page and "24 hours" in private_page
        assert "followup_consent" in private_page and "/instinct/followups" in private_page
        assert "Agent-reported answer" in private_page and followup["request_id"] in private_page
        assert authenticated("/instinct/revoke", {"grant_id": issued["grant_id"]}) == {"state": "revoked"}
        assert authenticated("/instinct/reports", {})["grants"][0]["state"] == "revoked"
        history = HistoryImportStore(tmp_path / "history.sqlite3",
                                     provider_secret=signing_key.encode(),
                                     encryption_key=b"history-encryption-key-32-bytes!!")
        try:
            assert history.onboarding_state(user_id)["history_state"] == "revoked"
        finally:
            history.close()
        with pytest.raises(HTTPError) as revoked:
            urlopen(base + "/instinct/instruction/" + issued["token"], timeout=5)
        assert revoked.value.code == 410
        with pytest.raises(HTTPError) as missing:
            urlopen(base + "/instinct/instruction/missing", timeout=5)
        assert missing.value.code == 410
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=5)


def test_email_followup_scope_expiry_conflicts_and_retained_private_history(tmp_path):
    clock = [NOW]
    store = StandingReportStore(tmp_path / "standing.sqlite3", now=lambda: clock[0])
    ordinary = store.issue("a", "agent@example.com")
    with pytest.raises(PermissionError, match="not_authorized"):
        store.followup_page(ordinary["token"])
    with pytest.raises(PermissionError, match="not_authorized"):
        store.issue_followup("a", ordinary["grant_id"], "Synthetic question")
    grant = store.issue("a", "agent@example.com", followup_enabled=True,
                        ttl=timedelta(hours=2))
    question = store.issue_followup("a", grant["grant_id"], "Fictional hotel?")
    assert question["expires_at"] == grant["expires_at"]
    with pytest.raises(PermissionError, match="cross_account"):
        store.issue_followup("b", grant["grant_id"], "Other account")

    def email(answer="Fictional answer", sender="agent@example.com"):
        message = EmailMessage()
        message["From"] = sender
        message["To"] = question["reply_address"]
        message.set_content(json.dumps({"request_id": question["request_id"],
                                       "answer": answer, "evidence_origin": "agent_reported",
                                       "synthetic": True}))
        return message.as_bytes()

    address = question["reply_address"]
    with pytest.raises(PermissionError, match="sender_mismatch"):
        store.ingest_followup_email(email(sender="other@example.com"), envelope_recipient=address)
    with pytest.raises(PermissionError, match="envelope_recipient_mismatch"):
        store.ingest_followup_email(email(), envelope_recipient="history+followup-wrong@inbound.backbond.ai")
    assert store.ingest_followup_email(email(), envelope_recipient=address)["state"] == "received"
    assert store.ingest_followup_email(email(), envelope_recipient=address)["state"] == "duplicate"
    with pytest.raises(ValueError, match="reply_conflict"):
        store.ingest_followup_email(email("Changed answer"), envelope_recipient=address)
    assert store.followups_for("a")[0]["answer"] == "Fictional answer"
    assert store.followups_for("b") == []
    pending = store.issue_followup("a", grant["grant_id"], "Second fictional question")
    store.revoke("a", grant["grant_id"])
    with pytest.raises(PermissionError, match="revoked"):
        store.ingest_followup_email(email(), envelope_recipient=address)
    records = store.followups_for("a")
    assert next(row for row in records if row["request_id"] == pending["request_id"])["state"] == "revoked"
    page = render_history({}, "a@example.com", "csrf", standing={
        "grants": store.grants_for("a"), "followups": records})
    assert "Fictional answer" not in page and question["request_id"] in page

    grant = store.issue("a", "agent@example.com", followup_enabled=True, ttl=timedelta(minutes=1))
    question = store.issue_followup("a", grant["grant_id"], "Expiring question")
    clock[0] += timedelta(minutes=2)
    with pytest.raises(PermissionError, match="expired"):
        store.ingest_followup_email(email(), envelope_recipient=question["reply_address"])
    store.close()
