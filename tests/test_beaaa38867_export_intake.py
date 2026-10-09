import io
import hashlib
import hmac
import json
import runpy
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pytest

from stage_b_pilot.export_intake import (
    AUTO_REPORT_SUBJECT, EXPORT_RECIPIENT, STOP_REPORTING_SUBJECT,
    TASK_REPORT_SUBJECT, TRANSPORT_BODY, TRANSPORT_SUBJECT,
    ExportIntakeStore,
)
from stage_b_pilot.mailgun import make_handler as make_inbound_handler
from stage_b_pilot.receipt_page import make_handler as make_page_handler

NOW = datetime(2026, 10, 7, 20, 0, tzinfo=timezone.utc)
KEY = b"e" * 32
SIGNING_KEY = "export-intake-test-signing-key"


def transport_message():
    item = EmailMessage()
    item["From"] = "same-assistant@instinct.example"
    item["To"] = EXPORT_RECIPIENT
    item["Subject"] = TRANSPORT_SUBJECT
    item.set_content(TRANSPORT_BODY)
    return item.as_bytes()


def task_report_message(**changes):
    report = {
        "fictional": True,
        "evidence_origin": "agent_reported",
        "task_requested": "Find a fictional dinner option",
        "proposal": "Fictional table for two at 19:30",
        "price": "4800",
        "currency": "TRY",
        "cancellation_terms": "Free cancellation until 18:00",
        "owner_approval": "Owner approved in chat",
        "action_taken": "Fictional simulation only; nothing booked",
        "outcome": "Proposal recorded; no real action",
    }
    report.update(changes)
    item = EmailMessage()
    item["From"] = "same-assistant@instinct.example"
    item["To"] = EXPORT_RECIPIENT
    item["Subject"] = TASK_REPORT_SUBJECT
    item.set_content(json.dumps(report))
    return item.as_bytes()


def automatic_report_message(sequence, window_id="window_test_001", sender="same-assistant@instinct.example", **changes):
    report = {
        "fictional": True, "evidence_origin": "agent_reported",
        "window_id": window_id, "task_sequence": sequence,
        "window_hours": 24, "max_reports": 2, "recipient": EXPORT_RECIPIENT,
        "deployed_commit": "2d852919d0ee53dec8be0350584472b5d5482d22",
        "standing_rule_accepted_at": "2026-10-07T20:00:00Z",
        "account_separation_reported": True, "account_separation_note": "Separate test account",
        "standing_report_consistent": True, "standing_report_consistency_note": "Matches chat",
        "time_to_first_task_ms": 1200, "tester_feedback": "Short flow",
        "task_requested": f"New pretend task {sequence}",
        "proposal": f"Fictional proposal {sequence}", "price": str(100 * sequence),
        "currency": "TRY", "cancellation_terms": "Fictional free cancellation",
        "owner_approval": "Owner authorized two new fictional reports for 24 hours",
        "action_taken": "Planning only; no external action",
        "outcome": "Fictional result; nothing booked",
        "task_id": f"pretend-{sequence}", "task_started_at": "2026-10-07T20:01:00Z",
        "pre_action_check_ids": [{"check_id": f"check-{sequence}",
                                   "timestamp": "2026-10-07T20:01:01Z"}],
        "reported_answer": "Fictional answer", "reported_latency_ms": 800,
        "stop_count": 0, "executions_without_prior_check_count": 0,
        "forwarded_confirmation_count": 0, "displayed_outcome_match": "yes",
        "non_recoverable_result": "none", "display_defect": "none",
    }
    report.update(changes)
    item = EmailMessage()
    item["From"] = sender
    item["To"] = EXPORT_RECIPIENT
    item["Subject"] = AUTO_REPORT_SUBJECT
    item.set_content(json.dumps(report))
    return item.as_bytes()


