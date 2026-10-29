"""Connection auth replacement is bounded and never reuses a rejected key."""
import ast
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

SOURCE = Path(__file__).resolve().parents[1] / 'backbond/scan-intake/server/app.py'


def invoke(statuses, tokens, caplog):
    tree = ast.parse(SOURCE.read_text())
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_post_connection_check')
    selected = iter(tokens)
    namespace = {'_headers': lambda data: {'Authorization': next(selected)},
                 'SCANNER_URL': 'https://scanner.invalid', 'logging': logging}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE), 'exec'), namespace)
    calls = []
    responses = iter(statuses)
    def post(url, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(status_code=next(responses))
    with caplog.at_level(logging.WARNING):
        response = namespace['_post_connection_check'](SimpleNamespace(post=post), {'tenant_id': 'partner-test'})
    assert 'secret-' not in caplog.text
    assert 'scanner.invalid' not in caplog.text
    return response.status_code, calls


@pytest.mark.parametrize('status', [200, 403, 422, 500])
def test_other_responses_are_never_retried(status, caplog):
    result, calls = invoke([status], ['Bearer secret-old'], caplog)
    assert result == status
    assert len(calls) == 1


def test_rejected_unchanged_credential_fails_loudly(caplog):
    result, calls = invoke([401], ['Bearer secret-old', 'Bearer secret-old'], caplog)
    assert result == 401
    assert len(calls) == 1
    assert 'replacement_available=False' in caplog.text


@pytest.mark.parametrize('replacement_status', [200, 401])
def test_replacement_is_tried_exactly_once(replacement_status, caplog):
    result, calls = invoke([401, replacement_status], ['Bearer secret-old', 'Bearer secret-new'], caplog)
    assert result == replacement_status
    assert [c['headers']['Authorization'] for c in calls] == ['Bearer secret-old', 'Bearer secret-new']
    assert 'replacement_available=True' in caplog.text
