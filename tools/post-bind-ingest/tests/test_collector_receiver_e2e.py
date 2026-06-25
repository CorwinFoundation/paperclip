from __future__ import annotations

import base64
import json
import subprocess
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from post_bind_ingest.normalizer import normalize_receiver_events


KEY_ID = "bind-key-e2e"


def test_collector_signed_batch_round_trips_through_receiver() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    collector_dir = repo_root / "tools" / "are-collector"
    private_key = ed25519.Ed25519PrivateKey.generate()
    seed = private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_key_raw = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    private_key_b64 = base64.b64encode(seed + public_key_raw).decode("ascii")
    public_key_b64url = base64.urlsafe_b64encode(public_key_raw).rstrip(b"=").decode("ascii")

    fixture = (
        b'{"event_type":"tool_call","event_id":"evt-e2e",'
        b'"logged_at":"2026-06-16T18:00:00Z",'
        b'"payload":{"args":{"email":"jane@example.com"}}}\n'
    )
    proc = subprocess.run(
        [
            "go",
            "run",
            "./cmd/are-collector",
            "-mode",
            "fixture-smoke",
            "-source",
            "datadog",
            "-customer-id",
            "customer-e2e",
            "-agent-id",
            "agent-e2e",
            "-batch-id",
            "batch-e2e",
            "-key-id",
            KEY_ID,
            "-ed25519-private-key-b64",
            private_key_b64,
            "-policy-json",
            '{"args.email":"redact-on-receipt"}',
        ],
        cwd=collector_dir,
        input=fixture,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )

    batch = json.loads(proc.stdout)
    normalized = normalize_receiver_events(batch, jwt_keyring={KEY_ID: public_key_b64url})

    assert batch["schema_version"] == "log-event-schema-v1.0"
    assert normalized[0]["event_id"] == "evt-e2e"
    assert normalized[0]["redactions"] == ({"path": "args.email", "redaction_class": "validated"},)
