import base64
import sys
from pathlib import Path
from unittest.mock import patch
import httpx
import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / 'server'))
from report_mail_transport import send_mail

CONFIG = {'provider': 'resend', 'api_key': 'test-key'}
ARGS = dict(recipients=['founder@example.com'], subject='Report', text='test',
            idempotency_key='report-mail-test-123')


def test_pdf_and_provider_identity():
    with patch('report_mail_transport.httpx.Client') as client:
        post = client.return_value.__enter__.return_value.post
        post.return_value = httpx.Response(200, json={'id': 'message-123'})
        assert send_mail(CONFIG, **ARGS, cc=['copy@example.com'], pdf=b'%PDF-1.7\nbytes') == 'message-123'
        request = post.call_args.kwargs
        assert request['headers']['Idempotency-Key'] == ARGS['idempotency_key']
        assert request['json']['from'] == 'BackBond <info@backbond.ai>'
        assert request['json']['cc'] == ['copy@example.com']
        assert base64.b64decode(request['json']['attachments'][0]['content']) == b'%PDF-1.7\nbytes'


@pytest.mark.parametrize('changes', [dict(recipients=[]), dict(recipients=['bad\r\n@example.com']),
    dict(subject='bad\nsubject'), dict(idempotency_key='bad'), dict(pdf=b'not-pdf')])
def test_invalid_input_never_calls_provider(changes):
    with patch('report_mail_transport.httpx.Client') as client:
        with pytest.raises(ValueError):
            send_mail(CONFIG, **(ARGS | changes))
        client.assert_not_called()


@pytest.mark.parametrize('status,body', [(429, {'secret': 'provider detail'}), (200, {}),
                                        (200, {'id': '\ninvalid'})])
def test_failure_is_redacted(status, body):
    with patch('report_mail_transport.httpx.Client') as client:
        client.return_value.__enter__.return_value.post.return_value = httpx.Response(status, json=body)
        with pytest.raises(RuntimeError) as error:
            send_mail(CONFIG, **ARGS)
        assert 'provider detail' not in str(error.value)


def test_html_and_text_alternative_share_existing_transport():
    with patch('report_mail_transport.httpx.Client') as client:
        post = client.return_value.__enter__.return_value.post
        post.return_value = httpx.Response(200, json={'id': 'message-html'})
        assert send_mail(CONFIG, **ARGS, html='<p>Your report is ready.</p>',
                         pdf=b'%PDF-1.7\nbytes') == 'message-html'
        payload = post.call_args.kwargs['json']
        assert payload['text'] == ARGS['text']
        assert payload['html'] == '<p>Your report is ready.</p>'
        assert payload['attachments'][0]['filename'] == 'findings-report.pdf'


def test_text_only_verification_mail_remains_supported():
    with patch('report_mail_transport.httpx.Client') as client:
        post = client.return_value.__enter__.return_value.post
        post.return_value = httpx.Response(200, json={'id': 'message-code'})
        send_mail(CONFIG, **ARGS)
        assert 'html' not in post.call_args.kwargs['json']
