from __future__ import annotations

import base64
from datetime import datetime, UTC

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from post_bind_ingest.otel_driver import build_signed_receiver_batch
from post_bind_ingest.runtime_continuity import PersistentContinuityRuntime, RuntimeContinuityError


def _batch(key, seq: int, heartbeat: str):
    event = {"event_id": f"evt-{seq}", "event_type": "security_event", "logged_at": heartbeat,
             "payload": {"action_class": "heartbeat", "safe_counter": seq}, "redaction_applied": []}
    return build_signed_receiver_batch((event,), customer_id="vendor-1", agent_id="agent-1",
        batch_id=f"batch-{seq}", issued_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        key_id="key-1", private_key=key)


def test_persistent_runtime_restart_refusal_recovery_and_privacy(tmp_path):
    key = ed25519.Ed25519PrivateKey.generate()
    public = base64.urlsafe_b64encode(key.public_key().public_bytes(serialization.Encoding.Raw,
        serialization.PublicFormat.Raw)).rstrip(b"=").decode()
    keyring = {"key-1": public}
    db = tmp_path / "continuity.sqlite"
    runtime = PersistentContinuityRuntime(db, enabled=True)
    for seq in (1, 2):
        runtime.ingest_signed_batch(_batch(key, seq, "2026-08-16T10:00:00Z"), jwt_keyring=keyring,
            tenant_id="vendor-1", deployment_id="deploy-1", collector_id="collector-1", batch_seq=seq,
            heartbeat_at="2026-08-16T10:00:00Z")
    runtime.close()

    runtime = PersistentContinuityRuntime(db, enabled=True)
    with pytest.raises(RuntimeContinuityError, match="sequence_skipped"):
        runtime.ingest_signed_batch(_batch(key, 4, "2026-08-16T12:00:00Z"), jwt_keyring=keyring,
            tenant_id="vendor-1", deployment_id="deploy-1", collector_id="collector-1", batch_seq=4,
            heartbeat_at="2026-08-16T12:00:00Z")
    with pytest.raises(RuntimeContinuityError, match="sequence_replayed_or_reset"):
        runtime.ingest_signed_batch(_batch(key, 1, "2026-08-16T12:00:00Z"), jwt_keyring=keyring,
            tenant_id="vendor-1", deployment_id="deploy-1", collector_id="collector-1", batch_seq=1,
            heartbeat_at="2026-08-16T12:00:00Z")
    stale = [runtime.decide(action_class=action, tenant_id="vendor-1", agent_id="agent-1", deployment_id="deploy-1",
        collector_id="collector-1", evaluated_at="2026-08-16T12:00:01Z", heartbeat_max_age_seconds=3600)
        for action in ("attach", "renew")]
    assert all(not decision["permitted"] and "heartbeat_stale" in decision["gap_state"]["reasons"] for decision in stale)
    missing = runtime.decide(action_class="attach", tenant_id="vendor-1", agent_id="agent-missing",
        deployment_id="deploy-1", collector_id="collector-1", evaluated_at="2026-08-16T12:00:01Z",
        heartbeat_max_age_seconds=3600)
    assert not missing["permitted"]
    assert {"heartbeat_missing", "no_sequence_data"} <= set(missing["gap_state"]["reasons"])
    repeat = runtime.decide(action_class="renew", tenant_id="vendor-1", agent_id="agent-1", deployment_id="deploy-1",
        collector_id="collector-1", evaluated_at="2026-08-16T12:00:01Z", heartbeat_max_age_seconds=3600)
    stale_renew_hash = runtime.audit_records()[-3]["input_hash"]
    assert runtime.audit_records()[-1]["input_hash"] == stale_renew_hash

    runtime.ingest_signed_batch(_batch(key, 3, "2026-08-16T12:00:02Z"), jwt_keyring=keyring,
        tenant_id="vendor-1", deployment_id="deploy-1", collector_id="collector-1", batch_seq=3,
        heartbeat_at="2026-08-16T12:00:02Z")
    recovered = runtime.decide(action_class="renew", tenant_id="vendor-1", agent_id="agent-1", deployment_id="deploy-1",
        collector_id="collector-1", evaluated_at="2026-08-16T12:00:03Z", heartbeat_max_age_seconds=3600)
    assert recovered["permitted"]
    assert [event["payload"]["event"] for event in recovered["audit_events"]] == ["telemetry_gap_recovered"]
    records = runtime.audit_records()
    assert [record["reason_code"] for record in records].count("sequence_skipped") == 1
    assert [record["reason_code"] for record in records].count("sequence_replayed_or_reset") == 1
    assert sum(record["reason_code"] == "eligible" for record in records) == 1
    serialized = "".join(record["decision_json"] for record in records)
    assert all(secret not in serialized for secret in ("raw prompt", "agent weights", "credential", "tool arguments"))
    runtime.close()
