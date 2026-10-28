import base64
import hashlib
import json
from pathlib import Path
import sys
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / 'server'))
from report_mail_outbox import ReportMailOutbox, validate


@pytest.fixture
def payload():
    value = dict(schema_version='report_mail@1', mode='founders', target_name='Target',
                 run_date='2026-10-23', note='test', recipients=[], approval_ref=None)
    for kind, raw in [('pdf', b'%PDF-1.7 exact supplied bytes'), ('html', b'<h1>Report</h1>')]:
        value[kind + '_base64'] = base64.b64encode(raw).decode()
        value[kind + '_sha256'] = hashlib.sha256(raw).hexdigest()
    return value


def submit(outbox, payload, **kwargs):
    return outbox.submit(payload, actor='bac-service-key', founders=['founder@example.com'],
                         links={'founder@example.com': 'https://backbond.ai/report/opaque'}, **kwargs)


def test_restart_duplicate_and_exact_attachment(tmp_path, payload):
    path = str(tmp_path / 'mail.db')
    ident = submit(ReportMailOutbox(path), payload, now=100)
    transport = Mock(return_value='resend-message')
    box = ReportMailOutbox(path)
    assert submit(box, payload, now=110) == ident
    assert box.deliver(ident, {}, now=120, transport=transport)['message_id'] == 'resend-message'
    assert ReportMailOutbox(path).deliver(ident, {}, now=130, transport=transport)['state'] == 'accepted'
    transport.assert_called_once()
    assert transport.call_args.kwargs['pdf'] == b'%PDF-1.7 exact supplied bytes'
    assert transport.call_args.kwargs['recipients'] == ['founder@example.com']
    with box.connect() as db:
        row = db.execute('SELECT * FROM bac_report_mail').fetchone()
    assert row['actor'] == 'bac-service-key' and row['created_at'] == 100
    assert json.loads(row['binding'])['payload']['html_sha256'] == payload['html_sha256']


def test_unknown_retries_identical_then_holds(tmp_path, payload):
    box = ReportMailOutbox(str(tmp_path / 'mail.db'))
    ident = submit(box, payload)
    transport = Mock(side_effect=RuntimeError('secret provider response'))
    assert box.deliver(ident, {}, now=100, transport=transport)['state'] == 'unknown'
    assert box.deliver(ident, {}, now=200, transport=transport)['state'] == 'unknown'
    assert transport.call_args_list[0] == transport.call_args_list[1]
    assert box.deliver(ident, {}, now=100 + 23 * 3600, transport=transport)['state'] == 'held'
    assert transport.call_count == 2


def test_crash_lease_blocks_parallel_send_then_recovers(tmp_path, payload):
    box = ReportMailOutbox(str(tmp_path / 'mail.db'))
    ident = submit(box, payload)
    with box.connect() as db:
        db.execute("UPDATE bac_report_mail SET state='sending',first_send=100,lease_until=220")
    transport = Mock(return_value='receipt')
    assert box.deliver(ident, {}, now=150, transport=transport)['state'] == 'sending'
    transport.assert_not_called()
    assert box.deliver(ident, {}, now=221, transport=transport)['state'] == 'accepted'


def test_customer_requires_approval_and_copies_founders(payload):
    payload.update(mode='customer', recipients=['customer@example.com'])
    with pytest.raises(ValueError, match='approval'):
        validate(payload, ['founder@example.com'])
    payload['approval_ref'] = 'BAC-74'
    to, cc, _ = validate(payload, ['founder@example.com'])
    assert to == ['customer@example.com'] and cc == ['founder@example.com']


@pytest.mark.parametrize('changes', [dict(mode='invalid'), dict(recipients=['x@example.com']),
    dict(approval_ref='BAC-74'), dict(pdf_sha256='0' * 64), dict(html_base64='!'),
    dict(target_name='Header\r\nInjected'), dict(run_date='bad'), dict(extra='field')])
def test_malformed_input_refused_before_storage(tmp_path, payload, changes):
    box = ReportMailOutbox(str(tmp_path / 'mail.db'))
    payload.update(changes)
    with pytest.raises(ValueError):
        submit(box, payload)
    with box.connect() as db:
        assert db.execute('SELECT count(*) FROM bac_report_mail').fetchone()[0] == 0


def test_no_founders_configuration_fails_closed(payload):
    with pytest.raises(ValueError):
        validate(payload, [])


def test_config_drift_does_not_create_second_send(tmp_path, payload):
    box = ReportMailOutbox(str(tmp_path / 'mail.db'))
    ident = submit(box, payload)
    assert box.submit(payload, actor='bac-service-key', founders=['other@example.com'],
        links={'other@example.com': 'https://backbond.ai/report/new'}) == ident
    transport = Mock(return_value='message')
    box.deliver(ident, {}, transport=transport)
    assert transport.call_args.kwargs['recipients'] == ['founder@example.com']


def test_internal_note_stays_in_outbox_only(tmp_path, payload):
    payload['note'] = 'BEAAA-44276 BAC-244 internal workflow ' + 'a' * 64
    box = ReportMailOutbox(str(tmp_path / 'mail.db'))
    ident = submit(box, payload)
    transport = Mock(return_value='message')
    assert box.deliver(ident, {}, transport=transport)['state'] == 'accepted'
    mail = transport.call_args.kwargs
    assert mail['subject'] == 'Your BackBond 100 Scan report — Target'
    assert mail['text'] == ('Your BackBond 100 Scan report is ready.\n\n'
                            'https://backbond.ai/report/opaque')
    assert payload['note'] not in mail['text']
    with box.connect() as db:
        binding = json.loads(db.execute('SELECT binding FROM bac_report_mail').fetchone()[0])
    assert binding['payload']['note'] == payload['note']


def test_html_reuses_intake_layout_and_escapes_link(tmp_path, payload):
    box = ReportMailOutbox(str(tmp_path / 'mail.db'))
    link = 'https://backbond.ai/report/opaque?email=a%40example.com&x="quoted"'
    ident = box.submit(payload, actor='service', founders=['founder@example.com'],
                       links={'founder@example.com': link})
    transport = Mock(return_value='message')
    assert box.deliver(ident, {}, transport=transport)['state'] == 'accepted'
    html = transport.call_args.kwargs['html']
    assert (Path(__file__).parents[1] / 'web' / 'scan.css').read_text() in html
    assert 'class="site-head"' in html and 'class="hero"' in html
    assert html.count('<a ') == 1
    assert '&amp;x=&quot;quoted&quot;' in html
    assert 'BEAAA-' not in html and 'BAC-' not in html
