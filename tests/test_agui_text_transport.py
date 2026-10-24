"""AG-UI text turns must finish successfully before they can be graded."""
from contextlib import contextmanager
import json

import httpx
import pytest

from api.scan_handlers import connectivity_check_handler
from api.scan_request_validation import validate_agent_endpoint
from harness import customer_runner
from harness.agui_text import request_payload, response_text
from harness.customer_runner import customer_agent_http_runner_factory
from harness.runner import RunnerError
from scanners import customer_agent_walker as walker

URL = 'https://agent.example.test/crew_chat'
START = {'type': 'RUN_STARTED', 'threadId': 'thread', 'runId': 'run'}
CHUNK = {'type': 'TEXT_MESSAGE_CHUNK', 'messageId': 'message', 'role': 'assistant', 'delta': 'OK'}
FINISH = {'type': 'RUN_FINISHED', 'threadId': 'thread', 'runId': 'run'}


def sse(events):
    return ''.join('data: ' + json.dumps(event) + '\n\n' for event in events)


def parse(events):
    return response_text(sse(events), thread_id='thread', run_id='run')


def test_requests_are_fresh_independent_turns_and_preserve_prompt_data():
    prompt = 'Quote "hello"\n你好 <html> {{prompt}}'
    first, second = request_payload(prompt), request_payload(prompt)
    assert first['threadId'] != second['threadId'] and first['runId'] != second['runId']
    assert first['messages'][0]['id'] != second['messages'][0]['id']
    assert first['messages'][0]['content'] == prompt
    assert first['state'] == {} and first['tools'] == [] and first['context'] == []
    assert first['forwardedProps'] == {}
    assert request_payload(prompt, 'system')['messages'][0]['role'] == 'system'


def test_live_crewai_chunk_shape_and_redundant_snapshot_do_not_duplicate_answer():
    snapshot = {'type': 'MESSAGES_SNAPSHOT', 'messages': [{'role': 'assistant', 'content': 'OK'}]}
    assert parse([START, CHUNK, snapshot, {'type': 'STATE_SNAPSHOT', 'snapshot': {'answer': 'wrong'}}, FINISH]) == 'OK'


def test_standard_start_content_end_and_legacy_chunk_continuations():
    assert parse([START, {'type': 'TEXT_MESSAGE_START', 'messageId': 'm', 'role': 'assistant'},
                  {'type': 'TEXT_MESSAGE_CONTENT', 'messageId': 'm', 'delta': 'Hi'},
                  {'type': 'TEXT_MESSAGE_END', 'messageId': 'm'}, FINISH]) == 'Hi'
    assert parse([START, {**CHUNK, 'delta': 'O'},
                  {'type': 'TEXT_MESSAGE_CHUNK', 'messageId': None, 'role': None, 'delta': 'K'}, FINISH]) == 'OK'
    assert parse([START, CHUNK, {**FINISH, 'outcome': {'type': 'success'}}]) == 'OK'
    assert parse([START, {'type': 'TOOL_CALL_START', 'toolCallId': 'tool'},
                  {'type': 'TOOL_CALL_RESULT', 'toolCallId': 'tool'}, CHUNK, FINISH]) == 'OK'


@pytest.mark.parametrize('events', [
    [START, CHUNK], [START, FINISH], [CHUNK, START, FINISH],
    [START, {**CHUNK, 'role': 'user'}, FINISH],
    [START, {**CHUNK, 'delta': None}, FINISH],
    [START, {**CHUNK, 'messageId': ''}, FINISH],
    [START, {'type': 'TEXT_MESSAGE_CHUNK', 'delta': 'unbound'}, FINISH],
    [START, {**CHUNK, 'delta': ' '}, FINISH],
    [{**START, 'runId': 'other'}, CHUNK, FINISH],
    [START, CHUNK, {**FINISH, 'threadId': 'other'}],
    [START, {**CHUNK, 'runId': 'other'}, FINISH],
    [START, CHUNK, {'type': 'RUN_ERROR', 'message': 'PRIVATE'}, FINISH],
    [START, CHUNK, {**FINISH, 'outcome': {'type': 'interrupt'}}],
    [START, CHUNK, {**FINISH, 'outcome': {'type': 'cancelled'}}],
    [START, CHUNK, {**FINISH, 'outcome': {'type': 'success', 'pendingToolCallIds': ['tool']}}],
    [START, {'type': 'TOOL_CALL_START', 'toolCallId': 'tool'}, CHUNK, FINISH],
    [START, {'type': 'CUSTOM', 'name': 'on_interrupt'}, CHUNK, FINISH],
    [START, CHUNK, FINISH, CHUNK], [START, START, CHUNK, FINISH],
    [START, {'type': 'STATE_SNAPSHOT', 'snapshot': {'response': 'PRIVATE'}}, FINISH],
    [START, {'type': 'TOOL_CALL_ARGS', 'delta': '{"answer":"PRIVATE"}'}, FINISH],
    [START, {'type': 'TEXT_MESSAGE_CONTENT', 'messageId': 'm', 'delta': 'missing start'}, FINISH],
    [START, {'type': 'TEXT_MESSAGE_START', 'messageId': 'm', 'role': 'assistant'}, FINISH],
    [START, {'type': 'TEXT_MESSAGE_END', 'messageId': 'm'}, FINISH],
    [START, [], FINISH], [START, {'data': 'PRIVATE'}, FINISH],
])
def test_invalid_or_incomplete_runs_never_return_partial_text(events):
    with pytest.raises(RunnerError) as error:
        parse(events)
    assert 'PRIVATE' not in str(error.value)


