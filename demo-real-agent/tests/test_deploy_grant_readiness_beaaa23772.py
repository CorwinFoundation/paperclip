from __future__ import annotations

import importlib.machinery
import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HELPER_PATH = ROOT / "artifacts/BEAAA-23772/candidate/backbond-real-agent-demo-secret-preflight"


def _load_helper():
    loader = importlib.machinery.SourceFileLoader("beaaa23772_preflight", str(HELPER_PATH))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


helper = _load_helper()


def _configured_proxy(*_args, **_kwargs):
    return [
        "DEMO_ACCESS_TOKEN",
        "ARE_BUNDLE_SIGNING_KEY",
        "DEMO_JUDGE_BACKEND",
        "CLAUDE_JUDGE_PROXY_TOKEN",
    ]


def test_missing_configured_judge_socket_preserves_grant(monkeypatch, capsys):
    consumed = False

    def forbidden_consume():
        nonlocal consumed
        consumed = True
        raise AssertionError("grant must not be consumed")

    monkeypatch.setattr(helper, "validate", _configured_proxy)
    monkeypatch.setattr(helper, "judge_socket_status", lambda: "missing")
    monkeypatch.setattr(helper, "consume_authorization", forbidden_consume)

    assert helper.main([]) == 1
    assert consumed is False
    assert "JUDGE_SOCKET: missing" in capsys.readouterr().out


def test_ready_configured_judge_socket_consumes_once(monkeypatch, capsys):
    calls = 0

    def consume():
        nonlocal calls
        calls += 1
        return helper.RELEASE_SHA

    monkeypatch.setattr(helper, "validate", _configured_proxy)
    monkeypatch.setattr(helper, "judge_socket_status", lambda: "ready")
    monkeypatch.setattr(helper, "consume_authorization", consume)

    assert helper.main([]) == 0
    assert calls == 1
    output = capsys.readouterr().out
    assert "JUDGE_SOCKET: ready" in output
    assert f"deploy_authorization: consumed {helper.RELEASE_SHA}" in output


def test_non_proxy_configuration_keeps_existing_consumption_path(monkeypatch):
    calls = 0

    def consume():
        nonlocal calls
        calls += 1
        return helper.RELEASE_SHA

    monkeypatch.setattr(
        helper,
        "validate",
        lambda: ["DEMO_ACCESS_TOKEN", "ARE_BUNDLE_SIGNING_KEY", "ANTHROPIC_API_KEY"],
    )
    monkeypatch.setattr(helper, "consume_authorization", consume)

    assert helper.main([]) == 0
    assert calls == 1


def test_candidate_diff_is_only_the_preconsume_missing_gate():
    candidate = HELPER_PATH.read_text()
    baseline = (HELPER_PATH.parent / "backbond-real-agent-demo-secret-preflight.baseline-9ecc6053").read_text()
    assert candidate.replace(
        '        if socket_status == "missing":\n            return 1\n', ""
    ) == baseline
