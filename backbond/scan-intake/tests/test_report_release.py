import hashlib
import importlib
import json
import os
import re
from datetime import datetime
import sys
import tempfile
from pathlib import Path
import pytest
from fastapi.testclient import TestClient  # before httpx is patched in app_env
sys.path.insert(0, str(Path(__file__).parents[1] / 'server'))
import report_release
from report_release import ApprovalRefused, ReleaseError, ReportRelease, RevisionTampered

# BEAAA-42596: the Operations workflow sends the final report to this address; intake never does.
INFO = 'info@backbond.ai'
LEAD = 'lead-agent'
ANALYST = 'analyst-agent'
REVIEWER = 'reviewer-agent'
SUMMARY = 'Analyst summary: the agent refused the credential-exfiltration probes it was given.'
FROZEN_INPUT = {'schema': 'starter_report_input@1', 'case_id': 'case-1', 'request_id': 'r1', 'scan_id': 's1',
                'evidence_sha256': 'e' * 64, 'outcome': 'complete', 'pack_version': 'starter-100@3',
                'results': [{'ordinal': 1, 'verdict': 'pass'}],
                'verdict_counts': {'pass': 1, 'fail': 0, 'could_not_run': 0},
                'comparison': {'status': 'not_applicable', 'reason': 'starter_single_scan'},
                'retest': {'status': 'not_applicable', 'reason': 'starter_single_scan'},
                'operations_freeze': {'supported': False, 'missing': ['comparison_id', 'retest_completed']}}
CONTENT = {'report_input': FROZEN_INPUT, 'input_sha256': hashlib.sha256(report_release.canonical(
    FROZEN_INPUT).encode()).hexdigest(), 'authored': {'summary': SUMMARY, 'recommendations': []}}
PRIVATE_TESTS = [{'id': 't1', 'verdict': 'pass', 'prompt': 'PRIVATE-PROMPT-1', 'reply': 'PRIVATE-REPLY-1'},
                 {'id': 't2', 'verdict': 'fail', 'prompt': 'PRIVATE-PROMPT-2', 'reply': 'PRIVATE-REPLY-2',
                  'reason': 'PRIVATE-REASON-2'}]


SEND_SURFACE = ('request_send', 'jobs', 'claim', 'hold_expired', 'finalize_from_receipt', 'accepted', 'reconcile',
                'status')


def rel(tmp_path, lead=LEAD):
    return ReportRelease(str(tmp_path / 'release.sqlite'), lead_actor_id=lead)


def nonce(i=0):
    return f'nonce-{i:016d}'


def reviewed(r, rev, i=0):
    """Independent reviewer step every Lead approval now requires (nonces kept apart from Lead ones)."""
    r.review(rev['revision_id'], rev['revision_sha256'], REVIEWER, nonce(5000 + i))
    return rev


def approved(r, content=CONTENT, i=0):
    rev = reviewed(r, r.create_revision('s1', content, author_actor=ANALYST), i)
    r.approve(rev['revision_id'], rev['revision_sha256'], LEAD, nonce(i))
    return rev


def test_wrong_revision_or_digest_rejected(tmp_path):
    r = rel(tmp_path); rev = r.create_revision('s1', CONTENT, author_actor=ANALYST)
    with pytest.raises(ApprovalRefused, match='digest_mismatch'):
        r.approve(rev['revision_id'], '0' * 64, LEAD, nonce(1))
    with pytest.raises(ApprovalRefused, match='unknown_revision'):
        r.approve('rev_' + '0' * 32, rev['revision_sha256'], LEAD, nonce(2))
    assert not r.approval_valid(rev['revision_id'])


def test_stale_approval_rejected(tmp_path):
    r = rel(tmp_path); old = r.create_revision('s1', CONTENT, author_actor=ANALYST)
    new = r.create_revision('s1', dict(CONTENT, edited=True), author_actor=ANALYST)
    with pytest.raises(ApprovalRefused, match='stale_revision'):
        r.approve(old['revision_id'], old['revision_sha256'], LEAD, nonce(1))
    assert not r.approval_valid(old['revision_id']) and not r.released('s1')
    assert r.read_revision(new['revision_id'])['current']


def test_wrong_actor_and_unconfigured_lead_rejected(tmp_path):
    r = rel(tmp_path); rev = r.create_revision('s1', CONTENT, author_actor=ANALYST)
    with pytest.raises(ApprovalRefused, match='wrong_actor'):
        r.approve(rev['revision_id'], rev['revision_sha256'], 'someone-else', nonce(1))
    with pytest.raises(ApprovalRefused, match='lead_unconfigured'):
        ReportRelease(str(tmp_path / 'release.sqlite'), lead_actor_id='').approve(
            rev['revision_id'], rev['revision_sha256'], '', nonce(2))
    assert not r.approval_valid(rev['revision_id'])


def test_replayed_nonce_rejected(tmp_path):
    r = rel(tmp_path); rev = approved(r)
    with pytest.raises(ApprovalRefused, match='nonce_replayed'):
        r.approve(rev['revision_id'], rev['revision_sha256'], LEAD, nonce(0), 'reject')
    assert r.approval_valid(rev['revision_id'])
    new = r.create_revision('s1', dict(CONTENT, v=2), author_actor=ANALYST)
    with pytest.raises(ApprovalRefused, match='nonce_replayed'):
        r.approve(new['revision_id'], new['revision_sha256'], LEAD, nonce(0))


def test_tampered_content_fails_closed(tmp_path):
    r = rel(tmp_path); rev = approved(r)
    with pytest.raises(Exception, match='immutable'):
        r.db.execute("UPDATE release_revisions SET content_json='{}'")
    r.db.execute('DROP TRIGGER release_revisions_immutable')
    r.db.execute("UPDATE release_revisions SET content_json='{\"forged\":1}'")
    with pytest.raises(RevisionTampered): r.read_revision(rev['revision_id'])
    assert not r.approval_valid(rev['revision_id']) and not r.released('s1')
    with pytest.raises(ApprovalRefused, match='revision_tampered'):
        r.approve(rev['revision_id'], rev['revision_sha256'], LEAD, nonce(9))


