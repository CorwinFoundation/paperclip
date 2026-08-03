from __future__ import annotations

import copy
import json
import socket
import sqlite3
import sys
import types
from datetime import datetime, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric import ed25519

from post_bind_ingest.openllmetry_compat import (
    CompatibilityConfig,
    FrameworkInstrumentation,
    OpenLLMetryCompatibilityError,
    hardened_environment,
)
from post_bind_ingest.otel_driver import (
    OTelDriverError,
    OTelSequenceGuard,
    canonical_batch_sha256,
    build_signed_receiver_batch,
    map_otlp_json,
)
from post_bind_ingest.normalizer import normalize_receiver_events

from pathlib import Path

LOG_TRACKER_ROOT = Path(__file__).resolve().parents[2] / "log-tracker"
sys.path.insert(0, str(LOG_TRACKER_ROOT / "src"))
from log_tracker.write_pipeline import CONTRACT_VERSION, append_envelope, apply_migrations  # noqa: E402


NOW = datetime(2026, 8, 3, 17, 0, tzinfo=timezone.utc)


def _attr(key: str, value: str) -> dict:
    return {"key": key, "value": {"stringValue": value}}


def _fixture(framework: str, seed: int = 0) -> dict:
    marker = "1" if framework == "crewai" else "2"
    return {"resourceSpans": [{
        "resource": {"attributes": [_attr("service.name", f"{framework}-fixture")]},
        "scopeSpans": [{"spans": [{
            "traceId": marker * 32,
            "spanId": f"{seed:016x}",
            "name": f"{framework}.workflow",
            "startTimeUnixNano": "1785776399000000000",
            "endTimeUnixNano": "1785776400000000000",
            "attributes": [
                _attr("gen_ai.operation.name", "chat"),
                _attr("gen_ai.provider.name", framework),
                _attr("gen_ai.request.model", "deterministic-fixture-v1"),
            ],
        }]}],
    }]}


@pytest.mark.parametrize("framework", ["crewai", "langchain"])
def test_n5_fixture_canonical_bytes_and_digest_are_identical(framework: str) -> None:
    canonical = []
    for seed in range(5):
        events = map_otlp_json(
            _fixture(framework), source_id=f"{framework}-fixture", sequence=1,
            batch_id=f"{framework}-batch", guard=OTelSequenceGuard(), now=NOW,
        )
        canonical.append((json.dumps(events, sort_keys=True, separators=(",", ":")), canonical_batch_sha256(events)))
    assert len(set(canonical)) == 1
    assert canonical[0][0].count(f'"gen_ai.provider.name":"{framework}"') == 1


def test_feature_flag_defaults_off_and_restores_native_path(monkeypatch) -> None:
    monkeypatch.delenv("BACKBOND_OPENLLMETRY_COMPAT", raising=False)
    config = CompatibilityConfig.from_env()
    assert config.enabled is False
    assert FrameworkInstrumentation("crewai", config).enable() is False
    assert hardened_environment(config)["OTEL_TRACES_EXPORTER"] == "none"


@pytest.mark.parametrize("endpoint", ["https://collector.example:4318", "http://10.0.0.1:4318", "http://localhost:4318"])
def test_non_loopback_or_named_exporters_fail_closed(monkeypatch, endpoint: str) -> None:
    monkeypatch.setenv("BACKBOND_OTLP_LOOPBACK_ENDPOINT", endpoint)
    with pytest.raises(OpenLLMetryCompatibilityError):
        CompatibilityConfig.from_env()


@pytest.mark.parametrize("framework,module_name,class_name", [
    ("crewai", "opentelemetry.instrumentation.crewai", "CrewAIInstrumentor"),
    ("langchain", "opentelemetry.instrumentation.langchain", "LangchainInstrumentor"),
])
def test_exact_instrumentor_path_and_symmetric_unpatch(monkeypatch, framework, module_name, class_name) -> None:
    calls = []
    fake_type = type(class_name, (), {"instrument": lambda self: calls.append("on"), "uninstrument": lambda self: calls.append("off")})
    module = types.ModuleType(module_name)
    setattr(module, class_name, fake_type)
    monkeypatch.setitem(sys.modules, module_name, module)
    config = CompatibilityConfig(enabled=True, endpoint="http://127.0.0.1:4318")
    with FrameworkInstrumentation(framework, config):
        assert calls == ["on"]
    assert calls == ["on", "off"]


