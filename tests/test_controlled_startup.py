"""Offline startup and actual worker-boundary checks; no network or permit arming."""
import ast
import json
from contextlib import nullcontext
from pathlib import Path
from queue import Queue
from types import SimpleNamespace

import pytest
from harness.controlled_target_policy import startup_target_config
from harness.controlled_permit_loader import AdmissionClosed


def configured(tmp_path, monkeypatch):
    folder = tmp_path / 'private'
    folder.mkdir(mode=0o700)
    path = folder / 'startup.json'
    config = dict(path='/protected/policy.json', expected_sha256='a'*64)
    path.write_text(json.dumps(config))
    path.chmod(0o600)
    monkeypatch.setenv('ARE_CONTROLLED_TARGET_CONFIG', str(path))
    return path, config


def test_ordinary_ignores_missing_or_invalid_setting(monkeypatch):
    monkeypatch.setenv('ARE_CONTROLLED_TARGET_CONFIG', '/does/not/exist')
    assert startup_target_config({}) is None
    assert startup_target_config(None) is None


@pytest.mark.parametrize('bad', ['unset', 'relative', 'missing', 'public', 'symlink', 'duplicate', 'oversize'])
def test_controlled_configuration_closed(tmp_path, monkeypatch, bad):
    path, _ = configured(tmp_path, monkeypatch)
    if bad == 'unset': monkeypatch.delenv('ARE_CONTROLLED_TARGET_CONFIG')
    elif bad == 'relative': monkeypatch.setenv('ARE_CONTROLLED_TARGET_CONFIG', 'relative.json')
    elif bad == 'missing': path.unlink()
    elif bad == 'public': path.chmod(0o644)
    elif bad == 'symlink':
        link = path.parent / 'link'
        link.symlink_to(path)
        monkeypatch.setenv('ARE_CONTROLLED_TARGET_CONFIG', str(link))
    elif bad == 'duplicate': path.write_text('{"payload":{},"payload":{}}')
    elif bad == 'oversize': path.write_text('x'*65537)
    with pytest.raises(AdmissionClosed):
        startup_target_config({'_controlled_permit': {}})


def worker():
    # Compile the actual production worker unchanged, with inert dependencies.
    source = ast.parse(Path('api/scan_runtime_factory.py').read_text())
    fn = next(n for n in source.body if isinstance(n, ast.FunctionDef) and n.name == '_prober_target')
    module = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), fn], type_ignores=[])
    scope = {'ScopedEgressAllowlist': lambda _: nullcontext()}
    exec(compile(ast.fix_missing_locations(module), 'actual-worker', 'exec'), scope)
    return scope


@pytest.mark.parametrize('controlled', [False, True])
def test_worker_forwards_only_service_configuration(tmp_path, monkeypatch, controlled):
    _, config = configured(tmp_path, monkeypatch)
    scope = worker()
    calls = []
    scope['run_scan'] = lambda *a, **kw: calls.append(kw) or 'result'
    results = Queue()
    scope['_prober_target'](scan_request=SimpleNamespace(endpoint_config={'_controlled_permit': {}} if controlled else {}),
        vault=None, channel=SimpleNamespace(send_end_of_scan=lambda: None), corpus_tarball_path=None,
        probe_run_fn=None, runner_factory=None, agent_endpoint_url='https://offline.invalid', result_queue=results,
        controlled_execution_payload={'credential_secret': 'memory-only'} if controlled else None)
    assert results.get() == ('ok', 'result')
    assert ('controlled_target_config' in calls[0]) is controlled
    if controlled: assert calls[0]['controlled_target_config'] == config


def test_missing_config_worker_never_calls_orchestrator(monkeypatch):
    monkeypatch.delenv('ARE_CONTROLLED_TARGET_CONFIG', raising=False)
    scope = worker()
    calls = []
    scope['run_scan'] = lambda *a, **kw: calls.append(kw)
    results = Queue()
    ended = []
    scope['_prober_target'](scan_request=SimpleNamespace(endpoint_config={'_controlled_permit': {}}),
        vault=None, channel=SimpleNamespace(send_end_of_scan=lambda: ended.append(True)), corpus_tarball_path=None,
        probe_run_fn=None, runner_factory=None, agent_endpoint_url='https://offline.invalid', result_queue=results)
    assert results.get()[0] == 'err'
    assert calls == []
    assert ended == [True]


@pytest.mark.parametrize('extra', ['payload', 'credential_secret', 'token'])
def test_startup_rejects_secret_bearing_schema(tmp_path, monkeypatch, extra):
    path, config = configured(tmp_path, monkeypatch)
    config[extra] = 'inert-forbidden-fixture'
    path.write_text(json.dumps(config))
    with pytest.raises(AdmissionClosed):
        startup_target_config({'_controlled_permit': {}})


def test_digest_only_config_and_missing_memory_fail_before_orchestrator(tmp_path, monkeypatch):
    path, config = configured(tmp_path, monkeypatch)
    assert startup_target_config({'_controlled_permit': {}}) == config
    assert set(json.loads(path.read_text())) == {'path', 'expected_sha256'}
    scope = worker()
    scope['run_scan'] = lambda *a, **kw: pytest.fail('lost context must not execute')
    results = Queue()
    scope['_prober_target'](scan_request=SimpleNamespace(endpoint_config={'_controlled_permit': {}}),
        vault=None, channel=SimpleNamespace(send_end_of_scan=lambda: None), corpus_tarball_path=None,
        probe_run_fn=None, runner_factory=None, agent_endpoint_url='https://offline.invalid', result_queue=results)
    assert results.get()[0] == 'err'
