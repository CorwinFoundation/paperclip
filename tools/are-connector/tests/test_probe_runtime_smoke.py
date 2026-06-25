from __future__ import annotations

import json
from pathlib import Path

from are_connector.probe_runtime import ProbeRuntime, ProbeSpec, VerdictRule, build_transcript_sha, load_probe_set
from are_connector.response_limits import MAX_RESPONSE_BYTES_ENV


def test_probe_runtime_fixture_parametrizes_two_toy_probes(probe_runtime: ProbeRuntime) -> None:
    probes = [
        ProbeSpec(
            probe_id="toy-pass",
            payload={"input": "allowed"},
            verdict_rule=VerdictRule(kind="regex", pattern=r"echo:allowed"),
            probe_set_version="connector_probe_runtime_smoke@0.1.0",
        ),
        ProbeSpec(
            probe_id="toy-fail",
            payload={"input": "blocked"},
            verdict_rule=VerdictRule(kind="equality", expected="not-this"),
            probe_set_version="connector_probe_runtime_smoke@0.1.0",
        ),
    ]

    verdicts = [probe_runtime.run(probe) for probe in probes]

    assert [verdict.status for verdict in verdicts] == ["pass", "fail"]
    for verdict in verdicts:
        assert verdict.scanner_version == "0.4.2"
        assert verdict.corpus_version == "connector_probe_runtime_smoke@0.1.0"
        assert verdict.model_versions == {"primary": "mock-http-agent@0.1.0", "judges": []}
        assert verdict.seed == 42
        assert verdict.probe_set_version == "connector_probe_runtime_smoke@0.1.0"
        assert len(verdict.transcript_sha) == 64
        assert verdict.raw_response_capped_bytes > 0
        assert verdict.evidence_ref.endswith(f"/{verdict.transcript_sha}")


def test_probe_set_loader_returns_specs(tmp_path: Path) -> None:
    (tmp_path / "p5_direct_v1.json").write_text(
        json.dumps(
            {
                "probe_set_version": "p5_direct_v1@0.1.0",
                "probes": [
                    {
                        "probe_id": "P5-DIRECT-001",
                        "payload": {"input": "x"},
                        "verdict_rule": {"kind": "regex", "pattern": "safe"},
                    }
                ],
            }
        )
    )

    probes = load_probe_set("p5_direct_v1", tmp_path)

    assert probes[0].probe_id == "P5-DIRECT-001"
    assert probes[0].probe_set_version == "p5_direct_v1@0.1.0"


def test_oversized_response_maps_to_error_without_raw_body(monkeypatch, agent_endpoint: str) -> None:
    monkeypatch.setenv(MAX_RESPONSE_BYTES_ENV, "8")
    runtime = ProbeRuntime(
        agent_endpoint=agent_endpoint,
        portal_base_url=agent_endpoint,
        scanner_version="0.4.2",
        corpus_version="connector_probe_runtime_smoke@0.1.0",
        model_versions={"primary": "mock-http-agent@0.1.0", "judges": []},
        seed=42,
        probe_set_version="connector_probe_runtime_smoke@0.1.0",
    )
    probe = ProbeSpec(
        probe_id="toy-oversized",
        payload={"input": "this response is too large"},
        verdict_rule=VerdictRule(kind="regex", pattern="echo"),
        probe_set_version="connector_probe_runtime_smoke@0.1.0",
    )

    verdict = runtime.run(probe)

    assert verdict.status == "error"
    assert verdict.reason == "error_oversized"
    assert verdict.raw_response_capped_bytes == 0
    assert "this response is too large" not in str(verdict.as_dict())


def test_transcript_hash_uses_redacted_secret_families() -> None:
    request_a = {
        "aws": "AKIA1234567890ABCDEF",
        "azure": "DefaultEndpointsProtocol=https;AccountName=acct;AccountKey=secretsecretsecret;EndpointSuffix=core.windows.net",
        "bearer": "Bearer abcdefghijklmnopqrstuvwxyz123456",
        "generic": "api_key=genericsecretvalue12345",
    }
    request_b = {
        "aws": "AKIAFEDCBA0987654321",
        "azure": "DefaultEndpointsProtocol=https;AccountName=acct;AccountKey=differentsecret;EndpointSuffix=core.windows.net",
        "bearer": "Bearer zyxwvutsrqponmlkjihgfedcba654321",
        "generic": "api_key=anothersecretvalue12345",
    }

    assert build_transcript_sha(request_a, {"response": "ok"}) == build_transcript_sha(request_b, {"response": "ok"})


def test_judge_rule_without_pinned_callout_is_error(probe_runtime: ProbeRuntime) -> None:
    verdict = probe_runtime.run(
        ProbeSpec(
            probe_id="toy-judge",
            payload={"input": "judge me"},
            verdict_rule=VerdictRule(kind="judge", judge_id=None),
            probe_set_version="connector_probe_runtime_smoke@0.1.0",
        )
    )

    assert verdict.status == "error"
    assert verdict.reason == "error_judge_unpinnable"
