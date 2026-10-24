"""The anonymous legacy endpoint is paced; payment/auth errors stay terminal."""
import json
from types import SimpleNamespace

import pytest

from harness import customer_runner as runners
from harness.pollinations_pacing import needs_free_tier_pacing, starter_wait_budget
from harness.runner import RunnerError

URL = 'https://text.pollinations.ai/openai'
CONFIG = {'transport': 'json_map', 'auth': {'type': 'none'},
          'request_template': {'messages': [{'role': 'user', 'content': '{{prompt}}'}]},
          'response_path': 'choices.0.message.content'}


def clock(monkeypatch):
    state = {'now': 100.0, 'waits': []}
    monkeypatch.setattr(runners.time, 'monotonic', lambda: state['now'])
    def sleep(seconds):
        state['waits'].append(seconds)
        state['now'] += seconds
    monkeypatch.setattr(runners.time, 'sleep', sleep)
    return state


def test_pacing_is_bound_to_exact_anonymous_endpoint_and_starter_pack():
    assert needs_free_tier_pacing(URL, CONFIG)
    for url in [URL+'.evil', URL.replace('text.pollinations.ai', 'text.pollinations.ai.evil'),
                URL+'?key=example', URL.replace('https:', 'http:'), URL+'/other']:
        assert not needs_free_tier_pacing(url, CONFIG)
    assert not needs_free_tier_pacing(URL, {**CONFIG, 'auth': {'type': 'bearer'}})
    request = SimpleNamespace(pack_id='starter-100@4', agent_endpoint=URL, endpoint_config=CONFIG)
    assert starter_wait_budget(request) == 3100
    request.pack_id = 'starter-100@5'
    assert starter_wait_budget(request) == 0
    assert starter_wait_budget(None) == 0


def test_calls_wait_without_changing_prompts_and_preserve_whole_evidence(monkeypatch):
    state = clock(monkeypatch)
    calls = []
    monkeypatch.setattr(runners._CustomerAgentHttpRunner, 'complete', lambda self, prompt: calls.append((state['now'], prompt)) or prompt)
    runner = runners.customer_agent_http_runner_factory(URL, '', endpoint_config=CONFIG)
    runner.set_scan_deadline(300)
    assert runner.complete('first prompt') == 'first prompt'
    assert runner.complete('second prompt') == 'second prompt'
    assert calls == [(100, 'first prompt'), (131, 'second prompt')]
    assert state['waits'] == [31]
    assert runner._request_timeout() == 90
    assert runner._cap_result_text('x'*6000) == 'x'*6000
    with pytest.raises(RunnerError, match='complete-capture limit'):
        runner._cap_result_text('x'*65537)


def test_explicit_free_limit_retries_same_prompt_after_peer_wait(monkeypatch):
    state = clock(monkeypatch)
    calls = []
    def complete(self, prompt):
        calls.append((state['now'], prompt))
        if len(calls) == 1:
            raise runners.CustomerEndpointHTTPError(402, 'Payment Required', json.dumps({'error': {'code': 'FREE_LIMIT'}}), {'retry-after': '45'})
        return 'whole answer'
    monkeypatch.setattr(runners._CustomerAgentHttpRunner, 'complete', complete)
    runner = runners.customer_agent_http_runner_factory(URL, '', endpoint_config=CONFIG)
    runner.set_scan_deadline(300)
    assert runner.complete('unchanged') == 'whole answer'
    assert calls == [(100, 'unchanged'), (145, 'unchanged')]


def test_slow_reply_gets_a_full_cooldown_after_it_finishes(monkeypatch):
    state = clock(monkeypatch)
    calls = []
    def complete(self, prompt):
        calls.append(state['now'])
        state['now'] += 7
        return 'complete answer'
    monkeypatch.setattr(runners._CustomerAgentHttpRunner, 'complete', complete)
    runner = runners.customer_agent_http_runner_factory(URL, '', endpoint_config=CONFIG)
    assert runner.complete('first') == 'complete answer'
    assert runner.complete('second') == 'complete answer'
    assert calls == [100, 138]
    assert state['waits'] == [31]


@pytest.mark.parametrize('status,body', [(402, {'error': {'code': 'INSUFFICIENT_BALANCE'}}),
    (401, {'error': {'code': 'FREE_LIMIT'}}), (403, {}), (402, {'error': None}), (402, [])])
