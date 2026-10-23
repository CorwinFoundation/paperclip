"""Offline failure-phase routing and log privacy; no target connections."""
from contextlib import nullcontext
from queue import Empty
from types import SimpleNamespace

import pytest
import api.scan_runtime_factory as factory
from tests.test_beaaa7840_scorer_recv_bound import _scan_request, _scan_result


class Process:
    exitcode = None
    def start(self):
        pass
    def is_alive(self):
        return True
    def join(self, timeout=None):
        pass
    def terminate(self):
        self.exitcode = -15


@pytest.mark.parametrize('phase', [
    'prober_result_receive', 'scorer_envelope_receive',
    'scorer_reported_error', 'prober_reported_error',
])
def test_failure_phase_and_precleanup_status(tmp_path, monkeypatch, caplog, phase):
    secret = 'SECRET-canary-https://private.example/token'
    class Queue:
        def get(self, **kwargs):
            if phase == 'prober_result_receive':
                raise Empty(secret)
            if phase == 'prober_reported_error':
                return ('err', secret, secret)
            return ('ok', _scan_result('scan-beaaa7840'))
    class Channel:
        def __init__(self, **kwargs):
            self.context = SimpleNamespace(Queue=lambda **kw: Queue(), Process=lambda **kw: Process())
        def recv_score_envelope(self, **kwargs):
            if phase == 'scorer_envelope_receive':
                raise Empty(secret)
            return ('err', secret, secret)
    monkeypatch.setattr(factory, 'WorkerChannel', Channel)
    monkeypatch.setattr(factory, 'spawn_scorer', lambda **kw: Process())
    monkeypatch.setattr(factory, 'ScopedEgressAllowlist', lambda *a, **kw: nullcontext())
    monkeypatch.setattr(factory, '_terminate', lambda *ps: [p.terminate() for p in ps])
    corpus = factory.build_v0_corpus_tarball(tmp_path / 'corpus')
    runtime = factory.make_production_scan_runtime(
        corpus_tarball_path=corpus, runner_factory=lambda *_: None,
        probe_run_fn=lambda _: iter(()),
        score_runtime=None if phase == 'prober_reported_error' else object(),
        score_envelope_registry={},
    )
    with pytest.raises(factory.OrchestratorError):
        runtime._run_scan_fn(_scan_request(factory.v0_corpus_snapshot_hash(corpus)), secret)
    records = [r for r in caplog.records if r.name == factory.__name__]
    assert len(records) == 1
    assert records[0].getMessage() == (
        f'scan_orchestrator_failed phase={phase} prober_alive=True prober_exitcode=None '
        'scorer_alive=True scorer_exitcode=None'
    )
    assert secret not in caplog.text
    assert records[0].exc_info is None


def test_unavailable_or_nonprimitive_status_is_private(caplog):
    class Unsafe:
        def is_alive(self):
            raise RuntimeError('SECRET')
    factory._log_worker_failure('prober_result_receive', Unsafe(),
                                SimpleNamespace(is_alive=lambda: 'SECRET', exitcode='SECRET'))
    assert 'SECRET' not in caplog.text
    assert 'prober_alive=None prober_exitcode=None scorer_alive=None scorer_exitcode=None' in caplog.text
