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
        issued = authenticated("/instinct/grant", {"consent": True, "synthetic": True})
        assert issued["synthetic"] is True and "instruction_url" in issued
        assert issued["history_address"].startswith("history+")
        with urlopen(base + "/instinct/instruction/" + issued["token"], timeout=5) as response:
            page = response.read().decode()
        assert issued["address"] in page and issued["history_address"] in page
        assert "approval_status" in page and "END INSTINCT HISTORY" in page
        assert "email delivery does not show webhook HTTP responses" in page
        assert "not an observed Instinct capability" in page

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
        assert state["grants"][0]["grant_id"] == issued["grant_id"]
        private_page = render_history({"onboarding": {}, "support": {}, "imports": [],
                                       "synthetic_enabled": True}, "user@example.com", "csrf-test",
                                      standing=state)
        assert issued["grant_id"] in private_page and issued["token"] not in private_page
        assert "90 days" in private_page and "24 hours" in private_page
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