def stop_message(window_id="window_test_001", sender="same-assistant@instinct.example"):
    item = EmailMessage()
    item["From"] = sender
    item["To"] = EXPORT_RECIPIENT
    item["Subject"] = STOP_REPORTING_SUBJECT
    item.set_content(json.dumps({"window_id": window_id, "command": "stop"}))
    return item.as_bytes()


def test_one_fictional_task_report_is_persisted_and_visible_read_only(tmp_path, monkeypatch, capsys):
    db = tmp_path / "history.sqlite3"
    store = ExportIntakeStore(db, encryption_key=KEY, now=lambda: NOW)
    probe_id = store.issue_transport_probe()
    store.ingest(transport_message(), envelope_recipient=EXPORT_RECIPIENT)
    report_id = store.ingest(task_report_message(), envelope_recipient=EXPORT_RECIPIENT)
    assert report_id.startswith("tr_")
    row = dict(store.connection.execute("SELECT * FROM fictional_task_reports").fetchone())
    assert row["received_at"] and row["evidence_origin"] == "agent_reported"
    assert row["probe_id"] == probe_id
    assert row["verification_state"] == "unverified"
    with pytest.raises(ValueError, match="already_received"):
        store.ingest(task_report_message(outcome="retry"), envelope_recipient=EXPORT_RECIPIENT)
    store.close()
    monkeypatch.setenv("BACKBOND_STAGE_B_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("BACKBOND_STAGE_B_NGINX_LOGS", str(tmp_path / "none"))
    monkeypatch.setenv("BACKBOND_STAGE_B_JOURNAL_CMD", "true")
    monkeypatch.setattr(sys, "argv", ["status.py", "2026-10-07T19:00", "2026-10-07T21:00"])
    with pytest.raises(SystemExit) as result:
        runpy.run_path("ops/stage-b-inbound/status.py", run_name="__main__")
    assert result.value.code == 0
    output = capsys.readouterr().out
    assert "fictional_task_reports total 1 in_window 1" in output
    assert f"transport_probe {probe_id} state accepted" in output
    assert "currency TRY" in output and "verification unverified" in output


def test_fictional_task_report_requires_matching_unexpired_probe_sender(tmp_path):
    store = ExportIntakeStore(tmp_path / "history.sqlite3", encryption_key=KEY, now=lambda: NOW)
    with pytest.raises(ValueError, match="sender_not_verified"):
        store.ingest(task_report_message(), envelope_recipient=EXPORT_RECIPIENT)
    store.issue_transport_probe()
    store.ingest(transport_message(), envelope_recipient=EXPORT_RECIPIENT)
    with pytest.raises(ValueError, match="sender_not_verified"):
        store.ingest(task_report_message().replace(
            b"same-assistant@instinct.example", b"other-assistant@instinct.example"
        ), envelope_recipient=EXPORT_RECIPIENT)


def test_two_automatic_reports_share_bounded_window_and_are_visible(tmp_path, monkeypatch, capsys):
    store = ExportIntakeStore(tmp_path / "history.sqlite3", encryption_key=KEY, now=lambda: NOW)
    probe_id = store.issue_transport_probe()
    store.ingest(transport_message(), envelope_recipient=EXPORT_RECIPIENT)
    first = store.ingest(automatic_report_message(1), envelope_recipient=EXPORT_RECIPIENT)
    second = store.ingest(automatic_report_message(2), envelope_recipient=EXPORT_RECIPIENT)
    assert first.startswith("ar_") and second.startswith("ar_")
    window = dict(store.connection.execute("SELECT * FROM fictional_reporting_windows").fetchone())
    assert window["probe_id"] == probe_id and window["state"] == "completed"
    assert window["max_reports"] == 2 and window["recipient"] == EXPORT_RECIPIENT
    assert window["account_separation_reported"] == 1
    assert store.connection.execute("SELECT count(*) FROM fictional_automatic_reports").fetchone()[0] == 2
    with pytest.raises(ValueError, match="window_inactive"):
        store.ingest(automatic_report_message(2), envelope_recipient=EXPORT_RECIPIENT)
    store.close()
    monkeypatch.setenv("BACKBOND_STAGE_B_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("BACKBOND_STAGE_B_NGINX_LOGS", str(tmp_path / "none"))
    monkeypatch.setenv("BACKBOND_STAGE_B_JOURNAL_CMD", "true")
    monkeypatch.setattr(sys, "argv", ["status.py", "2026-10-07T19:00", "2026-10-07T21:00"])
    with pytest.raises(SystemExit) as result:
        runpy.run_path("ops/stage-b-inbound/status.py", run_name="__main__")
    assert result.value.code == 0
    output = capsys.readouterr().out
    assert "fictional_reporting_window window_test_001" in output
    assert "fictional_automatic_reports total 2 in_window 2" in output


def test_stop_preserves_saved_report_and_blocks_future_report(tmp_path):
    store = ExportIntakeStore(tmp_path / "history.sqlite3", encryption_key=KEY, now=lambda: NOW)
    store.issue_transport_probe()
    store.ingest(transport_message(), envelope_recipient=EXPORT_RECIPIENT)
    store.ingest(automatic_report_message(1), envelope_recipient=EXPORT_RECIPIENT)
    assert store.ingest(stop_message(), envelope_recipient=EXPORT_RECIPIENT) == "window_test_001"
    window = dict(store.connection.execute("SELECT * FROM fictional_reporting_windows").fetchone())
    assert window["state"] == "stopped" and window["stopped_at"]
    assert store.connection.execute("SELECT count(*) FROM fictional_automatic_reports").fetchone()[0] == 1
    with pytest.raises(ValueError, match="window_inactive"):
        store.ingest(automatic_report_message(2), envelope_recipient=EXPORT_RECIPIENT)


def test_automatic_window_rejects_wrong_sender_sequence_scope_and_expiry(tmp_path):
    clock = [NOW]
    store = ExportIntakeStore(tmp_path / "history.sqlite3", encryption_key=KEY, now=lambda: clock[0])
    store.issue_transport_probe()
    store.ingest(transport_message(), envelope_recipient=EXPORT_RECIPIENT)
    with pytest.raises(ValueError, match="first_sequence_invalid"):
        store.ingest(automatic_report_message(2), envelope_recipient=EXPORT_RECIPIENT)
    with pytest.raises(ValueError, match="sender_not_verified"):
        store.ingest(automatic_report_message(1, sender="other@example.test"),
                     envelope_recipient=EXPORT_RECIPIENT)
    store.ingest(automatic_report_message(1), envelope_recipient=EXPORT_RECIPIENT)
    clock[0] += timedelta(hours=25)
    with pytest.raises(ValueError, match="window_expired"):
        store.ingest(automatic_report_message(2), envelope_recipient=EXPORT_RECIPIENT)


def test_automatic_report_rejects_invalid_observation_fields(tmp_path):
    store = ExportIntakeStore(tmp_path / "history.sqlite3", encryption_key=KEY, now=lambda: NOW)
    store.issue_transport_probe()
    store.ingest(transport_message(), envelope_recipient=EXPORT_RECIPIENT)
    with pytest.raises(ValueError, match="checks_invalid"):
        store.ingest(automatic_report_message(1, pre_action_check_ids=["bad"]),
                     envelope_recipient=EXPORT_RECIPIENT)
    with pytest.raises(ValueError, match="outcome_match_invalid"):
        store.ingest(automatic_report_message(1, displayed_outcome_match="maybe"),
                     envelope_recipient=EXPORT_RECIPIENT)


def test_existing_export_mailbox_accepts_one_transport_probe_without_history(tmp_path):
    store = ExportIntakeStore(tmp_path / "export.sqlite3", encryption_key=b"x" * 32)
    probe_id = store.issue_transport_probe()
    assert store.ingest(transport_message(), envelope_recipient=EXPORT_RECIPIENT) == probe_id
    status = store.transport_probe_status(probe_id)
    assert status["state"] == "accepted" and status["received_at"]
    assert status["sender_hash"] and "instinct.example" not in str(status)
    assert store.connection.execute("SELECT count(*) FROM export_intakes").fetchone()[0] == 0
    with pytest.raises(ValueError, match="not_armed"):
        store.ingest(transport_message(), envelope_recipient=EXPORT_RECIPIENT)
    store.close()


def message(code, rows):
    item = EmailMessage()
    item["From"] = "eric@example.test"
    item["To"] = EXPORT_RECIPIENT
    item["Subject"] = f"BackBond export {code}"
    item.set_content(json.dumps({"schema_version": "instinct-history/v1",
        "row_count": len(rows), "rows": rows, "end_marker": "END INSTINCT HISTORY"}))
    return item.as_bytes()


def row(task_id="task-1", **changes):
    value = {"task_id": task_id, "category": "fictional",
             "evidence_origin": "agent_reported", "synthetic": True}
    value.update(changes)
    return value


def test_e1_e2_valid_three_rows_status_and_single_use(tmp_path, monkeypatch, capsys):
    db = tmp_path / "history.sqlite3"
    store = ExportIntakeStore(db, encryption_key=KEY, now=lambda: NOW)
    code = store.issue_code()
    store.ingest(message(code, [row("one"), row("two"), row("three")]),
                 envelope_recipient=EXPORT_RECIPIENT)
    with pytest.raises(ValueError, match="export_code_used"):
        store.ingest(message(code, [row()]), envelope_recipient=EXPORT_RECIPIENT)
    store.close()
    monkeypatch.setenv("BACKBOND_STAGE_B_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("BACKBOND_STAGE_B_NGINX_LOGS", str(tmp_path / "none"))
    monkeypatch.setenv("BACKBOND_STAGE_B_JOURNAL_CMD", "true")
    monkeypatch.setattr(sys, "argv", ["status.py", "2026-10-07T19:00", "2026-10-07T21:00"])
    with pytest.raises(SystemExit) as result:
        runpy.run_path("ops/stage-b-inbound/status.py", run_name="__main__")
    assert result.value.code == 0
    out = capsys.readouterr().out
    assert "row_count 3 refs one two three" in out


def test_e3_unknown_and_expired(tmp_path):
    clock = [NOW]
    store = ExportIntakeStore(tmp_path / "h.sqlite3", encryption_key=KEY, now=lambda: clock[0])
    with pytest.raises(ValueError, match="export_code_unknown"):
        store.ingest(message("AAAAAA", [row()]), envelope_recipient=EXPORT_RECIPIENT)
    code = store.issue_code(); clock[0] += timedelta(hours=2)
    with pytest.raises(ValueError, match="export_code_expired"):
        store.ingest(message(code, [row()]), envelope_recipient=EXPORT_RECIPIENT)


@pytest.mark.parametrize(("change", "reason"), [
    ({"synthetic": False}, "export_row_not_synthetic:0"),
    ({"amount": 12}, "export_value_invalid:amount"),
])
def test_e4_e5_strict_values(tmp_path, change, reason):
    store = ExportIntakeStore(tmp_path / "h.sqlite3", encryption_key=KEY, now=lambda: NOW)
    code = store.issue_code()
    with pytest.raises(ValueError, match=reason):
        store.ingest(message(code, [row(**change)]), envelope_recipient=EXPORT_RECIPIENT)


def test_e6_account_free_schema(tmp_path):
    store = ExportIntakeStore(tmp_path / "h.sqlite3", encryption_key=KEY, now=lambda: NOW)
    code = store.issue_code(); store.ingest(message(code, [row()]), envelope_recipient=EXPORT_RECIPIENT)
    tables = {x[0] for x in store.connection.execute("select name from sqlite_master where type='table'")}
    assert tables == {"export_codes", "export_intakes", "transport_probes",
                      "fictional_task_reports", "fictional_reporting_windows",
                      "fictional_automatic_reports"}
    columns = {x[1] for x in store.connection.execute("pragma table_info(export_intakes)")}
    assert not columns.intersection({"account_id", "grant_id", "history_request_id", "email"})


@pytest.fixture
def http_servers(tmp_path):
    inbound_db = str(tmp_path / "inbound.sqlite3")
    history_db = str(tmp_path / "history.sqlite3")
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), make_inbound_handler(
        inbound_db, SIGNING_KEY, history_db_path=history_db, history_key=KEY))
    pages = ThreadingHTTPServer(("127.0.0.1", 0), make_page_handler(
        "127.0.0.1", upstream.server_port, "x" * 32,
        history_db=history_db, history_key=KEY))
    threads = [threading.Thread(target=server.serve_forever, daemon=True)
               for server in (upstream, pages)]
    for thread in threads:
        thread.start()
    try:
        yield (f"http://127.0.0.1:{upstream.server_port}",
               f"http://127.0.0.1:{pages.server_port}")
    finally:
        for server in (upstream, pages):
            server.shutdown(); server.server_close()
        for thread in threads:
            thread.join(timeout=5)


def _request(request):
    try:
        with urlopen(request, timeout=5) as response:
            return response.status, response.headers, response.read().decode()
    except HTTPError as error:
        return error.code, error.headers, error.read().decode()


def _signed_post(upstream, raw_message, nonce):
    timestamp = str(int(time.time()))
    signature = hmac.new(SIGNING_KEY.encode(), (timestamp + nonce).encode(),
                         hashlib.sha256).hexdigest()
    body = urlencode({"timestamp": timestamp, "token": nonce, "signature": signature,
                      "recipient": EXPORT_RECIPIENT,
                      "body-mime": raw_message.decode()}).encode()
    return _request(Request(upstream + "/history/mailgun/mime", data=body,
                            headers={"Content-Type": "application/x-www-form-urlencoded"},
                            method="POST"))


def _page_code(pages):
    status, headers, body = _request(Request(pages + "/stage-b-demo/export"))
    code = __import__("re").search(r"BackBond export ([A-Z2-9]{6})", body).group(1)
    return status, headers, body, code


def test_e2_e5_http_rejections_are_406_with_reason_codes(http_servers):
    upstream, pages = http_servers
    _, _, _, code = _page_code(pages)
    accepted = _signed_post(upstream, message(code, [row()]), "accepted")
    assert accepted[0] == 200
    used = _signed_post(upstream, message(code, [row("again")]), "used")
    assert used[0] == 406 and json.loads(used[2]) == {"error": "export_code_used"}

    unknown = _signed_post(upstream, message("AAAAAA", [row()]), "unknown")
    assert unknown[0] == 406 and json.loads(unknown[2]) == {"error": "export_code_unknown"}

    _, _, _, synthetic_code = _page_code(pages)
    not_synthetic = _signed_post(
        upstream, message(synthetic_code, [row(synthetic=False)]), "synthetic")
    assert not_synthetic[0] == 406
    assert json.loads(not_synthetic[2]) == {"error": "export_row_not_synthetic:0"}

    _, _, _, numeric_code = _page_code(pages)
    numeric = _signed_post(upstream, message(numeric_code, [row(amount=12)]), "numeric")
    assert numeric[0] == 406
    assert json.loads(numeric[2]) == {"error": "export_value_invalid:amount"}


def test_e7_export_page_issues_fresh_codes_without_cookie(http_servers):
    _upstream, pages = http_servers
    first = _page_code(pages)
    second = _page_code(pages)
    assert first[0] == second[0] == 200
    assert first[3] != second[3]
    for response in (first, second):
        headers = response[1]
        assert "no-store" in headers["Cache-Control"]
        assert headers.get("Set-Cookie") is None
        assert EXPORT_RECIPIENT in response[2]