def test_reject_then_new_revision_reapprove_releases_without_any_send(tmp_path):
    r = rel(tmp_path); rev = r.create_revision('s1', CONTENT, author_actor=ANALYST)
    assert r.approve(rev['revision_id'], rev['revision_sha256'], LEAD, nonce(1), 'reject')['decision'] == 'rejected'
    assert not r.released('s1')
    new = reviewed(r, r.create_revision('s1', dict(CONTENT, fixed=True), author_actor=ANALYST), 2)
    r.approve(new['revision_id'], new['revision_sha256'], LEAD, nonce(2))
    assert r.released('s1') and r.approval_valid(new['revision_id'])
    tables = {n for (n,) in r.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert not tables & {'release_sends', 'release_receipts'}  # no send outbox, no receipts

def test_edit_after_approval_voids_release(tmp_path):
    r = rel(tmp_path); rev = approved(r)
    assert r.released('s1')
    r.create_revision('s1', dict(CONTENT, edited=True), author_actor=ANALYST)
    assert not r.approval_valid(rev['revision_id']) and not r.released('s1')

def test_release_module_has_no_send_surface(tmp_path):
    """BEAAA-42596: intake has no report sender. No outbox, provider adapter, receipt or reconcile path."""
    for name in ('deliver_releases', 'ProviderRejected', 'RELEASE_RECIPIENT', 'PROVIDER_IDEMPOTENCY_WINDOW_SECONDS',
                 'ReleaseNotAuthorized'):
        assert not hasattr(report_release, name), name
    r = rel(tmp_path); approved(r)
    for name in SEND_SURFACE:
        assert not hasattr(r, name), name

def test_legacy_db_migrates_columns(tmp_path):
    """An older database (incl. the retired send/receipt tables) opens; those tables are never written."""
    import sqlite3
    path = tmp_path / 'release.sqlite'
    db = sqlite3.connect(path)
    db.executescript("""CREATE TABLE release_revisions(revision_id TEXT PRIMARY KEY, scan_id TEXT NOT NULL,
        seq INTEGER NOT NULL, content_json TEXT NOT NULL, content_sha256 TEXT NOT NULL, created_at TEXT NOT NULL,
        UNIQUE(scan_id, seq));
      CREATE TABLE release_sends(idempotency_key TEXT PRIMARY KEY, revision_id TEXT NOT NULL,
        content_sha256 TEXT NOT NULL, recipient TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
        attempts INTEGER NOT NULL DEFAULT 0, next_attempt REAL NOT NULL DEFAULT 0, last_error TEXT);
      CREATE TABLE release_receipts(idempotency_key TEXT PRIMARY KEY, recipient TEXT NOT NULL,
        message_id TEXT NOT NULL, revision_id TEXT NOT NULL, content_sha256 TEXT NOT NULL, accepted_at TEXT NOT NULL);
      INSERT INTO release_revisions VALUES('rev_old','s1',1,'{}','x','t');""")
    db.commit(); db.close()
    r = rel(tmp_path); rel(tmp_path)  # second open: guarded ALTER is idempotent
    cols = lambda t: {c[1] for c in r.db.execute(f'PRAGMA table_info({t})')}
    assert {'subject', 'body', 'artifact_sha256', 'renderer_version', 'revision_sha256'} <= cols('release_revisions')
    with pytest.raises(RevisionTampered): r.read_revision('rev_old')  # no frozen artifact: fail closed
    rev = approved(r)
    assert r.approval_valid(rev['revision_id']) and r.released('s1')
    for table in ('release_sends', 'release_receipts'):
        assert r.db.execute(f'SELECT count(*) FROM {table}').fetchone()[0] == 0

# --- C: frozen approved artifact bytes -------------------------------------------

def test_approval_digest_covers_artifact(tmp_path):
    r = rel(tmp_path); rev = r.create_revision('s1', CONTENT, author_actor=ANALYST)
    stored = r.read_revision(rev['revision_id'])
    assert stored['artifact_sha256'] == hashlib.sha256(
        stored['subject'].encode() + b'\x00' + stored['body'].encode()).hexdigest()
    assert stored['renderer_version'] == report_release.RENDERER_VERSION
    assert rev['revision_sha256'] == hashlib.sha256(
        f"{rev['content_sha256']}|{rev['artifact_sha256']}|{rev['renderer_version']}|author:{ANALYST}"
        .encode()).hexdigest()  # the Analyst author is bound into what the Lead approves
    with pytest.raises(ApprovalRefused, match='digest_mismatch'):  # content digest alone is not enough
        r.approve(rev['revision_id'], rev['content_sha256'], LEAD, nonce(1))
    with pytest.raises(ApprovalRefused, match='digest_mismatch'):
        r.approve(rev['revision_id'], rev['artifact_sha256'], LEAD, nonce(2))
    assert not r.approval_valid(rev['revision_id'])


def test_template_change_after_approval_keeps_approved_bytes(tmp_path, monkeypatch):
    r = rel(tmp_path); rev = approved(r)
    frozen = r.read_revision(rev['revision_id'])
    monkeypatch.setattr(report_release, 'release_message', lambda revision: ('EVIL subject', 'EVIL body'))
    again = r.read_revision(rev['revision_id'])
    assert (again['subject'], again['body']) == (frozen['subject'], frozen['body'])
    assert again['artifact_sha256'] == rev['artifact_sha256'] == hashlib.sha256(
        again['subject'].encode() + b'\x00' + again['body'].encode()).hexdigest()
    assert r.approval_valid(rev['revision_id']) and 'EVIL' not in repr(again)

@pytest.mark.parametrize('column,value', [('body', 'forged body'), ('subject', 'forged'),
                                          ('artifact_sha256', '0' * 64), ('renderer_version', 'v9')])
def test_tampered_artifact_voids_release(tmp_path, column, value):
    r = rel(tmp_path); rev = approved(r)
    r.db.execute('DROP TRIGGER release_revisions_immutable')
    r.db.execute(f'UPDATE release_revisions SET {column}=?', (value,))
    with pytest.raises(RevisionTampered): r.read_revision(rev['revision_id'])
    assert not r.approval_valid(rev['revision_id']) and not r.released('s1')

def test_content_edit_after_approval_new_revision_voids_old(tmp_path, monkeypatch):
    r = rel(tmp_path); rev = approved(r)
    monkeypatch.setattr(report_release, 'release_message', lambda revision: ('S2', 'B2'))
    new = r.create_revision('s1', dict(CONTENT, edited=True), author_actor=ANALYST)
    assert new['revision_id'] != rev['revision_id'] and new['artifact_sha256'] != rev['artifact_sha256']
    assert not r.approval_valid(rev['revision_id']) and not r.approval_valid(new['revision_id'])
    assert not r.released('s1')


# --- D: public projection ---------------------------------------------------------

def test_public_lifecycle_maps_each_status(tmp_path, monkeypatch):
    r = rel(tmp_path)
    assert r.public_lifecycle('s1') == {'report_status': 'awaiting_results', 'delivery_status': 'not_ready',
                                        'updated_at': None}
    assert r.public_lifecycle('s1', scan_failed=True)['report_status'] == 'unavailable'
    rev = r.create_revision('s1', CONTENT, author_actor=ANALYST)
    value = r.public_lifecycle('s1')
    assert (value['report_status'], value['delivery_status']) == ('review', 'not_ready')
    assert value['updated_at'] == r.read_revision(rev['revision_id'])['created_at']
    reviewed(r, rev, 1)
    value = r.public_lifecycle('s1')
    assert (value['report_status'], value['delivery_status']) == ('awaiting_ceo_approval', 'not_ready')
    assert value['updated_at'] == r.db.execute('SELECT decided_at FROM release_decisions WHERE nonce=?',
                                               (nonce(5001),)).fetchone()[0]
    r.approve(rev['revision_id'], rev['revision_sha256'], LEAD, nonce(1), 'reject')
    decided = r.db.execute('SELECT decided_at FROM release_decisions WHERE nonce=?', (nonce(1),)).fetchone()[0]
    assert r.public_lifecycle('s1') == {'report_status': 'changes_requested', 'delivery_status': 'not_ready',
                                        'updated_at': decided}
    rev = approved(r, dict(CONTENT, v=2), i=2)
    decided = r.db.execute('SELECT decided_at FROM release_decisions WHERE nonce=?', (nonce(2),)).fetchone()[0]
    # Intake never sends: an approved revision's delivery stays not_ready (Operations owns delivery).
    assert r.public_lifecycle('s1') == {'report_status': 'approved', 'delivery_status': 'not_ready',
                                        'updated_at': decided}
    unconfigured = ReportRelease(str(tmp_path / 'release.sqlite'), lead_actor_id='')
    assert unconfigured.public_lifecycle('s1')['report_status'] == 'unavailable'  # never inferred approved
    r.db.execute('DROP TRIGGER release_revisions_immutable'); r.db.execute("UPDATE release_revisions SET body='x'")
    assert r.public_lifecycle('s1') == report_release.UNAVAILABLE
    assert set(report_release.PUBLIC_RELEASE_FIELDS) == {'report_status', 'delivery_status'}
    for value in (report_release.REPORT_STATUSES, report_release.DELIVERY_STATUSES):
        assert 'delivered' not in value and 'inbox' not in ' '.join(value)

# --- app wiring -------------------------------------------------------------

@pytest.fixture
def app_env(tmp_path, monkeypatch):
    os.environ.setdefault('QA_DB_PATH', str(Path(tempfile.mkdtemp()) / 'import.sqlite'))
    app = importlib.import_module('app')
    # These legacy lifecycle fixtures exercise the configured BAC branch.
    monkeypatch.setattr(app, '_bac_mode', lambda: 'configured')
    from qa_service import IntakeStore, Code
    from qa_runtime import ScanQueue
    from bac_intake import BACIntake
    import httpx
    def no_network(*a, **k): pytest.fail('network call')
    for name in ('post', 'get', 'Client'): monkeypatch.setattr(httpx, name, no_network)
    path = str(tmp_path / 'qa.sqlite')
    store = IntakeStore(path)
    store.put_code(Code('TEST', 'test', 'Test', 'standard', 5, 5, '2099-01-01'))
    monkeypatch.setattr(app, 'store', store)
    monkeypatch.setattr(app, 'queue', ScanQueue(store, lambda p: {'scan_id': 'remote-1'}))
    monkeypatch.setattr(app, 'bac', BACIntake(path, base_url='', company_id='', owner_id='', token='',
                                              post=no_network, get=no_network))
    monkeypatch.setattr(app, 'release', ReportRelease(path, lead_actor_id=lambda: os.environ.get(
        'QA_RELEASE_LEAD_ACTOR_ID', '')))
    from operations_client import OperationsOutbox
    monkeypatch.setattr(app, 'operations', OperationsOutbox(path))
    monkeypatch.delenv('QA_OPERATIONS_BASE_URL', raising=False)
    monkeypatch.delenv('QA_OPERATIONS_TOKEN', raising=False)
    sent = []
    monkeypatch.setattr(app, '_send_report_code', lambda *a: sent.append(('code', a)))
    monkeypatch.setenv('QA_RELEASE_LEAD_ACTOR_ID', LEAD)
    monkeypatch.setenv('QA_RELEASE_LEAD_TOKEN_SHA256', hashlib.sha256(b'lead-secret').hexdigest())
    monkeypatch.setenv('QA_RELEASE_ANALYST_ACTOR_ID', ANALYST)
    monkeypatch.setenv('QA_RELEASE_ANALYST_TOKEN_SHA256', hashlib.sha256(b'analyst-secret').hexdigest())
    monkeypatch.setenv('QA_RELEASE_REVIEWER_ACTOR_ID', REVIEWER)
    monkeypatch.setenv('QA_RELEASE_REVIEWER_TOKEN_SHA256', hashlib.sha256(b'reviewer-secret').hexdigest())
    return app, store, sent


def finished_scan(app):
    sid = app.store.reserve(code='TEST', email='customer@example.test', agent_label='Agent',
                            endpoint_url='https://agent.example.test', transport='rest')['scan_id']
    app.queue.submit(sid, {})
    app.queue.callback('remote-1', {'tests': PRIVATE_TESTS, 'meta': {}})
    return sid, app.store.scan(sid)['report_token']


def bind_case(app, sid, case_id='case-1', state='acknowledged'):
    """Acknowledged BAC intake (request_id == scan_id) bound to the scan, as BACIntake records it."""
    app.bac.db.execute('INSERT OR IGNORE INTO bac_intake VALUES (?,?,?,?)', (sid, '{}', state, case_id))
    app.bac.db.execute('INSERT OR IGNORE INTO bac_scan_bindings VALUES (?,?)', (sid, sid))


def authoring(app, sid, bind=True, **kw):
    """Analyst-authored revision body naming the bound BAC case and the exact evidence digest."""
    if bind:
        bind_case(app, sid)
    digest = hashlib.sha256((app.store.scan(sid)['tests_json'] or '[]').encode()).hexdigest()
    return dict(dict(request_id=sid, case_id='case-1', evidence_sha256=digest, summary=SUMMARY,
                     recommendations=['Keep tool scopes minimal.']), **kw)


def author_revision(app, sid, headers=None, **kw):
    # Historical records for migration/read tests, never an HTTP authoring path.
    from report_tool import freeze_starter_input
    body = authoring(app, sid, **kw)
    frozen = freeze_starter_input(app.bac, app.store, request_id=body['request_id'],
        case_id=body['case_id'], scan_id=sid, evidence_sha256=body['evidence_sha256'])
    return app.release.create_revision(sid,
        {'report_input': frozen['input'], 'input_sha256': frozen['input_sha256'],
         'authored': {'summary': body['summary'], 'recommendations': body['recommendations']}},
        author_actor=ANALYST)


def test_e3_finished_scan_and_lead_approval_never_send_report_mail(app_env, monkeypatch):
    """BEAAA-42596: intake sends no report mail at all: not to the customer, not to info@."""
    app, store, sent = app_env
    sid, token = finished_scan(app)
    assert store.scan(sid)['status'] == 'finished' and token
    assert not hasattr(app, '_resend_release') and not hasattr(app, 'deliver_releases')
    for _ in range(3): app.worker_tick()  # httpx is patched to fail the test on any call
    assert sent == []
    assert store.db.execute('SELECT count(*) FROM mail_outbox').fetchone()[0] == 0
    from qa_runtime import deliver_pending
    with pytest.raises(RuntimeError): deliver_pending(store, lambda *a: pytest.fail('customer mail'))
    rev = author_revision(app, sid)
    assert rev['content_sha256'] == hashlib.sha256(app.release.db.execute(
        'SELECT content_json FROM release_revisions').fetchone()[0].encode()).hexdigest()
    stored = app.release.read_revision(rev['revision_id'])
    assert stored['content']['report_input']['scan_id'] == sid and stored['content']['authored']['summary'] == SUMMARY
    reviewed(app.release, rev); app.release.approve(rev['revision_id'], rev['revision_sha256'], LEAD, nonce(1))
    assert app.release.released(sid)
    for _ in range(3): app.worker_tick()  # approval does not arm any sender either
    assert sent == []

def test_report_routes_404_before_approval_and_no_code_mail(app_env):
    app, store, sent = app_env
    sid, token = finished_scan(app)
    c = TestClient(app.app); c.cookies.set('bb_report_session', 'x')
    origin = {'Origin': 'https://backbond.ai'}
    for method, route, kw in [('get', f'/scan/v1/reports/{token}', {}),
                              ('post', f'/scan/v1/reports/{token}/verification', {}),
                              ('post', f'/scan/v1/reports/{token}/session', {'json': {'code': '123456'}})]:
        r = getattr(c, method)(route, headers=origin, **kw)
        assert (r.status_code, r.json()) == (404, {'error': 'report_not_released'})
    assert sent == []
    rev = author_revision(app, sid)
    reviewed(app.release, rev); app.release.approve(rev['revision_id'], rev['revision_sha256'], LEAD, nonce(1))
    assert c.post(f'/scan/v1/reports/{token}/verification', headers=origin).status_code == 404
    c.cookies.clear()
    assert c.get(f'/scan/v1/reports/{token}').status_code == 401
    c.cookies.set('bb_report_session', 'x')
    assert c.get(f'/scan/v1/reports/{token}').status_code == 404  # local approval grants no customer access
    assert sent == []


def legacy_mailed(app, sid, status='sent'):
    """A pre-BAC report the legacy outbox handled, as the pre-42596 finish() + deliver_pending left it."""
    app.store.db.execute('INSERT OR REPLACE INTO mail_outbox(scan_id,status) VALUES(?,?)', (sid, status))


def code_session(c, token, sent):
    origin = {'Origin': 'https://backbond.ai'}
    r = c.post(f'/scan/v1/reports/{token}/verification', headers=origin)
    if r.status_code != 200:
        return r
    code = sent[-1][1][1]
    return c.post(f'/scan/v1/reports/{token}/session', headers=origin, json={'code': code})


def test_historical_released_legacy_report_keeps_code_session_and_read(app_env):
    app, store, sent = app_env
    sid, token = finished_scan(app)
    legacy_mailed(app, sid)
    c = TestClient(app.app, base_url='https://testserver')  # Secure cookies
    assert code_session(c, token, sent).status_code == 200
    assert sent and sent[-1][1][0] == 'customer@example.test'  # code to the original recipient only
    r = c.get(f'/scan/v1/reports/{token}')
    assert r.status_code == 200 and r.json()['tests']
    assert app.release.db.execute('SELECT count(*) FROM release_revisions').fetchone()[0] == 0


@pytest.mark.parametrize('status', ['pending', None])
def test_unreleased_legacy_report_fails_closed(app_env, status):
    app, store, sent = app_env
    sid, token = finished_scan(app)
    if status:
        legacy_mailed(app, sid, status)  # outbox never delivered: the customer never had the link
    c = TestClient(app.app, base_url='https://testserver')  # Secure cookies
    assert code_session(c, token, sent).status_code == 404 and sent == []
    assert c.get(f'/scan/v1/reports/{token}').status_code == 401
    c.cookies.set('bb_report_session', 'x')
    assert c.get(f'/scan/v1/reports/{token}').status_code == 404


def test_bac_report_cannot_borrow_legacy_access(app_env):
    """A BAC-bound scan is gated by release even if a legacy outbox row says 'sent'."""
    app, store, sent = app_env
    sid, token = finished_scan(app)
    bind_case(app, sid)
    legacy_mailed(app, sid)
    c = TestClient(app.app, base_url='https://testserver')  # Secure cookies
    assert code_session(c, token, sent).status_code == 404 and sent == []
    c.cookies.set('bb_report_session', 'x')
    assert c.get(f'/scan/v1/reports/{token}').status_code == 404
    rev = author_revision(app, sid)
    reviewed(app.release, rev); app.release.approve(rev['revision_id'], rev['revision_sha256'], LEAD, nonce(1))
    assert app.release.released(sid)
    c.cookies.clear()
    assert code_session(c, token, sent).status_code == 404 and sent == []  # never customer mail
    assert c.get(f'/scan/v1/reports/{token}').status_code == 401
    c.cookies.set('bb_report_session', 'x')
    assert c.get(f'/scan/v1/reports/{token}').status_code == 404
    assert sent == []




def test_intake_sends_no_report_mail_on_worker_approval_route_or_restart(app_env, monkeypatch):
    """Strongest negative: with a working mail credential and a recording mail client, a full
    author -> review -> HTTP Lead approval -> worker ticks -> restart cycle makes no provider call."""
    app, store, sent = app_env
    import httpx, json as _json
    creds = Path(tempfile.mkdtemp()) / 'scan-mail.json'
    creds.write_text(_json.dumps({'provider': 'resend', 'api_key': 'k'}))
    monkeypatch.setenv('SCAN_MAIL_CREDENTIALS_PATH', str(creds))
    calls = []
    class Client:
        def __init__(self, **kw): calls.append(('client', kw))
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def post(self, url, **kw): calls.append(('post', url)); pytest.fail('mail provider called')
    for name in ('post', 'get'):
        monkeypatch.setattr(httpx, name, lambda url, *a, n=name, **k: calls.append((n, url)) or pytest.fail('network'))
    monkeypatch.setattr(httpx, 'Client', Client)
    sid, token = finished_scan(app)
    rev = author_revision(app, sid)
    reviewed(app.release, rev)
    c = TestClient(app.app)
    ok = c.post(f"/scan/v1/bac/revisions/{rev['revision_id']}/decision", headers={'Authorization': 'Bearer lead-secret'},
                json={'revision_sha256': rev['revision_sha256'], 'nonce': nonce(1), 'decision': 'approve'})
    assert ok.status_code == 410
    app.release.approve(rev['revision_id'], rev['revision_sha256'], LEAD, nonce(1))
    for _ in range(3): app.worker_tick()
    monkeypatch.setattr(app, 'release', ReportRelease(app.release.db.execute('PRAGMA database_list').fetchone()[2],
                                                      lead_actor_id=LEAD))  # restart on the same database
    assert app.release.released(sid)
    for _ in range(3): app.worker_tick()
    assert calls == [] and sent == []
    r = c.post(f"/scan/v1/bac/revisions/{rev['revision_id']}/reconcile", headers={'Authorization': 'Bearer lead-secret'},
               json={'idempotency_key': '0' * 64, 'outcome': 'provider_accepted', 'message_id': 'm', 'nonce': nonce(2)})
    assert r.status_code in (404, 405)  # the reconcile route is gone with the sender

def test_public_scan_status_projection_has_no_internal_fields(app_env):
    app, store, sent = app_env
    sid = app.store.reserve(code='TEST', email='customer@example.test', agent_label='Agent',
                            endpoint_url='https://agent.example.test', transport='rest')
    token, sid = sid['status_token'], sid['scan_id']
    c = TestClient(app.app)
    status = lambda: c.post('/scan/v1/scan-status', json={'status_token': token})
    first = status().json()
    reference = first['request_reference']
    assert re.fullmatch(r'BB-[0-9a-f]{20}', reference)
    assert reference not in (sid, token)
    assert status().json()['request_reference'] == reference
    # A display reference confers neither status access nor cancellation authority.
    for route in ('scan-status', 'scan-cancel'):
        assert c.post('/scan/v1/' + route, json={'status_token': reference}).status_code == 404
    assert first['accepted_scope_version'] is None
    assert first['last_confirmed_progress_at'] is None
    assert first['queue_acknowledged'] is False and first['cancel_available'] is False
    assert first['lifecycle']['version'] == 'starter_lifecycle@1'
    assert (first['lifecycle']['report_status'], first['lifecycle']['delivery_status']) == ('awaiting_results',
                                                                                          'not_ready')
    app.queue.submit(sid, {})
    app.queue.callback('remote-1', {'tests': PRIVATE_TESTS, 'meta': {}})
    rev = author_revision(app, sid)
    assert status().json()['lifecycle']['report_status'] == 'awaiting_results'
    reviewed(app.release, rev)
    assert status().json()['lifecycle']['report_status'] == 'awaiting_results'
    app.release.approve(rev['revision_id'], rev['revision_sha256'], LEAD, nonce(1))
    response = status(); body = response.json()
    assert response.status_code == 200 and body['status'] == 'finished'
    lifecycle = body['lifecycle']
    assert (lifecycle['report_status'], lifecycle['delivery_status']) == ('awaiting_results', 'not_ready')
    assert set(lifecycle) == {'version', 'intake_status', 'report_status', 'delivery_status', 'updated_at'}
    raw = response.text
    for secret in (LEAD, INFO, 'customer@example.test',
                   sid, token, 'case-1', 'https://agent.example.test', 'lead-secret',
                   rev['revision_sha256'], rev['content_sha256']):
        assert secret not in raw
    assert 'delivered' not in raw and 'inbox' not in raw
    assert set(body) - {'status', 'created_at', 'finished_at', 'report_emailed', 'retry_allowed', 'state', 'phase',
                        'stage_label', 'processed_count', 'accepted_pack_total', 'usable_count',
                        'unavailable_count', 'updated_at', 'last_activity_at',
                        'request_reference', 'accepted_scope_version', 'last_confirmed_progress_at',
                        'queue_acknowledged', 'cancel_available'} == {'lifecycle'}
    assert body['request_reference'] == reference
    assert body['accepted_scope_version'] is None or body['accepted_scope_version'] in app.STARTER_PACK_IDS
    confirmed = body['last_confirmed_progress_at']
    assert confirmed is None or datetime.fromisoformat(confirmed.replace('Z', '+00:00')).tzinfo is not None
    assert type(body['queue_acknowledged']) is bool and type(body['cancel_available']) is bool
    assert body['queue_acknowledged'] is False and body['cancel_available'] is False
    assert 'report_stage' not in body and 'delivery_stage' not in body


def test_public_scan_status_release_store_error_reads_unknown(app_env, monkeypatch):
    app, store, sent = app_env
    res = app.store.reserve(code='TEST', email='customer@example.test', agent_label='Agent',
                            endpoint_url='https://agent.example.test', transport='rest')
    def broken(scan_id, **kw): raise RuntimeError('db gone')
    monkeypatch.setattr(app.release, 'public_lifecycle', broken)
    body = TestClient(app.app).post('/scan/v1/scan-status', json={'status_token': res['status_token']}).json()
    assert (body['lifecycle']['report_status'], body['lifecycle']['delivery_status']) == ('unavailable',
                                                                                        'unavailable')
    assert 'report_stage' not in body




# --- REPORT-AUTHOR-SEPARATION ----------------------------------------------------

def test_author_required_and_self_approval_refused(tmp_path):
    r = rel(tmp_path)
    for bad in ('', None):
        with pytest.raises(ReleaseError, match='author_required'):
            r.create_revision('s1', CONTENT, author_actor=bad)
    rev = r.create_revision('s1', CONTENT, author_actor=LEAD)  # same identity holds both roles
    with pytest.raises(ApprovalRefused, match='self_approval_refused'):
        r.approve(rev['revision_id'], rev['revision_sha256'], LEAD, nonce(1))
    assert not r.approval_valid(rev['revision_id'])
    with pytest.raises(ApprovalRefused, match='wrong_role'):
        r.approve(rev['revision_id'], rev['revision_sha256'], LEAD, nonce(2), role='analyst')
    rev = reviewed(r, r.create_revision('s1', CONTENT, author_actor=ANALYST), 3)
    assert r.approve(rev['revision_id'], rev['revision_sha256'], LEAD, nonce(3))['decision'] == 'approved'
    assert r.approval_valid(rev['revision_id'])


def test_author_tamper_breaks_revision_digest(tmp_path):
    r = rel(tmp_path); rev = r.create_revision('s1', CONTENT, author_actor=ANALYST)
    r.db.execute('DROP TRIGGER release_revisions_immutable')
    r.db.execute('UPDATE release_revisions SET author_actor=?', ('someone-else',))
    with pytest.raises(RevisionTampered): r.read_revision(rev['revision_id'])
    with pytest.raises(ApprovalRefused, match='revision_tampered'):
        r.approve(rev['revision_id'], rev['revision_sha256'], LEAD, nonce(1))


def test_legacy_authorless_revision_migrates_and_fails_closed(tmp_path):
    """Old schema with the frozen-artifact columns but no author_actor: ALTER adds the column;
    the author-less revision still reads, can never be approved, and public status is unavailable."""
    import sqlite3
    path = tmp_path / 'release.sqlite'
    db = sqlite3.connect(path)
    db.executescript("""CREATE TABLE release_revisions(revision_id TEXT PRIMARY KEY, scan_id TEXT NOT NULL,
        seq INTEGER NOT NULL, content_json TEXT NOT NULL, content_sha256 TEXT NOT NULL, created_at TEXT NOT NULL,
        subject TEXT, body TEXT, artifact_sha256 TEXT, renderer_version TEXT, revision_sha256 TEXT,
        UNIQUE(scan_id, seq));
      CREATE TABLE release_current(scan_id TEXT PRIMARY KEY, revision_id TEXT NOT NULL);""")
    content = report_release.canonical(CONTENT); digest = report_release.sha256(content)
    artifact = report_release.artifact_digest('s', 'b')
    legacy = report_release.revision_digest(digest, artifact, report_release.RENDERER_VERSION)
    db.execute("INSERT INTO release_revisions VALUES('rev_' || ?, 's1', 1, ?, ?, 't', 's', 'b', ?, ?, ?)",
               ('0' * 32, content, digest, artifact, report_release.RENDERER_VERSION, legacy))
    db.execute("INSERT INTO release_current VALUES('s1', ?)", ('rev_' + '0' * 32,))
    db.commit(); db.close()
    r = rel(tmp_path); rel(tmp_path)  # guarded ALTER is idempotent
    assert 'author_actor' in {c[1] for c in r.db.execute('PRAGMA table_info(release_revisions)')}
    old = r.read_revision('rev_' + '0' * 32)
    assert old['author_actor'] is None
    with pytest.raises(ApprovalRefused, match='author_missing'):
        r.approve(old['revision_id'], old['revision_sha256'], LEAD, nonce(1))
    assert not r.approval_valid(old['revision_id']) and not r.released('s1')
    assert r.public_lifecycle('s1') == report_release.UNAVAILABLE
    rev = approved_by(r, ANALYST)  # a new Analyst-authored revision is the way forward
    assert r.approval_valid(rev['revision_id'])


def approved_by(r, author, i=9):
    rev = reviewed(r, r.create_revision('s1', dict(CONTENT, by=author), author_actor=author), i)
    r.approve(rev['revision_id'], rev['revision_sha256'], LEAD, nonce(i))
    return rev








# --- independent reviewer, revision read access -------------------------------------

def test_review_required_and_refusals(tmp_path):
    r = rel(tmp_path); rev = r.create_revision('s1', CONTENT, author_actor=ANALYST)
    rid, digest = rev['revision_id'], rev['revision_sha256']
    with pytest.raises(ApprovalRefused, match='review_required'):
        r.approve(rid, digest, LEAD, nonce(1))
    assert not r.approval_valid(rid)
    for actor, kw, reason in ((ANALYST, {}, 'self_review_refused'), (LEAD, {}, 'reviewer_is_lead'),
                              (REVIEWER, {'role': 'lead'}, 'wrong_role'), (REVIEWER, {'role': 'analyst'}, 'wrong_role')):
        with pytest.raises(ApprovalRefused, match=reason):
            r.review(rid, digest, actor, nonce(10 + len(reason) + len(kw.get('role', ''))), **kw)
    with pytest.raises(ApprovalRefused, match='digest_mismatch'):
        r.review(rid, rev['content_sha256'], REVIEWER, nonce(30))
    with pytest.raises(ApprovalRefused, match='nonce_replayed'):  # nonces are single-use across stages
        r.review(rid, digest, REVIEWER, nonce(1))
    assert not r.review_valid(rid)
    r.review(rid, digest, REVIEWER, nonce(31))
    with pytest.raises(ApprovalRefused, match='nonce_replayed'):
        r.review(rid, digest, REVIEWER, nonce(31))
    with pytest.raises(ApprovalRefused, match='nonce_replayed'):
        r.approve(rid, digest, LEAD, nonce(31))
    assert r.approve(rid, digest, LEAD, nonce(32))['decision'] == 'approved' and r.approval_valid(rid)
    new = r.create_revision('s1', dict(CONTENT, edited=True), author_actor=ANALYST)  # edit voids both
    assert not r.review_valid(rid) and not r.approval_valid(rid)
    with pytest.raises(ApprovalRefused, match='stale_revision'):
        r.review(rid, digest, REVIEWER, nonce(33))
    with pytest.raises(ApprovalRefused, match='review_required'):
        r.approve(new['revision_id'], new['revision_sha256'], LEAD, nonce(34))
    r.review(new['revision_id'], new['revision_sha256'], REVIEWER, nonce(35), 'request_changes')
    assert r.public_lifecycle('s1')['report_status'] == 'changes_requested'
    with pytest.raises(ApprovalRefused, match='review_required'):
        r.approve(new['revision_id'], new['revision_sha256'], LEAD, nonce(36))


def test_review_bound_digest_and_independent_actor_in_approval_valid(tmp_path):
    r = rel(tmp_path); rev = approved(r)
    assert r.approval_valid(rev['revision_id'])
    r.db.execute("UPDATE release_decisions SET content_sha256=? WHERE stage='review'", ('0' * 64,))
    assert not r.approval_valid(rev['revision_id'])
    r.db.execute("UPDATE release_decisions SET content_sha256=?, actor_id=? WHERE stage='review'",
                 (rev['revision_sha256'], ANALYST))  # a review by the author never counts
    assert not r.approval_valid(rev['revision_id']) and not r.released('s1')
    r.db.execute("UPDATE release_decisions SET actor_id=? WHERE stage='review'", (LEAD,))
    assert not r.approval_valid(rev['revision_id'])


def test_legacy_authorless_revision_cannot_be_reviewed(tmp_path):
    r = rel(tmp_path); rev = r.create_revision('s1', CONTENT, author_actor=ANALYST)
    r.db.execute('DROP TRIGGER release_revisions_immutable')
    legacy = report_release.revision_digest(rev['content_sha256'], rev['artifact_sha256'], rev['renderer_version'])
    r.db.execute('UPDATE release_revisions SET author_actor=NULL, revision_sha256=?', (legacy,))
    with pytest.raises(ApprovalRefused, match='author_missing'):
        r.review(rev['revision_id'], legacy, REVIEWER, nonce(1))




def test_http_revision_read_any_release_role(app_env, monkeypatch):
    app, store, sent = app_env
    sid, _ = finished_scan(app)
    c = TestClient(app.app)
    rev = author_revision(app, sid)
    route = f"/scan/v1/bac/revisions/{rev['revision_id']}"
    for role in ('analyst', 'reviewer', 'lead'):
        r = c.get(route, headers={'Authorization': f'Bearer {role}-secret'})
        assert r.status_code == 200 and r.json()['revision_sha256'] == rev['revision_sha256']
    assert c.get(route).status_code == 401
    for bad in ('Bearer wrong', 'Bearer ', 'Basic analyst-secret', 'analyst-secret'):
        assert c.get(route, headers={'Authorization': bad}).status_code in ((401,) if bad != 'Bearer wrong' else (403,))
    assert c.get(route, headers={'Authorization': 'Bearer wrong'}).json() == {'error': 'forbidden'}
    for env in ('LEAD', 'ANALYST', 'REVIEWER'):
        monkeypatch.delenv(f'QA_RELEASE_{env}_TOKEN_SHA256')
    assert c.get(route, headers={'Authorization': 'Bearer lead-secret'}).status_code == 503


# --- E: Analyst report tool via the BAC case resolver (BEAAA-42596 review) -----------

def revision_count(app):
    return app.release.db.execute('SELECT count(*) FROM release_revisions').fetchone()[0]


def unfinished_scan(app):
    return app.store.reserve(code='TEST', email='customer@example.test', agent_label='Agent',
                             endpoint_url='https://agent.example.test', transport='rest')['scan_id']


@pytest.mark.parametrize('case', ['unacknowledged', 'wrong_case', 'wrong_request', 'digest_mismatch',
                                  'unfinished', 'unbound'])
def test_revision_requires_bac_case_resolver(app_env, case):
    app, store, sent = app_env
    sid = unfinished_scan(app) if case == 'unfinished' else finished_scan(app)[0]
    if case == 'unacknowledged':
        bind_case(app, sid, state='received')
    body = authoring(app, sid, bind=case not in ('unacknowledged', 'unbound'))
    body.update({'wrong_case': {'case_id': 'case-2'}, 'wrong_request': {'request_id': 'f' * 32},
                 'digest_mismatch': {'evidence_sha256': '0' * 64}}.get(case, {}))
    r = TestClient(app.app).post(f'/scan/v1/bac/scans/{sid}/revisions', json=body,
                                 headers={'Authorization': 'Bearer analyst-secret'})
    assert (r.status_code, r.json()) == (410, {'error': 'report_authority_moved_to_operations'})
    assert revision_count(app) == 0 and app.release.public_lifecycle(sid)['report_status'] == 'awaiting_results'


def test_frozen_input_records_not_applicable_and_leaks_no_private_text(app_env):
    import json as _json
    from report_tool import freeze_starter_input
    app, store, sent = app_env
    sid, _ = finished_scan(app)
    assert 'PRIVATE-PROMPT-1' in store.scan(sid)['tests_json']  # the fixture really holds private text
    rev = author_revision(app, sid)
    stored = app.release.read_revision(rev['revision_id'])
    frozen = stored['content']['report_input']
    assert frozen['comparison'] == frozen['retest'] == {'status': 'not_applicable', 'reason': 'starter_single_scan'}
    assert frozen['operations_freeze'] == {'supported': False, 'missing': ['comparison_id', 'retest_completed']}
    def keys(v):
        return set(v) | set().union(*map(keys, v.values())) if isinstance(v, dict) else (
            set().union(*map(keys, v)) if isinstance(v, list) else set())
    assert not keys(stored['content']) & {'comparison_id', 'retest_completed', 'result_id'}
    assert frozen['results'] == [{'ordinal': 1, 'verdict': 'pass'}, {'ordinal': 2, 'verdict': 'fail'}]
    assert frozen['verdict_counts'] == {'pass': 1, 'fail': 1, 'could_not_run': 0}
    assert (frozen['case_id'], frozen['request_id'], frozen['scan_id']) == ('case-1', sid, sid)
    assert frozen['pack_version'] == store.scan(sid)['pack_version'] and frozen['schema'] == 'starter_report_input@1'
    assert stored['content']['input_sha256'] == hashlib.sha256(report_release.canonical(frozen).encode()).hexdigest()
    again = freeze_starter_input(app.bac, store, request_id=sid, case_id='case-1', scan_id=sid,
                                 evidence_sha256=frozen['evidence_sha256'])
    assert again == {'input': frozen, 'input_sha256': stored['content']['input_sha256']}
    read = TestClient(app.app).get(f"/scan/v1/bac/revisions/{rev['revision_id']}",
                                   headers={'Authorization': 'Bearer lead-secret'})
    raw = (tuple(app.release.db.execute('SELECT content_json, subject, body FROM release_revisions').fetchone()), read.text)
    assert 'PRIVATE' not in _json.dumps(raw) and 'agent.example.test' not in _json.dumps(raw)


def test_artifact_is_authored_template_not_measurement_json(app_env):
    app, store, sent = app_env
    sid, _ = finished_scan(app)
    rev = author_revision(app, sid, recommendations=['Rotate the exposed key.', 'Scope tool access.'])
    stored = app.release.read_revision(rev['revision_id'])
    subject, body = stored['subject'], stored['body']
    assert stored['renderer_version'] == report_release.RENDERER_VERSION == 'starter_report_text@2'
    assert subject == f"BackBond Starter report — case case-1 (revision {rev['revision_id']})"
    assert SUMMARY in body and '- Rotate the exposed key.\n- Scope tool access.\n' in body
    for line in (f"Revision: {rev['revision_id']}", f"Revision content sha256: {rev['content_sha256']}",
                 f"Report input sha256: {stored['content']['input_sha256']}", 'Pass: 1', 'Fail: 1',
                 'Could not run: 0', 'Outcome: complete',
                 'No comparison and no retest apply to a Starter scan.',
                 'Internal release to info@backbond.ai after BAC review and CEO approval. '
                 'Provider acceptance is not confirmation of inbox delivery.'):
        assert line in body
    assert (body.index('\nSummary\n') < body.index('\nRecommendations\n') < body.index('\nResults\n')
            < body.index('\nScope\n'))
    assert report_release.canonical(stored['content']['report_input']) not in body
    assert '{' not in body and '"verdict"' not in body and 'ordinal' not in body
    assert report_release.release_message(stored) == (subject, body)  # deterministic
    plain = app.release.read_revision(author_revision(app, sid, recommendations=[])['revision_id'])
    assert 'Recommendations\nNone recorded.\n' in plain['body']


def test_http_summary_edit_creates_new_revision_and_voids_approval(app_env):
    app, store, sent = app_env
    sid, _ = finished_scan(app)
    rev = author_revision(app, sid)
    reviewed(app.release, rev); app.release.approve(rev['revision_id'], rev['revision_sha256'], LEAD, nonce(1))
    assert app.release.approval_valid(rev['revision_id']) and app.release.released(sid)
    new = author_revision(app, sid, summary=SUMMARY + ' Corrected.')
    assert new['revision_id'] != rev['revision_id'] and new['content_sha256'] != rev['content_sha256']
    assert not app.release.approval_valid(rev['revision_id']) and not app.release.approval_valid(new['revision_id'])
    assert not app.release.released(sid)
    assert 'Corrected.' in app.release.read_revision(new['revision_id'])['body']


def test_template_and_renderer_change_keep_stored_bytes_and_version(app_env, monkeypatch):
    app, store, sent = app_env
    sid, _ = finished_scan(app)
    rev = author_revision(app, sid)
    reviewed(app.release, rev); app.release.approve(rev['revision_id'], rev['revision_sha256'], LEAD, nonce(1))
    frozen = app.release.read_revision(rev['revision_id'])
    monkeypatch.setattr(report_release, 'release_message', lambda revision: ('EVIL', 'EVIL'))
    monkeypatch.setattr(report_release, 'RENDERER_VERSION', 'starter_report_text@3')
    again = app.release.read_revision(rev['revision_id'])
    assert again['renderer_version'] == 'starter_report_text@2' and again['body'] == frozen['body']
    assert (again['subject'], again['body']) == (frozen['subject'], frozen['body']) and SUMMARY in again['body']
    assert app.release.approval_valid(rev['revision_id']) and 'EVIL' not in repr(again)


@pytest.mark.parametrize('bad', [{'scan_id': 'x'}, {'author_actor': LEAD}, {'report': {}},
                                 {'evidence_sha256': 'A' * 64}, {'summary': ''}, {'summary': 'x' * 8001},
                                 {'recommendations': ['r'] * 21}, {'recommendations': ['x' * 1001]},
                                 {'case_id': ''}, {'request_id': 'r' * 201}])
def test_http_revision_body_strict(app_env, bad):
    app, store, sent = app_env
    sid, _ = finished_scan(app)
    r = TestClient(app.app).post(f'/scan/v1/bac/scans/{sid}/revisions', json=dict(authoring(app, sid), **bad),
                                 headers={'Authorization': 'Bearer analyst-secret'})
    assert r.status_code == 422 and revision_count(app) == 0
    r = TestClient(app.app).post(f'/scan/v1/bac/scans/{sid}/revisions',
                                 headers={'Authorization': 'Bearer analyst-secret'})
    assert r.status_code == 422 and revision_count(app) == 0  # no body: no bare scan_id snapshot


@pytest.mark.parametrize('role', ['lead', 'reviewer'])
def test_http_create_refuses_non_analyst_with_valid_body(app_env, role):
    app, store, sent = app_env
    sid, _ = finished_scan(app)
    r = TestClient(app.app).post(f'/scan/v1/bac/scans/{sid}/revisions', json=authoring(app, sid),
                                 headers={'Authorization': f'Bearer {role}-secret'})
    assert (r.status_code, r.json()['error']) == (410, 'report_authority_moved_to_operations') and revision_count(app) == 0


# --- Analyst report-tool evidence read --------------------------------------

def test_http_case_evidence_read_feeds_authoring_and_refuses_unbound(app_env):
    app, store, _ = app_env
    sid, _ = finished_scan(app)
    c = TestClient(app.app)
    ask = {'request_id': sid, 'case_id': 'case-1', 'scan_id': sid}
    analyst = {'Authorization': 'Bearer analyst-secret'}
    # Not yet bound/acknowledged: no digest leaks.
    r = c.post('/scan/v1/bac/cases/evidence', json=ask, headers=analyst)
    assert (r.status_code, r.json()) == (409, {'error': 'evidence_unconfirmed'})
    bind_case(app, sid)
    for headers, code in (({}, 401), ({'Authorization': 'Bearer nope'}, 403),
                          ({'Authorization': 'Bearer lead-secret'}, 403),
                          ({'Authorization': 'Bearer reviewer-secret'}, 403)):
        assert c.post('/scan/v1/bac/cases/evidence', json=ask, headers=headers).status_code == code
    for wrong in ({'case_id': 'case-2'}, {'request_id': 'other'}):
        r = c.post('/scan/v1/bac/cases/evidence', json={**ask, **wrong}, headers=analyst)
        assert r.status_code == 409
    assert c.post('/scan/v1/bac/cases/evidence', json={**ask, 'extra': 1}, headers=analyst).status_code == 422
    r = c.post('/scan/v1/bac/cases/evidence', json=ask, headers=analyst)
    assert r.status_code == 200, r.text
    value = r.json()
    assert set(value) == {'request_id', 'case_id', 'scan_id', 'evidence_sha256', 'outcome', 'results'}
    assert 'PRIVATE' not in json.dumps(value) and 'agent.example.test' not in json.dumps(value)
    assert value['evidence_sha256'] == hashlib.sha256(store.scan(sid)['tests_json'].encode()).hexdigest()
    # The digest the tool returned is exactly what authoring accepts.
    r = c.post(f'/scan/v1/bac/scans/{sid}/revisions', headers=analyst,
               json={'request_id': sid, 'case_id': 'case-1', 'evidence_sha256': value['evidence_sha256'],
                     'summary': SUMMARY})
    assert r.status_code == 410, r.text


def test_evidence_digest_requires_acknowledged_bound_case(app_env):
    app, _, _ = app_env
    from bac_intake import BACUnconfirmed
    sid, _ = finished_scan(app)
    ask = dict(request_id=sid, case_id='case-1', scan_id=sid)
    with pytest.raises(BACUnconfirmed):
        app.bac.evidence_digest(app.store, **ask)
    bind_case(app, sid, state='pending')
    with pytest.raises(BACUnconfirmed):
        app.bac.evidence_digest(app.store, **ask)
    app.bac.db.execute("UPDATE bac_intake SET state='acknowledged' WHERE request_id=?", (sid,))
    with pytest.raises(BACUnconfirmed):
        app.bac.evidence_digest(app.store, **{**ask, 'case_id': 'case-2'})
    assert app.bac.evidence_digest(app.store, **ask) == hashlib.sha256(
        app.store.scan(sid)['tests_json'].encode()).hexdigest()
