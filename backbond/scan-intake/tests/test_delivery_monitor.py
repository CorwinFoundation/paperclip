from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json
import sys
from unittest.mock import Mock

import pytest
sys.path.insert(0, str(Path(__file__).parents[1] / 'server'))
from delivery_monitor import (AlertOutbox, credential_alert, delivery_alert,
                              delivery_observation, review_alert)


def observation(row=None):
    return delivery_observation(dict(id='fixture-scan', status='finished', outcome='complete',
        finished_at='2026-10-31T00:00:00Z'), row)


@pytest.mark.parametrize('row,stage', [
    (None, 'workflow_write_back'),
    (dict(state='held', remote_status='{"state":"needs_attention"}'), 'review'),
    (dict(state='held', error='mail_not_configured'), 'credential'),
    (dict(state='sent', remote_status='{"state":"sent"}'), 'provider'),
    (dict(state='accepted', message_id='provider-id'), 'workflow_write_back'),
])
def test_deadline_from_completion_and_exact_stage(row, stage):
    value = observation(row)
    assert value['stage'] == stage
    assert delivery_alert(value, value['completed_at'] + 14399) is None
    assert delivery_alert(value, value['completed_at'] + 14400)['priority'] == 'critical'


def test_sent_requires_local_provider_receipt():
    value = observation(dict(state='sent', message_id='provider-id'))
    assert delivery_alert(value, value['completed_at'] + 999999) is None
    assert not observation(dict(state='sent', message_id=''))['delivered']


def test_no_naive_timestamp():
    from delivery_monitor import epoch
    with pytest.raises(ValueError, match='timezone'):
        epoch('2026-10-31T00:00:00')


def test_review_card_before_breach():
    value = observation(dict(state='held', remote_status='{"state":"needs_attention"}'))
    assert len(review_alert(value)['options']) == 3
    assert delivery_alert(value, value['completed_at'] + 60) is None


@pytest.mark.parametrize('remaining,threshold', [(604801, None), (604800, 'T-7'), (86400, 'T-1'), (-1, 'T-1')])
def test_credential_thresholds(remaining, threshold):
    value = credential_alert('fixture-key', 1000000 + remaining, 'verified fixture command', 1000000)
    assert (value['threshold'] if value else None) == threshold


def test_push_retry_does_not_repeat_ticket_after_restart(tmp_path):
    path = str(tmp_path / 'alerts.sqlite')
    alert = delivery_alert(observation(), observation()['completed_at'] + 14400)
    box = AlertOutbox(path)
    box.enqueue(alert)
    ticket = Mock(return_value='BEAAA-fixture')
    push = Mock(side_effect=[RuntimeError('secret'), 'push-receipt'])
    box.flush(ticket, push, now=0)
    box = AlertOutbox(path)
    box.enqueue(alert)
    box.flush(ticket, push, now=59)
    box.flush(ticket, push, now=60)
    ticket.assert_called_once()
    assert push.call_count == 2
    assert push.call_args_list[0] == push.call_args_list[1]
    with box.connect() as db:
        row = dict(db.execute('SELECT * FROM delivery_alerts').fetchone())
    assert row['push'] == 'push-receipt' and 'secret' not in json.dumps(row)


def test_concurrent_monitors_deduplicate(tmp_path):
    path = str(tmp_path / 'alerts.sqlite')
    AlertOutbox(path)
    ticket, push = Mock(return_value='ticket'), Mock(return_value='push')
    def run(_):
        box = AlertOutbox(path)
        box.enqueue(dict(key='fixture', kind='delivery_breach'))
        box.flush(ticket, push, now=0)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(run, range(8)))
    ticket.assert_called_once()
    push.assert_called_once()


def test_ambiguous_ticket_failure_reuses_remote_key(tmp_path):
    box = AlertOutbox(str(tmp_path / 'alerts.sqlite'))
    box.enqueue(dict(key='fixture'))
    ticket = Mock(side_effect=[TimeoutError(), 'ticket'])
    push = Mock(return_value='push')
    box.flush(ticket, push, now=0)
    push.assert_not_called()
    box.flush(ticket, push, now=60)
    assert ticket.call_args_list[0] == ticket.call_args_list[1]
    push.assert_called_once()


def test_collect_includes_capture_gaps_without_mutating_scan(tmp_path):
    import sqlite3
    import threading
    from types import SimpleNamespace
    from findings_client import FindingsReports
    from delivery_monitor import collect_alerts, epoch
    db = sqlite3.connect(':memory:')
    db.row_factory = sqlite3.Row
    db.execute('CREATE TABLE scans(id,status,outcome,finished_at)')
    db.executemany('INSERT INTO scans VALUES (?,?,?,?)', [
        ('missing', 'finished', 'complete', '2026-10-31T00:00:00Z'),
        ('old', 'finished', 'complete', '2026-10-01T00:00:00Z'),
        ('partial', 'finished', 'incomplete', '2026-10-31T00:00:00Z')])
    store = SimpleNamespace(db=db, _lock=threading.RLock())
    reports = FindingsReports(str(tmp_path / 'reports.sqlite'))
    alerts = AlertOutbox(str(tmp_path / 'alerts.sqlite'))
    before = list(db.iterdump())
    assert collect_alerts(store, reports, alerts, now=epoch('2026-10-31T04:00:00Z'),
                          since='2026-10-31T00:00:00Z') == 1
    with alerts.connect() as conn:
        rows = conn.execute('SELECT payload FROM delivery_alerts').fetchall()
    assert len(rows) == 1
    assert json.loads(rows[0][0])['scan_id'] == 'missing'
    assert before == list(db.iterdump())


