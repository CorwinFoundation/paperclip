"""BEAAA-14430 regression coverage for cross-surface CI gate semantics."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

import ci_plugin.gate as gate
import ci_plugin.runner as runner
from ci_plugin.client import Response, ScanClient, ScanTransportError
from ci_plugin.gate import GateDecision, EXIT_INFRA_ADVISORY
from ci_plugin.runner import Inputs


def _decision(exit_code: int, *, advisory: bool) -> GateDecision:
    return GateDecision(
        exit_code=exit_code,
        reason=f"test decision {exit_code}",
        bindable=False,
        advisory=advisory,
    )


def _set_required_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INPUT_AGENT_ENDPOINT", "https://agent.example.test")
    monkeypatch.setenv("INPUT_BACKBOND_API_KEY", "test-key")


def test_github_maps_infra_75_to_warning_and_process_success(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    canonical = _decision(EXIT_INFRA_ADVISORY, advisory=True)
    monkeypatch.setattr(runner, "run", lambda *_args, **_kwargs: canonical)
    _set_required_env(monkeypatch)
    output_path = tmp_path / "github-output.txt"
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_path))

    assert runner.main([]) == 0

    stdout = capsys.readouterr().out
    assert "::warning::test decision 75" in stdout
    result_line = output_path.read_text(encoding="utf-8").strip()
    payload = json.loads(result_line.removeprefix("result="))
    assert payload["exit_code"] == 75
    assert payload["advisory"] is True


def test_gitlab_preserves_infra_75_process_exit(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    canonical = _decision(EXIT_INFRA_ADVISORY, advisory=True)
    monkeypatch.setattr(runner, "run", lambda *_args, **_kwargs: canonical)
    _set_required_env(monkeypatch)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)

    assert runner.main([]) == 75
    assert "::warning::" not in capsys.readouterr().out


def test_missing_threshold_is_config_error_78_not_advisory() -> None:
    decision = gate.decide_exit(
        production_self_identified=False,
        infra_exhausted=False,
        webhook_verified=None,
        score=90.0,
        threshold=None,
        scan_tier="full",
    )

    assert getattr(gate, "EXIT_CONFIG_ERROR", None) == 78
    assert "EXIT_CONFIG_ERROR" in gate.__all__
    assert decision.exit_code == 78
    assert decision.advisory is False


def test_github_does_not_soften_config_error_78(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_error = _decision(78, advisory=False)
    monkeypatch.setattr(runner, "run", lambda *_args, **_kwargs: config_error)
    _set_required_env(monkeypatch)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)

    assert runner.main([]) == 78
    assert "::warning::" not in capsys.readouterr().out


def test_gitlab_allows_only_infra_advisory_75() -> None:
    template = Path("ci_plugin/templates/.gitlab-ci.yml")
    config = yaml.safe_load(template.read_text(encoding="utf-8"))
    assert config[".backbond_scan"]["allow_failure"]["exit_codes"] == [75]


class _StaticTransport:
    def __init__(self, responses: list[Response]) -> None:
        self.responses = responses

    def request(self, *_args, **_kwargs) -> Response:
        return self.responses.pop(0)


@pytest.mark.parametrize("operation", ["preflight", "submit", "poll"])
@pytest.mark.parametrize("body", [b"<html>not JSON</html>", b"\xff"])
def test_malformed_json_200_is_classified_as_transport_error(
    operation: str, body: bytes
) -> None:
    malformed = Response(status=200, body=body)
    client = ScanClient(
        base_url="https://scanner.example.test",
        api_key="test-key",
        transport=_StaticTransport([malformed]),
    )

    with pytest.raises(ScanTransportError, match="malformed JSON") as exc_info:
        if operation == "preflight":
            client.endpoint_self_identifies_production(
                "https://agent.example.test", None
            )
        elif operation == "submit":
            client.submit_scan(
                agent_endpoint="https://agent.example.test",
                auth_token=None,
                scan_tier="full",
            )
        else:
            client.poll_scan("scan-123", max_wait_s=0, sleep=lambda _s: None)

    assert exc_info.value.kind == "unreachable"
    assert "not JSON" not in str(exc_info.value)


def test_malformed_poll_json_flows_through_retry_to_infra_75() -> None:
    valid_preflight = Response(
        status=200, body=json.dumps({"environment": "staging"}).encode()
    )
    valid_submit = Response(
        status=202, body=json.dumps({"scan_id": "scan-123"}).encode()
    )
    malformed_poll = Response(status=200, body=b"not-json")
    decision = runner.run(
        Inputs(
            agent_endpoint="https://agent.example.test",
            backbond_api_key="test-key",
            backbond_base_url="https://scanner.example.test",
            fail_below_score=72.0,
            max_infra_retries=0,
        ),
        transport=_StaticTransport(
            [valid_preflight, valid_submit, malformed_poll]
        ),
        sleep=lambda _s: None,
        log=lambda _message: None,
    )

    assert decision.exit_code == EXIT_INFRA_ADVISORY
    assert decision.advisory is True
