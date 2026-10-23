"""A website response is a transport failure, never agent evidence to grade."""
from __future__ import annotations

from contextlib import contextmanager
import json

import httpx
import pytest

from api.scan_handlers import connectivity_check_handler
from harness import customer_runner
from harness.customer_runner import customer_agent_http_runner_factory
from harness.runner import RunnerError
from scanners import customer_agent_walker as walker


HTML = '<!DOCTYPE html><html><body>Dojo application shell PRIVATE-BODY</body></html>'
ENDPOINT = 'https://agent.example.test/chat'


@pytest.fixture
def endpoint(monkeypatch):
    """Exercise real runner parsing over bounded, in-memory HTTP responses."""
    replies = []
    requests = []

    def handle(request):
        requests.append(request)
        body, content_type = replies.pop(0) if len(replies) > 1 else replies[0]
        return httpx.Response(200, stream=httpx.ByteStream(body.encode()), headers={'Content-Type': content_type})

    class Scope:
        def __init__(self, url):
            assert url == ENDPOINT

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        @contextmanager
        def client(self, **kwargs):
            with httpx.Client(transport=httpx.MockTransport(handle), **kwargs) as client:
                yield client

    monkeypatch.setattr(customer_runner, 'ScopedEgressAllowlist', Scope)
    return replies, requests


def runner(transport='rest', **extra):
    config = {'transport': transport, 'auth': {'type': 'none'}, '_include_result_rows': True}
    if transport == 'openai_chat':
        config['model'] = 'fixture-model'
    if transport == 'json_map':
        config.update(request_template={'prompt': '{{prompt}}'}, response_path='data.0.answer')
    config.update(extra)
    return customer_agent_http_runner_factory(ENDPOINT, '', endpoint_config=config)


@pytest.mark.parametrize('transport', ['rest', 'openai_chat', 'json_map'])
@pytest.mark.parametrize('content_type', ['text/html; charset=utf-8', 'application/xhtml+xml', 'application/json', 'text/plain'])
def test_html_fails_connection_check_and_probe(endpoint, transport, content_type):
    replies, requests = endpoint
    replies.append((HTML, content_type))
    target = runner(transport)
    status, value = connectivity_check_handler({
        'agent_endpoint': {'url': ENDPOINT, **target._config},
    }, runner_factory=lambda *a, **kw: target)
    assert status == 200
    assert value == {'success': False, 'error': 'connection_failed'}
    with pytest.raises(RunnerError) as error:
        target.complete('Run the next case')
    assert 'PRIVATE-BODY' not in str(error.value)
    assert target._pending_exchange is None
    assert len(requests) == 2


@pytest.mark.parametrize('transport,body', [
    ('rest', '{}'), ('rest', '[]'), ('rest', 'null'), ('rest', 'OK'),
    ('rest', '{"response":false}'), ('rest', '{"response":null}'),
    ('rest', '{"response":"  "}'), ('rest', '{"error":"PRIVATE-BODY"}'),
    ('openai_chat', '{}'), ('openai_chat', '{"choices":[]}'),
    ('openai_chat', '{"choices":[{"message":{"content":null}}]}'),
    ('openai_chat', '{"choices":[{"message":{"content":123}}]}'),
    ('json_map', '{}'), ('json_map', '{"data":[]}'),
    ('json_map', '{"data":[{"answer":false}]}'),
])
def test_invalid_response_contract_is_never_a_reply(endpoint, transport, body):
    endpoint[0].append((body, 'application/json'))
    with pytest.raises(RunnerError) as error:
        runner(transport).complete('hello')
    assert 'PRIVATE-BODY' not in str(error.value)


@pytest.mark.parametrize('transport,body', [
    ('rest', {'response': 'OK'}),
    ('openai_chat', {'choices': [{'message': {'content': 'OK'}}]}),
    ('json_map', {'data': [{'answer': 'OK'}]}),
])
def test_valid_contract_still_passes_connection_test(endpoint, transport, body):
    endpoint[0].append((json.dumps(body), 'application/json; charset=utf-8'))
    target = runner(transport)
    assert connectivity_check_handler({
        'agent_endpoint': {'url': ENDPOINT, **target._config},
    }, runner_factory=lambda *a, **kw: target)[1] == {'success': True, 'error': None}
    assert target.complete('hello') == 'OK'


def test_html_mime_type_is_rejected_even_with_valid_json(endpoint):
    endpoint[0].append(('{"response":"OK"}', 'Text/HTML; charset=utf-8'))
    with pytest.raises(RunnerError):
        runner().complete('hello')


def test_an_agent_can_return_requested_html_inside_its_json_contract(endpoint):
    endpoint[0].append((json.dumps({'response': HTML}), 'application/json'))
    assert runner().complete('Write an HTML document') == HTML


def test_json_map_can_select_text_from_a_top_level_array(endpoint):
    endpoint[0].append(('[{"answer":"OK"}]', 'application/json'))
    assert runner('json_map', response_path='0.answer').complete('hello') == 'OK'


@pytest.mark.parametrize('message', [
    {'role': 'assistant', 'content': 'Hello'},
    {'role': 'assistant', 'content': None, 'tool_calls': [
        {'id': 'call-1', 'type': 'function', 'function': {'name': 'lookup', 'arguments': '{}'}},
    ]},
])
@pytest.mark.parametrize('envelope', ['direct', 'openai'])
def test_native_message_and_tool_call_contracts_are_preserved(endpoint, message, envelope):
    payload = message if envelope == 'direct' else {'choices': [{'message': message}]}
    endpoint[0].append((json.dumps(payload), 'application/json'))
    assert runner().complete_messages([{'role': 'user', 'content': 'hi'}], tools=[]) == message


def test_failed_probe_cannot_reuse_a_previous_valid_exchange(endpoint):
    endpoint[0].extend([('{"response":"previous valid reply"}', 'application/json'), (HTML, 'text/html')])
    target = runner()
    assert target.complete('previous case') == 'previous valid reply'
    with pytest.raises(RunnerError):
        target.complete('next case')
    target.record_starter_verdict(test_id='next', family='prompt_injection', verdict='could_not_run', reason='invalid response')
    row, = target.starter_result_rows()
    assert row['verdict'] == 'could_not_run'
    assert 'reply' not in row and 'prompt' not in row


def test_html_scan_never_calls_judge_or_records_pass_fail(endpoint, monkeypatch):
    endpoint[0].append((HTML, 'text/html'))
    target = runner()
    target.pack_id = 'starter-100@2'

    class Judge:
        calls = 0

        def complete(self, **kwargs):
            self.calls += 1
            pytest.fail('Invalid HTTP responses must never reach the evaluator')

    judge = Judge()
    monkeypatch.setattr(walker, 'judge_client_from_env', lambda: judge)
    with pytest.raises(RunnerError):
        list(walker.customer_agent_probe_run_fn(target))
    assert judge.calls == 0
    assert len(endpoint[1]) == 3  # Existing unreachable-target stop bound.
    assert target.starter_result_rows()
    assert all(row['verdict'] == 'could_not_run' and 'reply' not in row
               for row in target.starter_result_rows())
