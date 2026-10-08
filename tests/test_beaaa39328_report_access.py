from datetime import datetime, timezone
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backbond" / "scan-intake" / "server"))
import qa_service


def store_with_report(tmp_path):
    store = qa_service.IntakeStore(str(tmp_path / "qa.sqlite"))
    store.db.execute("PRAGMA foreign_keys=OFF")
    store.db.execute("INSERT INTO scans(id,code,partner,email,agent_label,endpoint_host,transport,pack_version,created_at,status,report_token,tests_json,outcome) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     ('s1','c','p','Owner@Example.COM','agent','example.test','rest','starter-100@4',datetime.now(timezone.utc).isoformat(),'finished','a'*32,'[]','complete'))
    return store


def test_report_is_fail_closed_and_bound_to_report(tmp_path):
    store = store_with_report(tmp_path)
    assert not store.authorize_report('a'*32, None)
    _, challenge = store.issue_report_code('a'*32, now=100)
    session = store.verify_report_code('a'*32, challenge, now=101)
    assert session and store.authorize_report('a'*32, session, now=102)
    assert not store.authorize_report('b'*32, session, now=102)


def test_code_is_single_use_bounded_and_expires(tmp_path):
    store = store_with_report(tmp_path)
    _, challenge = store.issue_report_code('a'*32, now=100)
    assert store.verify_report_code('a'*32, challenge, now=101)
    assert store.verify_report_code('a'*32, challenge, now=102) is None
    _, expired = store.issue_report_code('a'*32, now=200)
    assert store.verify_report_code('a'*32, expired, now=801) is None


def test_session_idle_absolute_and_logout(tmp_path):
    store = store_with_report(tmp_path)
    _, challenge = store.issue_report_code('a'*32, now=100)
    session = store.verify_report_code('a'*32, challenge, now=101)
    assert not store.authorize_report('a'*32, session, now=702)
    store.revoke_report_session(session, now=200)
    assert not store.authorize_report('a'*32, session, now=201)


def test_historical_row_without_recipient_mapping_stays_blocked(tmp_path):
    store = store_with_report(tmp_path)
    store.db.execute("UPDATE scans SET email='' WHERE report_token=?", ('a'*32,))
    assert store.issue_report_code('a'*32, now=100) is None


def test_report_mutations_require_first_party_origin(tmp_path, monkeypatch):
    # Keep the policy unit-testable without importing the process-global FastAPI app.
    monkeypatch.setenv("QA_DB_PATH", str(tmp_path / "app.sqlite"))
    from app import _report_csrf_ok
    class Request:
        def __init__(self, headers):
            self.headers = headers
    assert _report_csrf_ok(Request({'origin': 'https://backbond.ai'}))
    assert _report_csrf_ok(Request({'referer': 'https://www.backbond.ai/scan/r/' + 'a' * 32}))
    assert not _report_csrf_ok(Request({}))
    assert not _report_csrf_ok(Request({'origin': 'https://evil.example'}))
