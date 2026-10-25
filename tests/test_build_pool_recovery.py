"""Exercise admission ordering and crash recovery without a VM or credentials."""
import importlib.util
import hashlib
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def broker_module(monkeypatch, tmp_path):
    source = Path(__file__).resolve().parents[1] / "scripts/ci"
    policy_spec = importlib.util.spec_from_file_location("build_pool_policy", source / "build_pool_policy.py")
    policy = importlib.util.module_from_spec(policy_spec)
    policy_spec.loader.exec_module(policy)
    worker = ModuleType("build_pool_worker")
    worker.STATE, worker.Worker = tmp_path, MagicMock()
    monkeypatch.setitem(sys.modules, "build_pool_policy", policy)
    monkeypatch.setitem(sys.modules, "build_pool_worker", worker)
    monkeypatch.setitem(sys.modules, "fcntl", SimpleNamespace())
    spec = importlib.util.spec_from_file_location("build_pool_broker_test", source / "build_pool_broker.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # Durable fsync/ownership is a Linux acceptance check; tests below concern
    # journal semantics and API mutation order, and also run on Windows.
    monkeypatch.setattr(module, "save", lambda value: module.JOURNAL.write_text(json.dumps(value)))
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="Runner.Listener\n"))
    return module


def test_cleanup_failure_never_restores_capacity_or_forgets_journal(broker_module):
    module = broker_module
    state = dict(identity="a" * 32, role="publish", runner_id=123, restore=["/first", "/second"])
    module.save(state)
    module.Worker.return_value.cleanup.side_effect = RuntimeError("still mounted")
    github = MagicMock()
    with pytest.raises(RuntimeError, match="still mounted"):
        module.Broker(github).recover()
    github.request.assert_not_called()
    assert json.loads(module.JOURNAL.read_text()) == state


def test_interrupted_image_preparation_keeps_reservation_closed(broker_module):
    module = broker_module
    module.save(dict(identity="a" * 32, role="build", restore=["/first"], bootstrap=True))
    github = MagicMock()
    with pytest.raises(RuntimeError, match="image preparation reservation"):
        module.Broker(github).recover()
    module.Worker.assert_not_called()
    github.request.assert_not_called()
    assert module.JOURNAL.exists()


def test_recovery_deletes_only_recorded_jit_runner_before_restoring_exact_labels(broker_module):
    module = broker_module
    module.save(dict(identity="b" * 32, role="build", runner_id=99, restore=["/first"]))
    events = []
    module.Worker.return_value.cleanup.side_effect = lambda: events.append("cleanup")
    github = MagicMock()
    github.request.side_effect = lambda *args: events.append(args)
    module.Broker(github).recover()
    assert events == ["cleanup", ("DELETE", "/repos/CorwinFoundation/are-scanner/actions/runners/99"),
                      ("POST", "/first/labels", {"labels": ["backbond-ci"]})]
    assert not module.JOURNAL.exists()


def test_api_restore_failure_retains_journal_for_next_restart(broker_module):
    module = broker_module
    module.save(dict(identity="c" * 32, role="build", restore=["/first"]))
    github = MagicMock()
    github.request.side_effect = RuntimeError("GitHub unavailable")
    with pytest.raises(RuntimeError):
        module.Broker(github).recover()
    assert module.JOURNAL.exists()


def test_drain_journals_intent_before_label_deletion_and_waits_for_stable_idle(broker_module):
    module = broker_module
    github = MagicMock()
    deleted = set()
    observed = []
    def request(method, path, data=None):
        if method == "DELETE":
            base = path.removesuffix("/labels/backbond-ci")
            assert base in json.loads(module.JOURNAL.read_text())["restore"]
            deleted.add(base)
            return
        repository, identity, name = next(x for x in module.EXISTING if path == f"/repos/{x[0]}/actions/runners/{x[1]}")
        observed.append(path)
        return dict(name=name, status="online", busy=False,
                    labels=[] if path in deleted else [{"name": "backbond-ci"}])
    github.request.side_effect = request
    state = dict(identity="d" * 32, role="build", restore=[])
    module.save(state)
    module.Broker(github).drain(state)
    assert len(deleted) == 2 and len(observed) == 6


def test_changed_runner_identity_closes_admission(broker_module):
    module = broker_module
    github = MagicMock()
    github.request.return_value = dict(name="ariclaw", status="online", busy=False, labels=[])
    with pytest.raises(RuntimeError, match="identity/health"):
        module.Broker(github).drain(dict(identity="e" * 32, role="build", restore=[]))
    assert all(call.args[0] == "GET" for call in github.request.call_args_list)


def test_late_local_worker_blocks_idle_api_admission(broker_module, monkeypatch):
    module = broker_module
    github = MagicMock()
    calls = []
    deleted = set()
    def request(method, path, data=None):
        if method == "DELETE":
            deleted.add(path.removesuffix("/labels/backbond-ci"))
            return
        _, _, name = next(x for x in module.EXISTING if path == f"/repos/{x[0]}/actions/runners/{x[1]}")
        return dict(name=name, status="online", busy=False,
                    labels=[] if path in deleted else [{"name": "backbond-ci"}])
    github.request.side_effect = request
    def processes(*args, **kwargs):
        calls.append(1)
        return SimpleNamespace(stdout="  Runner.Worker\n" if len(calls) == 1 else "Runner.Listener\n")
    monkeypatch.setattr(module.subprocess, "run", processes)
    module.Broker(github).drain(dict(identity="f" * 32, role="build", restore=[]))
    assert len(calls) == 3


def test_preexisting_capacity_reservation_is_not_stolen(broker_module):
    module = broker_module
    github = MagicMock()
    github.request.return_value = dict(name=module.EXISTING[0][2], status="online", busy=False, labels=[])
    with pytest.raises(RuntimeError, match="reserved elsewhere"):
        module.Broker(github).drain(dict(identity="a" * 32, role="build", restore=[]))
    assert all(call.args[0] == "GET" for call in github.request.call_args_list)


def test_changed_firewall_rules_close_admission(broker_module, monkeypatch):
    module = broker_module
    github = MagicMock()
    github.config = {"egress_sha256": hashlib.sha256(b"reviewed rules").hexdigest()}
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=b"changed rules"))
    with pytest.raises(RuntimeError, match="egress rules changed"):
        module.Broker(github).verify_egress()
    github.request.assert_not_called()