def test_worker_monitor_is_disabled_by_default(monkeypatch):
    from delivery_monitor import monitor_tick
    monkeypatch.delenv('QA_DELIVERY_MONITOR', raising=False)
    assert monitor_tick(None, None, http=None, now=0) == 0


def test_worker_monitor_requires_explicit_cutoff_and_storage(monkeypatch):
    import pytest
    from delivery_monitor import monitor_tick
    monkeypatch.setenv('QA_DELIVERY_MONITOR', '1')
    monkeypatch.delenv('QA_DELIVERY_MONITOR_SINCE', raising=False)
    monkeypatch.delenv('QA_DELIVERY_ALERT_DB_PATH', raising=False)
    with pytest.raises(ValueError, match='explicit_monitor'):
        monitor_tick(None, None, http=None, now=0)


def test_worker_monitor_flushes_incident_and_push_once(tmp_path, monkeypatch):
    import sqlite3
    import threading
    from types import SimpleNamespace
    from findings_client import FindingsReports
    from delivery_monitor import epoch, monitor_tick

    class Response:
        def __init__(self, status_code, body):
            self.status_code = status_code
            self._body = body
            self.headers = {}

        def json(self):
            return self._body

    class Http:
        def __init__(self):
            self.posts = []
            self.gets = []

        def get(self, url, **kwargs):
            self.gets.append((url, kwargs))
            return Response(200, [])

        def post(self, url, **kwargs):
            self.posts.append((url, kwargs))
            if url.endswith('/issues'):
                return Response(201, {'id': 'BEAAA-fixture', 'companyId': 'fixture-company'})
            return Response(202, {'id': 'push-fixture'})

    db = sqlite3.connect(':memory:')
    db.row_factory = sqlite3.Row
    db.execute('CREATE TABLE scans(id,status,outcome,finished_at)')
    db.execute('INSERT INTO scans VALUES (?,?,?,?)',
               ('fixture-complete', 'finished', 'complete', '2026-10-31T00:00:00Z'))
    store = SimpleNamespace(db=db, _lock=threading.RLock())
    reports = FindingsReports(str(tmp_path / 'reports.sqlite'))
    http = Http()
    monkeypatch.setenv('QA_DELIVERY_MONITOR', '1')
    monkeypatch.setenv('QA_DELIVERY_MONITOR_SINCE', '2026-10-31T00:00:00Z')
    monkeypatch.setenv('QA_DELIVERY_ALERT_DB_PATH', str(tmp_path / 'alerts.sqlite'))
    monkeypatch.setenv('PAPERCLIP_API_URL', 'http://127.0.0.1:3100')
    monkeypatch.setenv('PAPERCLIP_COMPANY_ID', 'fixture-company')
    monkeypatch.setenv('PAPERCLIP_API_KEY', 'fixture-paperclip-token')
    monkeypatch.setenv('QA_FOUNDER_PUSH_URL', 'http://127.0.0.1:8787/push')
    monkeypatch.setenv('QA_FOUNDER_PUSH_TOKEN', 'fixture-push-token')

    assert monitor_tick(store, reports, http=http, now=epoch('2026-10-31T04:00:00Z')) == 1
    assert monitor_tick(store, reports, http=http, now=epoch('2026-10-31T04:01:00Z')) == 1
    assert [url for url, _ in http.posts] == [
        'http://127.0.0.1:3100/api/companies/fixture-company/issues',
        'http://127.0.0.1:8787/push',
    ]
    with AlertOutbox(str(tmp_path / 'alerts.sqlite')).connect() as conn:
        row = dict(conn.execute('SELECT * FROM delivery_alerts').fetchone())
    assert row['ticket'] == 'paperclip-issue:BEAAA-fixture'
    assert row['push'] == 'founder-push:push-fixture'
    assert json.loads(row['payload'])['stage'] == 'workflow_write_back'

@pytest.mark.parametrize('labels', [None, [], ['Info'], ['SPAM'], ['SENT'], ['INBOX', 'SPAM']])
def test_placement_requires_actual_inbox(labels):
    from delivery_monitor import placement_alert
    value = observation(dict(state='sent', message_id='provider-id'))
    now = value['completed_at'] + 14400
    evidence = dict(scan_id='fixture-scan', message_id='provider-id',
                    recipient='info@backbond.ai', labels=labels, observed_at=now)
    assert placement_alert(value, now, evidence, message_id='provider-id')['stage'] == 'mailbox'
    evidence['labels'] = ['INBOX', 'Info']
    assert placement_alert(value, now, evidence, message_id='provider-id') is None
    evidence['message_id'] = 'another-message'
    assert placement_alert(value, now, evidence, message_id='provider-id')


def test_missing_placement_uses_ticket_and_push(tmp_path):
    from delivery_monitor import placement_alert
    from alert_adapters import sanitize_alert
    value = observation(dict(state='sent', message_id='provider-id'))
    alert = placement_alert(value, value['completed_at'] + 14400)
    assert sanitize_alert(alert)['kind'] == 'placement_failure'
    box = AlertOutbox(str(tmp_path / 'placement.sqlite'))
    ticket, push = Mock(return_value='fixture-ticket'), Mock(return_value='fixture-push')
    for _ in range(2):
        box.enqueue(alert)
        box.flush(ticket, push, now=0)
    ticket.assert_called_once()
    push.assert_called_once()
