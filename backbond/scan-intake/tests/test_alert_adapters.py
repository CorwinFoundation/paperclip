from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / 'server'))
from alert_adapters import (AlertAdapterError, FounderPushAdapter,
                            PaperclipIncidentAdapter)


KEY = 'a' * 64
ALERT = dict(key='delivery:scan-1', kind='delivery_breach', priority='critical',
             scan_id='scan-1', stage='provider', recipient='info@backbond.ai',
             completed_at=1790812800.0)


class Response:
    def __init__(self, status_code, value=None, headers=None):
        self.status_code = status_code
        self._value = value
        self.headers = headers or {}

    def json(self):
        if isinstance(self._value, Exception):
            raise self._value
        return self._value


def test_paperclip_reconciles_existing_incident_without_create():
    calls = []
    existing = {'id': 'issue-1', 'companyId': 'company-1',
                'description': 'AUTOMATED SCANNER ALERT delivery:scan-1\n{}'}
    adapter = PaperclipIncidentAdapter('https://paperclip.invalid', 'company-1', 'token',
        lambda *args, **kwargs: pytest.fail('duplicate create'),
        lambda *args, **kwargs: (calls.append((args, kwargs)) or Response(200, [existing])))
    assert adapter(ALERT, idempotency_key=KEY) == 'paperclip-issue:issue-1'
    assert calls[0][1]['headers']['Idempotency-Key'] == KEY


def test_paperclip_create_payload_is_closed_and_retries_can_reconcile():
    posted = []
    def post(route, **kwargs):
        posted.append((route, kwargs))
        return Response(201, {'id': 'issue-2', 'companyId': 'company-1'})
    adapter = PaperclipIncidentAdapter('https://paperclip.invalid', 'company-1', 'token', post,
        lambda *args, **kwargs: Response(200, []))
    assert adapter(dict(ALERT, provider_error='secret must not escape'), idempotency_key=KEY) == 'paperclip-issue:issue-2'
    payload = posted[0][1]['json']
    assert payload['priority'] == 'critical'
    assert 'provider_error' not in payload['description']
    assert posted[0][1]['headers']['Idempotency-Key'] == KEY


def test_paperclip_rejects_duplicate_matches():
    records = [{'id': 'issue-1', 'companyId': 'company-1',
                'description': 'AUTOMATED SCANNER ALERT delivery:scan-1\n{}'},
               {'id': 'issue-2', 'companyId': 'company-1',
                'description': 'AUTOMATED SCANNER ALERT delivery:scan-1\n{}'}]
    adapter = PaperclipIncidentAdapter('https://paperclip.invalid', 'company-1', 'token',
        lambda *args, **kwargs: pytest.fail('must not create'),
        lambda *args, **kwargs: Response(200, records))
    with pytest.raises(AlertAdapterError, match='duplicate'):
        adapter(ALERT, idempotency_key=KEY)


def test_founder_push_requires_receipt_and_does_not_send_unknown_fields():
    calls = []
    adapter = FounderPushAdapter('https://push.invalid/alerts', 'push-token',
        lambda *args, **kwargs: (calls.append((args, kwargs)) or Response(202, {'receipt': 'push-1'})))
    assert adapter(dict(ALERT, raw_provider_error='do-not-send'), idempotency_key=KEY) == 'founder-push:push-1'
    route, kwargs = calls[0]
    assert route == ('https://push.invalid/alerts',)
    assert kwargs['headers']['Idempotency-Key'] == KEY
    assert kwargs['json']['idempotency_key'] == KEY
    assert 'raw_provider_error' not in str(kwargs['json'])


def test_founder_push_rejects_missing_receipt():
    adapter = FounderPushAdapter('https://push.invalid/alerts', 'push-token',
        lambda *args, **kwargs: Response(202, None))
    with pytest.raises(AlertAdapterError, match='receipt'):
        adapter(ALERT, idempotency_key=KEY)


@pytest.mark.parametrize('adapter', [
    PaperclipIncidentAdapter('', '', '', lambda *args, **kwargs: None),
    FounderPushAdapter('', '', lambda *args, **kwargs: None),
])
def test_unconfigured_adapters_fail_closed(adapter):
    with pytest.raises(AlertAdapterError, match='not_configured'):
        adapter(ALERT, idempotency_key=KEY)


def test_adapters_retry_push_after_restart_without_recreating_incident(tmp_path):
    from delivery_monitor import AlertOutbox
    tickets, pushes = [], []
    def create(*args, **kwargs):
        tickets.append(kwargs)
        return Response(201, {'id': 'issue-1', 'companyId': 'company-1'})
    def notify(*args, **kwargs):
        pushes.append(kwargs)
        if len(pushes) == 1:
            raise TimeoutError('ambiguous acceptance')
        return Response(202, {'receipt': 'push-1'})
    ticket = PaperclipIncidentAdapter('https://paperclip.invalid', 'company-1', 'token',
                                     create, lambda *a, **kw: Response(200, []))
    push = FounderPushAdapter('https://push.invalid/alerts', 'token', notify)
    path = str(tmp_path / 'alerts.sqlite')
    box = AlertOutbox(path)
    box.enqueue(ALERT)
    box.flush(ticket, push, now=0)
    resumed = AlertOutbox(path)
    resumed.enqueue(ALERT)
    resumed.flush(ticket, push, now=60)
    resumed.flush(ticket, push, now=120)
    assert len(tickets) == 1
    assert len(pushes) == 2
    assert pushes[0]['json'] == pushes[1]['json']
    assert pushes[0]['headers']['Idempotency-Key'] == pushes[1]['headers']['Idempotency-Key']
    with resumed.connect() as db:
        row = db.execute('SELECT ticket,push FROM delivery_alerts').fetchone()
        assert tuple(row) == ('paperclip-issue:issue-1', 'founder-push:push-1')


def test_ambiguous_incident_post_reconciles_after_restart(tmp_path):
    from delivery_monitor import AlertOutbox
    records, posts = [], []
    def create(*args, **kwargs):
        posts.append(kwargs)
        records.append(dict(id='issue-1', companyId='company-1',
                            description=kwargs['json']['description']))
        raise TimeoutError('accepted before connection loss')
    ticket = PaperclipIncidentAdapter('https://paperclip.invalid', 'company-1', 'token',
                                     create, lambda *a, **kw: Response(200, records))
    path = str(tmp_path / 'alerts.sqlite')
    box = AlertOutbox(path)
    box.enqueue(ALERT)
    box.flush(ticket, lambda *a, **kw: 'push-1', now=0)
    AlertOutbox(path).flush(ticket, lambda *a, **kw: 'push-1', now=60)
    assert len(posts) == 1
    with box.connect() as db:
        assert db.execute('SELECT ticket FROM delivery_alerts').fetchone()[0] == 'paperclip-issue:issue-1'


def test_incident_requires_lookup_before_creation():
    adapter = PaperclipIncidentAdapter('https://paperclip.invalid', 'company-1', 'token',
        lambda *a, **kw: pytest.fail('must reconcile before create'))
    with pytest.raises(AlertAdapterError, match='not_configured'):
        adapter(ALERT, idempotency_key=KEY)
