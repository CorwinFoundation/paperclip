"""Runtime proofs for BEAAA-37906 binding, retention, and deletion wiring."""

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

from stage_b_pilot.history_import import HistoryImportStore, REPORT_VERSION, ROW_KEYS
from stage_b_pilot.inbound import InboundStore
from stage_b_pilot.instinct_standing import EVENT_KEYS, SCHEMA_VERSION, StandingReportStore
from stage_b_pilot.mailgun import ingest_history_route, make_handler


KEY = b"history-encryption-key-32-bytes!!"
SIGNING = "runtime-controls-signing-key"
NOW = datetime(2026, 10, 4, tzinfo=timezone.utc)


def _report():
    row = {key: "unknown" for key in ROW_KEYS}
    row.update(evidence_origin="agent_reported", synthetic=True)
    return {"schema_version": REPORT_VERSION, "row_count": 1, "rows": [row],
            "end_marker": "END INSTINCT HISTORY"}


def _message(address):
    message = EmailMessage()
    message["From"] = "agent@example.com"
    message["To"] = address
    message.set_content(json.dumps(_report()))
    return message


def test_mailgun_route_passes_stored_binding_to_ingest():
    class Store:
        def binding_for_address(self, address):
            return {"user_id": "u", "tenant_id": "t", "account_id": "a", "agent_id": "g"}

        def ingest(self, raw, **kwargs):
            assert kwargs == {"transport_authenticated": True, "expected_user_id": "u",
                              "expected_tenant_id": "t", "expected_account_id": "a",
                              "expected_agent_id": "g"}
            return "preview"

    message = _message("history+MixedCase@inbound.backbond.ai")
    timestamp, token = str(int(time.time())), "route-binding"
    fields = {"timestamp": timestamp, "token": token,
              "signature": hmac.new(SIGNING.encode(), (timestamp + token).encode(),
                                    hashlib.sha256).hexdigest(),
              "recipient": message["To"], "body-mime": message.as_string()}
    assert ingest_history_route(Store(), fields, SIGNING) == "preview"


def test_store_open_backfills_legacy_expiry_and_runtime_route_invokes_cleanup(tmp_path, monkeypatch):
    history_path = tmp_path / "history.sqlite3"
    store = HistoryImportStore(history_path, provider_secret=SIGNING.encode(), encryption_key=KEY,
                               now=lambda: NOW)
    issued = store.issue_address("user", scope={"lookback_days": 7}, synthetic=True)
    store.ingest(_message(issued.address).as_bytes(), transport_authenticated=True)
    store.connection.execute("UPDATE history_previews SET retention_expires_at=''")
    store.connection.execute("UPDATE history_events SET retention_expires_at=''")
    store.connection.commit(); store.close()
    reopened = HistoryImportStore(history_path, provider_secret=SIGNING.encode(), encryption_key=KEY,
                                  now=lambda: NOW)
    assert all(reopened.connection.execute(
        "SELECT retention_expires_at!='' FROM history_previews UNION ALL "
        "SELECT retention_expires_at!='' FROM history_events"
    ).fetchone())
    reopened.close()

    inbound_path = tmp_path / "inbound.sqlite3"
    inbound = InboundStore(inbound_path)
    _, enrollment = inbound.begin_signup("user@example.com")
    inbound.confirm_signup("user@example.com", enrollment)
    inbound.configure_agent_recipient("user@example.com", enrollment, "agent@example.com")
    inbound.close()
    calls = []
    original = HistoryImportStore.purge_expired

    def observed(self):
        calls.append(True)
        return original(self)

    monkeypatch.setattr(HistoryImportStore, "purge_expired", observed)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(
        str(inbound_path), SIGNING, history_db_path=str(history_path), history_key=KEY,
        history_synthetic_enabled=True))
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        request = Request(f"http://127.0.0.1:{server.server_port}/history/state?email=user%40example.com",
                          headers={"Authorization": f"Bearer {enrollment}"})
        with urlopen(request, timeout=5):
            pass
        assert calls == [True]
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=5)


def test_cross_binding_rejected_and_standing_revoke_cascades_all_content(tmp_path):
    history = HistoryImportStore(tmp_path / "history.sqlite3", provider_secret=SIGNING.encode(),
                                 encryption_key=KEY)
    issued = history.issue_address("u", tenant_id="t", account_id="a", agent_id="g",
                                   scope={"lookback_days": 7}, synthetic=True)
    preview = history.ingest(_message(issued.address).as_bytes(), transport_authenticated=True,
                             expected_user_id="u", expected_tenant_id="t",
                             expected_account_id="a", expected_agent_id="g")
    with pytest.raises(PermissionError, match="preview_not_found"):
        history.act("u", preview, "delete", tenant_id="t", account_id="other", agent_id="g")
    history.act("u", preview, "delete", tenant_id="t", account_id="a", agent_id="g")
    assert history.connection.execute("SELECT count(*) FROM history_previews").fetchone()[0] == 0

    standing = StandingReportStore(tmp_path / "standing.sqlite3")
    grant = standing.issue("u", "agent@example.com", followup_enabled=True)
    event = {key: "unknown" for key in EVENT_KEYS}
    event.update(evidence_origin="agent_reported", synthetic=True)
    standing.ingest(grant["token"], "agent@example.com", {
        "schema_version": SCHEMA_VERSION, "deduplication_key": "runtime-proof",
        "trigger": "request_completed", "event": event})
    standing.issue_followup("u", grant["grant_id"], "Synthetic question")
    standing.revoke("u", grant["grant_id"])
    assert len(standing.reports_for("u")) == 1  # STOP retains received reports
    retained = standing.followups_for("u")
    assert retained[0]["state"] == "revoked" and retained[0]["answer"] is None


def test_standing_legacy_expiry_backfill_and_cleanup(tmp_path):
    clock = [NOW]
    path = tmp_path / "standing.sqlite3"
    store = StandingReportStore(path, now=lambda: clock[0],
                                content_retention=timedelta(hours=1))
    grant = store.issue("u", "agent@example.com", followup_enabled=True)
    event = {key: "unknown" for key in EVENT_KEYS}
    event.update(evidence_origin="agent_reported", synthetic=True)
    store.ingest(grant["token"], "agent@example.com", {
        "schema_version": SCHEMA_VERSION, "deduplication_key": "legacy-proof",
        "trigger": "request_completed", "event": event})
    store.issue_followup("u", grant["grant_id"], "Synthetic question")
    store.connection.execute("UPDATE instinct_standing_reports SET retention_expires_at=''")
    store.connection.execute("UPDATE instinct_followup_requests SET retention_expires_at=''")
    store.connection.commit(); store.close()
    reopened = StandingReportStore(path, now=lambda: clock[0],
                                   content_retention=timedelta(hours=1))
    clock[0] += timedelta(hours=2)
    assert reopened.purge_expired() == {"reports_deleted": 1, "followups_deleted": 1}
