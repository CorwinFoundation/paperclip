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


def test_new_issuance_does_not_invalidate_recipient_code(tmp_path):
    store = store_with_report(tmp_path)
    _, first = store.issue_report_code('a'*32, now=100)
    _, second = store.issue_report_code('a'*32, now=101)
    assert store.verify_report_code('a'*32, first, now=102)
    assert store.verify_report_code('a'*32, second, now=103)


def test_failed_verification_is_bounded_across_challenges(tmp_path):
    store = store_with_report(tmp_path)
    challenges = [store.issue_report_code('a'*32, request_key=f'foreign-{index}', now=100 + index)[1]
                  for index in range(3)]
    for index in range(10):
        challenge = challenges[index % 3]
        nonce, code = challenge.split('.')
        wrong = nonce + '.' + ('000000' if code != '000000' else '111111')
        assert store.verify_report_code('a'*32, wrong, request_key=f'foreign-{index % 3}', now=110 + index) is None
    # Ten guesses from other clients do not lock out the recipient's valid code.
    _, recipient = store.issue_report_code('a'*32, request_key='recipient', now=121)
    assert store.verify_report_code('a'*32, recipient, request_key='recipient', now=122)


def test_report_wide_failure_ceiling_does_not_lock_out_recipient(tmp_path):
    store = store_with_report(tmp_path)
    challenges = [store.issue_report_code('a'*32, request_key=f'foreign-{index // 5}', now=100 + index)[1]
                  for index in range(20)]
    _, recipient = store.issue_report_code('a'*32, request_key='recipient', now=121)

    for index, challenge in enumerate(challenges):
        nonce, code = challenge.split('.')
        wrong = nonce + '.' + ('000000' if code != '000000' else '111111')
        for attempt in range(5):
            assert store.verify_report_code('a'*32, wrong, request_key=f'foreign-{index // 5}',
                                            now=130 + index * 5 + attempt) is None

    assert store.db.execute(
        "SELECT COALESCE(sum(attempts),0) FROM report_verifications WHERE report_token=?",
        ('a'*32,)).fetchone()[0] == 100
    nonce, code = recipient.split('.')
    wrong = nonce + '.' + ('000000' if code != '000000' else '111111')
    assert store.verify_report_code('a'*32, wrong, request_key='foreign-extra', now=239) is None
    assert store.verify_report_code('a'*32, recipient, request_key='recipient', now=240)


def test_report_wide_failure_ceiling_still_exhausts_each_challenge(tmp_path):
    store = store_with_report(tmp_path)
    challenges = [store.issue_report_code('a'*32, request_key=f'foreign-{index // 5}', now=100 + index)[1]
                  for index in range(21)]
    for index, challenge in enumerate(challenges[:20]):
        nonce, code = challenge.split('.')
        wrong = nonce + '.' + ('000000' if code != '000000' else '111111')
        for attempt in range(5):
            assert store.verify_report_code('a'*32, wrong, now=130 + index * 5 + attempt) is None

    nonce, code = challenges[-1].split('.')
    wrong = nonce + '.' + ('000000' if code != '000000' else '111111')
    for attempt in range(5):
        assert store.verify_report_code('a'*32, wrong, now=240 + attempt) is None
    assert store.db.execute("SELECT attempts FROM report_verifications WHERE id=?", (nonce,)).fetchone()[0] == 5
    # The sixth guess is rejected without changing the exhausted budget, and
    # even the correct code can no longer revive this challenge.
    assert store.verify_report_code('a'*32, wrong, now=245) is None
    assert store.verify_report_code('a'*32, challenges[-1], now=246) is None
    assert store.db.execute("SELECT attempts FROM report_verifications WHERE id=?", (nonce,)).fetchone()[0] == 5


def test_forwarded_link_cannot_exhaust_recipient_issuance_budget(tmp_path):
    store = store_with_report(tmp_path)
    for index in range(5):
        assert store.issue_report_code('a'*32, request_key=f'foreign-{index}', now=100 + index)
    _, recipient = store.issue_report_code('a'*32, request_key='recipient', now=106)
    assert recipient


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


def test_real_viewer_uses_credentialed_verification_contract():
    source = (Path(__file__).resolve().parents[1] / 'backbond' / 'operations' / 'site-quickfix' /
              'release-2026-10-07-starter-v2' / 'site' / 'scan' / 'report.js').read_text()
    for required in ("credentials:'include'", "'/verification'", "'/session'", "method:'DELETE'",
                     'one-time-code', 'Email me a code', 'Sign out'):
        assert required in source
    assert "credentials:'omit'" not in source


def test_cors_allows_credentialed_viewer_and_logout():
    source = (Path(__file__).resolve().parents[1] / 'backbond' / 'scan-intake' / 'server' / 'app.py').read_text()
    assert 'allow_credentials=True' in source
    assert '"DELETE"' in source
    assert '"https://www.backbond.ai"' in source
    assert '"recipient_hint"' not in source
    assert "nonce = secrets.token_hex(16)" in source


def test_intake_unit_trusts_only_loopback_proxy_headers():
    source = (Path(__file__).resolve().parents[1] / 'backbond' / 'scan-intake' /
              'systemd' / 'backbond-scan-intake.service').read_text()
    assert '--proxy-headers' in source
    assert '--forwarded-allow-ips=127.0.0.1' in source
