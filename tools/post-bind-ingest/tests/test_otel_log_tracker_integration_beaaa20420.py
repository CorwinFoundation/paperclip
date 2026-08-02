from __future__ import annotations

import copy
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric import ed25519

from post_bind_ingest.egress import EgressConfig, build_monitor_artifact, transmit_monitor_artifact
from post_bind_ingest.normalizer import normalize_receiver_events
from post_bind_ingest.otel_driver import (
    OTelSequenceGuard,
    build_signed_receiver_batch,
    map_otlp_json,
)
from post_bind_ingest.receiver import ReceiverSchemaError

LOG_TRACKER_ROOT = Path(__file__).resolve().parents[2] / "log-tracker"
sys.path.insert(0, str(LOG_TRACKER_ROOT / "src"))
from log_tracker.write_pipeline import CONTRACT_VERSION, append_envelope, apply_migrations  # noqa: E402


NOW = datetime(2026, 8, 2, 22, 50, tzinfo=timezone.utc)


def _attr(key: str, value: str) -> dict:
    return {"key": key, "value": {"stringValue": value}}


def _otlp_payload() -> dict:
    return {
        "resourceSpans": [{
            "resource": {"attributes": [_attr("service.name", "vendor-agent")]},
            "scopeSpans": [{"spans": [{
                "traceId": "a" * 32,
                "spanId": "b" * 16,
                "name": "chat",
                "startTimeUnixNano": "1785710999000000000",
                "endTimeUnixNano": "1785711000000000000",
                "attributes": [_attr("gen_ai.operation.name", "chat")],
            }]}],
        }],
    }


def _signed_batch(private_key: ed25519.Ed25519PrivateKey) -> dict:
    events = map_otlp_json(
        _otlp_payload(),
        source_id="vendor-source-1",
        sequence=1,
        batch_id="otel-batch-1",
        guard=OTelSequenceGuard(),
        now=NOW,
    )
    return build_signed_receiver_batch(
        events,
        customer_id="customer-1",
        agent_id="agent-1",
        batch_id="otel-batch-1",
        issued_at=NOW.isoformat().replace("+00:00", "Z"),
        key_id="vendor-key-1",
        private_key=private_key,
    )


def test_otel_signed_receiver_zero_egress_and_append_only_log_tracker(monkeypatch) -> None:
    private_key = ed25519.Ed25519PrivateKey.generate()
    batch = _signed_batch(private_key)
    monkeypatch.setattr("post_bind_ingest.receiver.validator.datetime", _FrozenDateTime)
    normalized = normalize_receiver_events(
        batch,
        jwt_keyring={"vendor-key-1": private_key.public_key()},
    )

    assert normalized[0]["event_type"] == "llm_completion"
    assert normalized[0]["event_ingest"]["manifest_id"] == "otel-batch-1"
    artifact = build_monitor_artifact(normalized, manifest_ref="manifest://otel-batch-1")
    sent: list[bytes] = []
    egress = transmit_monitor_artifact(
        artifact,
        config=EgressConfig(),
        transport=lambda _endpoint, payload: sent.append(payload),
    )
    assert egress.transmitted is False
    assert sent == []

    conn = sqlite3.connect(":memory:")
    apply_migrations(conn)
    append_envelope(conn, _tracker_envelope(batch["batch_sha256"]))
    row = conn.execute(
        "SELECT event_id, payload_json FROM scanner_runs WHERE scan_id = ?",
        ("scan-otel-isolated-1",),
    ).fetchone()
    assert row is not None
    assert "manifest://otel-batch-1" in row[1]
    with pytest.raises(sqlite3.IntegrityError):
        append_envelope(conn, _tracker_envelope(batch["batch_sha256"]))
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute("UPDATE scanner_runs SET band = 'A' WHERE scan_id = 'scan-otel-isolated-1'")


def test_tampered_mapped_event_is_rejected_by_signed_receiver(monkeypatch) -> None:
    private_key = ed25519.Ed25519PrivateKey.generate()
    batch = _signed_batch(private_key)
    tampered = copy.deepcopy(batch)
    tampered["events"][0]["payload"]["span"]["gen_ai.operation.name"] = "tampered"
    monkeypatch.setattr("post_bind_ingest.receiver.validator.datetime", _FrozenDateTime)
    with pytest.raises(ReceiverSchemaError, match="batch_sha256"):
        normalize_receiver_events(tampered, jwt_keyring={"vendor-key-1": private_key.public_key()})


class _FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW


def _tracker_envelope(batch_sha256: str) -> dict:
    return {
        "contract_version": CONTRACT_VERSION,
        "hook": "scanner_run.completed",
        "event_id": "evt-otel-isolated-1",
        "event_t": "2026-08-02T22:50:00Z",
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
            "scan_id": "scan-otel-isolated-1", "agent_id": "agent-1", "customer_id": "customer-1",
            "purpose": "mid_term", "are_total": 70,
            "subscores": {"GOVERNANCE": 20, "ROBUSTNESS": 20, "BIAS_FAIRNESS": 15, "OPS": 15},
            "band": "B", "confidence": 1.0, "scanner_version": "0.5.0",
            "corpus_version": "frozen-0.5.0", "model_versions": {"otel_batch_sha256": batch_sha256},
            "seed": 0, "run_timestamp": "2026-08-02T22:50:00Z", "uncovered_dimensions": [],
            "evidence_refs": [{"rubric_point_id": "P4.4", "test_id": "otel-batch-1",
                "artifact_uri": "manifest://otel-batch-1", "verdict": "pass", "weight": 1.0,
                "redaction_class": {"artifact_uri": "clear"}}],
        },
    }