def test_unknown_content_secret_malformed_replay_reorder_and_tamper_reject_before_advance() -> None:
    for bad_attr in [
        _attr("unknown.attribute", "x"),
        _attr("gen_ai.prompt", "private prompt"),
        _attr("gen_ai.request.model", "api_key=sk-abcdefghijklmnop"),
    ]:
        request = _fixture("crewai")
        request["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["attributes"].append(bad_attr)
        guard = OTelSequenceGuard()
        with pytest.raises(OTelDriverError):
            map_otlp_json(request, source_id="s", sequence=1, batch_id="bad", guard=guard, now=NOW)
        assert guard.last_sequence == {} and guard.seen_batches == set()

    guard = OTelSequenceGuard()
    map_otlp_json(_fixture("crewai"), source_id="s", sequence=1, batch_id="one", guard=guard, now=NOW)
    with pytest.raises(OTelDriverError, match="replay"):
        map_otlp_json(_fixture("crewai"), source_id="s", sequence=2, batch_id="one", guard=guard, now=NOW)
    with pytest.raises(OTelDriverError, match="expected 2"):
        map_otlp_json(_fixture("crewai"), source_id="s", sequence=3, batch_id="three", guard=guard, now=NOW)
    tampered = copy.deepcopy(_fixture("crewai"))
    tampered["signature"] = "attacker"
    with pytest.raises(OTelDriverError):
        map_otlp_json(tampered, source_id="t", sequence=1, batch_id="t", guard=OTelSequenceGuard(), now=NOW)


def test_deny_by_default_socket_harness_records_no_non_loopback_attempt(monkeypatch) -> None:
    attempts = []
    original = socket.socket.connect

    def guarded_connect(sock, address):
        host = address[0]
        attempts.append(host)
        if host not in {"127.0.0.1", "::1"}:
            raise AssertionError(f"non-loopback socket attempt: {host}")
        return original(sock, address)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    config = CompatibilityConfig(enabled=True, endpoint="http://127.0.0.1:4318")
    hardened_environment(config)
    map_otlp_json(_fixture("langchain"), source_id="l", sequence=1, batch_id="l", guard=OTelSequenceGuard(), now=NOW)
    assert attempts == []


@pytest.mark.parametrize("framework", ["crewai", "langchain"])
def test_framework_event_reaches_signed_receiver_and_append_only_tracker(monkeypatch, framework: str) -> None:
    private_key = ed25519.Ed25519PrivateKey.generate()
    events = map_otlp_json(
        _fixture(framework), source_id=f"{framework}-fixture", sequence=1,
        batch_id=f"{framework}-batch", guard=OTelSequenceGuard(), now=NOW,
    )
    batch = build_signed_receiver_batch(
        events, customer_id="customer-pilot", agent_id=f"{framework}-agent",
        batch_id=f"{framework}-batch", issued_at="2026-08-03T17:00:00Z",
        key_id="pilot-key", private_key=private_key,
    )
    from post_bind_ingest.receiver import validator

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW

    monkeypatch.setattr(validator, "datetime", FrozenDateTime)
    normalized = normalize_receiver_events(batch, jwt_keyring={"pilot-key": private_key.public_key()})
    assert normalized[0]["provenance"]["source"]["evidence_tier"] == "vendor-local-redacted"

    envelope = {
        "contract_version": CONTRACT_VERSION,
        "hook": "scanner_run.completed",
        "event_id": f"evt-{framework}-pilot",
        "event_t": "2026-08-03T17:00:00Z",
        "source_system": "post-bind-ingest.otel_driver",
        "source_version": "otel-collector-direct-v1",
        "schema_version": "are-scanner-api-v1@v1.0",
        "redaction_class_map": {
            "scan_id": "clear", "agent_id": "hash", "customer_id": "hash",
            "purpose": "clear", "scanner_version": "clear", "corpus_version": "clear",
            "model_versions.otel_batch_sha256": "clear",
            "evidence_refs[].rubric_point_id": "clear", "evidence_refs[].test_id": "clear",
            "evidence_refs[].artifact_uri": "clear", "evidence_refs[].verdict": "clear",
        },
        "payload": {
            "scan_id": f"scan-{framework}-pilot", "agent_id": f"{framework}-agent",
            "customer_id": "customer-pilot", "purpose": "mid_term", "are_total": 70,
            "subscores": {"GOVERNANCE": 20, "ROBUSTNESS": 20, "BIAS_FAIRNESS": 15, "OPS": 15},
            "band": "B", "confidence": 1.0, "scanner_version": "0.5.0",
            "corpus_version": "frozen-0.5.0", "model_versions": {"otel_batch_sha256": batch["batch_sha256"]},
            "seed": 0, "run_timestamp": "2026-08-03T17:00:00Z", "uncovered_dimensions": [],
            "evidence_refs": [{"rubric_point_id": "OPS.DATA_HANDLING.LOCAL_INGEST", "test_id": f"{framework}-batch",
                "artifact_uri": f"manifest://{framework}-batch", "verdict": "pass", "weight": 1.0,
                "redaction_class": {"artifact_uri": "clear"}}],
        },
    }
    conn = sqlite3.connect(":memory:")
    apply_migrations(conn)
    append_envelope(conn, envelope)
    assert conn.execute("SELECT count(*) FROM scanner_runs").fetchone()[0] == 1
    with pytest.raises(sqlite3.IntegrityError):
        append_envelope(conn, envelope)
