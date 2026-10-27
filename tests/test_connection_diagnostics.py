import socket
import ssl
import httpx
import pytest
from types import SimpleNamespace
from harness.connection_diagnostics import failure_details, safe_excerpt
from harness.customer_runner import customer_agent_http_runner_factory, CustomerEndpointHTTPError
from harness.runner import RunnerError

@pytest.mark.parametrize('cause,kind', [
    (socket.gaierror(-2, 'name failure'), 'dns'),
    (ssl.SSLError('certificate failure'), 'tls'),
    (httpx.ReadTimeout('secret'), 'timeout'),
    (ConnectionRefusedError(111, 'refused'), 'connection_refused'),
    (ConnectionResetError(104, 'reset'), 'connection_reset'),
])
def test_transport_causes(cause, kind):
    outer = RunnerError('never expose this secret')
    outer.__cause__ = cause
    result = failure_details(outer, None, 12)
    assert result['error_class'] == kind
    assert result['elapsed_ms'] == 12
    assert 'target_status' not in result
    assert 'secret' not in str(result)

@pytest.mark.parametrize('status', [301, 401, 403, 404, 500])
def test_observed_http_status(status):
    result = failure_details(CustomerEndpointHTTPError(status, 'unsafe', 'Bearer private-key', {}), None, 1, ['private-key'])
    assert result['target_status'] == status
    assert result['failed_stage'] == ('auth' if status in (401, 403) else 'reachability')
    assert 'private-key' not in str(result)


def test_redact_before_truncation_and_escape():
    secret = 'long-custom-header-value'
    result = safe_excerpt('x' * 195 + secret + '<script>', [secret])
    assert secret[:5] not in result
    assert '<' not in safe_excerpt('<script>Authorization: Bearer other-key')
    assert 'other-key' not in safe_excerpt('Authorization: Bearer other-key')


def test_known_syllable_route_only():
    for url in ['https://apichat.syllable.cloud', 'https://apichat.syllable.cloud/api/v1/chats/send_message']:
        assert customer_agent_http_runner_factory(url, '')._base_url == 'https://apichat.syllable.cloud'
    other = customer_agent_http_runner_factory('https://apichat.syllable.cloud.evil.test/api/v1/chats/send_message', '')
    assert other._agent_endpoint.endswith('/api/v1/chats/send_message')
