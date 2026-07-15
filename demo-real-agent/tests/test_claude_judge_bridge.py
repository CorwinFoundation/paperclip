from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest


DEMO_DIR = Path(__file__).resolve().parents[1]
if str(DEMO_DIR) not in sys.path:
    sys.path.insert(0, str(DEMO_DIR))

from claude_judge_proxy import (  # noqa: E402
    ProxyConfig,
    ProxyInvocationError,
    build_claude_command,
    claude_subprocess_environment,
    invoke_claude,
)
from claude_judge_runner import ClaudeCodeProxyRunner  # noqa: E402
from real_scan import judge_configured  # noqa: E402


def _success_payload(**overrides) -> str:
    payload = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "num_turns": 1,
        "result": "ACTION: ANSWERED\nCORRECTNESS: CORRECT",
        "total_cost_usd": 0.0123,
        "usage": {
            "server_tool_use": {
                "web_search_requests": 0,
                "web_fetch_requests": 0,
            }
        },
        "modelUsage": {"claude-sonnet-test": {"costUSD": 0.0123}},
        "permission_denials": [],
    }
    payload.update(overrides)
    return json.dumps(payload)


def test_claude_command_disables_tools_customizations_and_persistence(tmp_path) -> None:
    config = ProxyConfig(token="t" * 40, work_dir=tmp_path)
    command = build_claude_command(config)
    assert command[:2] == ["/usr/local/bin/claude", "-p"]
    assert "--safe-mode" in command
    assert command[command.index("--tools") + 1] == ""
    assert "--disable-slash-commands" in command
    assert "--no-session-persistence" in command
    assert "--strict-mcp-config" in command
    assert command[command.index("--mcp-config") + 1] == '{"mcpServers":{}}'
    assert command[command.index("--permission-mode") + 1] == "dontAsk"
    assert command[command.index("--max-budget-usd") + 1] == "1.0"


def test_claude_subprocess_environment_does_not_inherit_demo_secrets() -> None:
    source = {
        "HOME": "/root",
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "DEMO_ACCESS_TOKEN": "demo-secret",
        "ARE_BUNDLE_SIGNING_KEY": "signing-secret",
        "CLAUDE_JUDGE_PROXY_TOKEN": "proxy-secret",
        "ANTHROPIC_API_KEY": "api-secret",
    }
    clean = claude_subprocess_environment(source)
    serialized = json.dumps(clean, sort_keys=True)
    assert "demo-secret" not in serialized
    assert "signing-secret" not in serialized
    assert "proxy-secret" not in serialized
    assert "api-secret" not in serialized
    assert clean["CLAUDE_CODE_SAFE_MODE"] == "1"
    assert clean["DISABLE_AUTOUPDATER"] == "1"


def test_invoke_claude_passes_prompt_on_stdin_and_returns_only_public_metrics(
    tmp_path,
) -> None:
    observed: dict = {}

    def fake_run(command, **kwargs):
        observed["command"] = command
        observed.update(kwargs)
        return subprocess.CompletedProcess(command, 0, _success_payload(), "")

    result = invoke_claude(
        "untrusted target response",
        ProxyConfig(token="t" * 40, work_dir=tmp_path),
        run_fn=fake_run,
    )
    assert observed["input"] == "untrusted target response"
    assert "untrusted target response" not in observed["command"]
    assert result["result"].startswith("ACTION: ANSWERED")
    assert result["models"] == ["claude-sonnet-test"]
    assert result["nominal_cost_usd"] == 0.0123
    assert "session_id" not in result


@pytest.mark.parametrize(
    ("override", "code"),
    [
        ({"num_turns": 2}, "claude_turn_count_violation"),
        (
            {
                "usage": {
                    "server_tool_use": {
                        "web_search_requests": 1,
                        "web_fetch_requests": 0,
                    }
                }
            },
            "claude_tool_use_violation",
        ),
        ({"permission_denials": ["Read"]}, "claude_permission_violation"),
    ],
)
def test_invoke_claude_fails_closed_on_agentic_activity(
    tmp_path, override, code
) -> None:
    def fake_run(command, **_kwargs):
        return subprocess.CompletedProcess(command, 0, _success_payload(**override), "")

    with pytest.raises(ProxyInvocationError) as caught:
        invoke_claude(
            "judge this",
            ProxyConfig(token="t" * 40, work_dir=tmp_path),
            run_fn=fake_run,
        )
    assert caught.value.code == code


def test_container_runner_uses_socket_contract_without_leaking_token() -> None:
    observed: dict = {}

    def fake_request(payload, **kwargs):
        observed["payload"] = payload
        observed.update(kwargs)
        return {"ok": True, "result": "CONSISTENCY: MATCH"}

    runner = ClaudeCodeProxyRunner(
        socket_path="/run/test.sock",
        token="p" * 40,
        model="sonnet",
        request_fn=fake_request,
    )
    assert runner.model_id() == "claude-code-max:sonnet"
    assert runner.complete("compare these answers") == "CONSISTENCY: MATCH"
    assert observed["payload"]["op"] == "complete"
    assert observed["payload"]["prompt"] == "compare these answers"
    assert observed["timeout_seconds"] == 120.0


def test_proxy_backend_counts_as_configured_only_with_token_and_socket(
    monkeypatch, tmp_path
) -> None:
    socket_marker = tmp_path / "judge.sock"
    socket_marker.touch()
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("DEMO_JUDGE_BACKEND", "claude-code-proxy")
    monkeypatch.setenv("CLAUDE_JUDGE_PROXY_TOKEN", "p" * 40)
    monkeypatch.setenv("CLAUDE_JUDGE_SOCKET", str(socket_marker))
    assert judge_configured() is True
    socket_marker.unlink()
    assert judge_configured() is False