def test_billing_auth_and_malformed_errors_never_retry(monkeypatch, status, body):
    state = clock(monkeypatch)
    def complete(self, prompt):
        raise runners.CustomerEndpointHTTPError(status, 'Rejected', json.dumps(body), {})
    monkeypatch.setattr(runners._CustomerAgentHttpRunner, 'complete', complete)
    runner = runners.customer_agent_http_runner_factory(URL, '', endpoint_config=CONFIG)
    with pytest.raises(runners.CustomerEndpointHTTPError): runner.complete('prompt')
    assert not state['waits']


def test_retries_and_scan_wait_are_bounded(monkeypatch):
    state = clock(monkeypatch)
    calls = []
    def complete(self, prompt):
        calls.append(state['now'])
        raise runners.CustomerEndpointHTTPError(402, 'Rejected', '{"error":{"code":"FREE_LIMIT"}}', {})
    monkeypatch.setattr(runners._CustomerAgentHttpRunner, 'complete', complete)
    runner = runners.customer_agent_http_runner_factory(URL, '', endpoint_config=CONFIG)
    with pytest.raises(runners.CustomerEndpointHTTPError): runner.complete('prompt')
    assert calls == [100, 131, 162]
    runner.set_scan_deadline(180)
    with pytest.raises(RunnerError, match='scan deadline exceeded'): runner.complete('prompt')
    assert calls == [100, 131, 162]


def test_real_adapter_path_keeps_request_bytes_and_whole_reply(monkeypatch):
    import httpx
    state = clock(monkeypatch)
    requests = []
    timeouts = []
    answer = 'whole response '*500
    def respond(request):
        requests.append((state['now'], str(request.url), json.loads(request.content), dict(request.headers)))
        if len(requests) == 1:
            status, body = 402, {'error': {'code': 'FREE_LIMIT'}}
        else:
            status, body = 200, {'choices': [{'message': {'content': answer}, 'finish_reason': 'stop'}]}
        return httpx.Response(status, headers={'Content-Type': 'application/json'},
                              stream=httpx.ByteStream(json.dumps(body).encode()))
    class Scope:
        def __init__(self, endpoint): assert endpoint == URL
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def client(self, **kwargs):
            timeouts.append(kwargs['timeout'])
            return httpx.Client(transport=httpx.MockTransport(respond), **kwargs)
    monkeypatch.setattr(runners, 'ScopedEgressAllowlist', Scope)
    runner = runners.customer_agent_http_runner_factory(URL, '', endpoint_config={**CONFIG, '_include_result_rows': True})
    runner.set_scan_deadline(250)
    assert runner.complete('exact prompt') == answer
    runner.record_starter_verdict(test_id='ANS-01', family='answers_normal_requests', verdict='pass')
    assert runner.starter_result_rows()[0]['reply'] == answer
    assert requests[0][1:3] == requests[1][1:3]
    assert requests[0][2] == {'messages': [{'role': 'user', 'content': 'exact prompt'}]}
    assert all('authorization' not in request[3] for request in requests)
    assert state['waits'] == [31]
    assert timeouts == [90, 90]


def test_runtime_budgets_include_wait_only_for_the_paced_scan():
    from api.scan_runtime_factory import _orchestrator_join_timeout, _parent_timeout_for_scan, _scorer_timeout_for_scan, _scorer_row_recv_timeout
    from scanners.live_probe_orchestrator import scan_wall_clock_seconds
    request = SimpleNamespace(pack_id='starter-100@4', agent_endpoint=URL, endpoint_config=CONFIG)
    assert scan_wall_clock_seconds(request) == 4900
    assert _parent_timeout_for_scan(30, request) == 4930
    # Three slow attempts, two maximum Retry-After waits and initial pacing
    # must fit without consuming the ordinary row/grading allowance.
    assert _scorer_timeout_for_scan(request) == _scorer_row_recv_timeout() + 31 + 3 * 90 + 2 * 60
    assert _scorer_timeout_for_scan(None) == _scorer_row_recv_timeout()
    assert scan_wall_clock_seconds(None) == 1800
    assert _orchestrator_join_timeout(30) == 1830


@pytest.mark.parametrize('finish', ['length', 'content_filter', None])
def test_incomplete_provider_termination_is_never_complete_evidence(finish):
    runner = runners.customer_agent_http_runner_factory(URL, '', endpoint_config=CONFIG)
    with pytest.raises(RunnerError, match='completeness unconfirmed'):
        runner._parse_response(json.dumps({'choices': [{'message': {'content': 'unfinished'}, 'finish_reason': finish}]}))