@pytest.mark.parametrize('wire', ['<!DOCTYPE html><html>PRIVATE</html>', 'data: PRIVATE\n\n',
                                 sse([START, CHUNK, FINISH]).rstrip('\n')])
def test_malformed_stream_and_truncated_final_frame_are_errors(wire):
    with pytest.raises(RunnerError):
        response_text(wire, thread_id='thread', run_id='run')


def test_sse_comments_crlf_and_multiline_data_are_supported():
    wire = ': keepalive\r\n\r\n' + sse([START, CHUNK, FINISH]).replace('\n', '\r\n')
    assert response_text(wire, thread_id='thread', run_id='run') == 'OK'
    wire = sse([START]) + 'data: {"type":"TEXT_MESSAGE_CHUNK",\ndata: "messageId":"m","delta":"OK"}\n\n' + sse([FINISH])
    assert response_text(wire, thread_id='thread', run_id='run') == 'OK'


def test_event_count_bound():
    with pytest.raises(RunnerError):
        parse([START] + [{'type': 'STEP_STARTED', 'stepName': 'x'}] * 4096 + [CHUNK, FINISH])


@pytest.fixture
def http_endpoint(monkeypatch):
    requests = []
    config = {'content_type': 'text/event-stream; charset=utf-8', 'events': None, 'body': None}

    def handle(request):
        requests.append(request)
        value = json.loads(request.content)
        events = config['events'] if config['events'] is not None else [
            {**START, 'threadId': value['threadId'], 'runId': value['runId']}, CHUNK,
            {**FINISH, 'threadId': value['threadId'], 'runId': value['runId']},
        ]
        if config.get('truncate_finish'):
            events = events[:-1]
        body = config['body'] if config['body'] is not None else sse(events)
        return httpx.Response(200, stream=httpx.ByteStream(body.encode()), headers={'Content-Type': config['content_type']})

    class Scope:
        def __init__(self, url):
            assert url == URL
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        @contextmanager
        def client(self, **kwargs):
            assert kwargs['follow_redirects'] is False
            with httpx.Client(transport=httpx.MockTransport(handle), **kwargs) as client:
                yield client

    monkeypatch.setattr(customer_runner, 'ScopedEgressAllowlist', Scope)
    target = customer_agent_http_runner_factory(URL, 'INERT-CANARY', endpoint_config={
        'transport': 'ag_ui', 'auth': {'type': 'bearer'}, '_include_result_rows': True})
    return target, requests, config


def test_connection_and_probe_share_guarded_transport_and_fresh_identity(http_endpoint):
    target, requests, _ = http_endpoint
    status, result = connectivity_check_handler({'agent_endpoint': {'url': URL, **target._config}, 'credential_secret': 'INERT-CANARY'},
        runner_factory=lambda *a, **kw: target)
    assert status == 200 and result == {'success': True, 'error': None}
    assert target.complete('Case prompt') == 'OK'
    bodies = [json.loads(r.content) for r in requests]
    assert [b['messages'][-1]['content'] for b in bodies] == ['Reply with exactly OK.', 'Case prompt']
    assert bodies[0]['threadId'] != bodies[1]['threadId']
    for request in requests:
        assert request.headers['Accept'] == 'text/event-stream'
        assert request.headers['Accept-Encoding'] == 'identity'
        assert request.headers['Authorization'] == 'Bearer INERT-CANARY'


@pytest.mark.parametrize('content_type,body', [
    ('text/html', '<html>PRIVATE</html>'), ('application/json', '{"response":"OK"}'),
    ('text/event-stream', 'data: <!DOCTYPE html>PRIVATE\n\n'),
])
def test_wrong_mime_or_html_never_confirms_connection(http_endpoint, content_type, body):
    target, _, config = http_endpoint
    config.update(content_type=content_type, body=body)
    assert connectivity_check_handler({'agent_endpoint': {'url': URL, **target._config}, 'credential_secret': 'INERT-CANARY'},
        runner_factory=lambda *a, **kw: target)[1]['success'] is False
    assert target._pending_exchange is None


def test_partial_run_never_reaches_judge_or_reuses_previous_reply(http_endpoint, monkeypatch):
    target, requests, config = http_endpoint
    assert target.complete('previous turn') == 'OK'
    config['truncate_finish'] = True
    target.pack_id = 'starter-100@2'
    class Judge:
        def complete(self, **kwargs):
            pytest.fail('Incomplete event streams must never reach the judge')
    monkeypatch.setattr(walker, 'judge_client_from_env', lambda: Judge())
    with pytest.raises(RunnerError):
        list(walker.customer_agent_probe_run_fn(target))
    assert len(requests) == 4  # One previous valid turn, then the existing three-failure stop.
    assert all(row['verdict'] == 'could_not_run' and 'reply' not in row for row in target.starter_result_rows())


def test_api_preserves_agui_transport_and_native_tool_turns_are_explicitly_unsupported(http_endpoint):
    target, requests, _ = http_endpoint
    value = validate_agent_endpoint({'url': URL, 'transport': 'ag_ui', 'auth': {'type': 'none'}})
    assert value['transport'] == 'ag_ui'
    with pytest.raises(RunnerError, match='native tool turns'):
        target.complete_messages([{'role': 'user', 'content': 'hi'}], tools=[])
    assert requests == []
