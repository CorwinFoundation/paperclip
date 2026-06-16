"""(customer_id, manifest_sha256) replay returns identical events, no side-effects."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from post_bind_ingest.ingest_gateway.idempotency import IdempotencyStore
from post_bind_ingest.ingest_gateway.manifest_receiver import (
    GatewayResult,
    ManifestReceiver,
)
from post_bind_ingest.ingest_gateway.dead_letter import DeadLetterQueue
from post_bind_ingest.ingest_gateway.manifest_signature import sign_manifest_body
from post_bind_ingest.normalizer.transcript_sha import canonical_json


KEY = b"fixture-key-do-not-use-in-prod-0123456789ab"
CUSTOMER = "11111111-1111-4111-8111-111111111111"
AGENT = "22222222-2222-4222-8222-222222222222"
DAY = "2026-06-17"


def _make_manifest(tmp_path: Path) -> tuple[Path, dict, str]:
    root = tmp_path / "s3"
    raw_dir = root / CUSTOMER / "raw" / DAY
    raw_dir.mkdir(parents=True)
    object_bytes = b'{"kind":"agent.invocation","payload":{"session_id":"11111111-1111-4111-8111-111111111111","agent_version":"v1","scanner_version_at_bind":"v0.3.2","t_event":"2026-06-17T00:00:00Z"}}\n'
    object_sha = "sha256:" + hashlib.sha256(object_bytes).hexdigest()
    (raw_dir / object_sha).write_bytes(object_bytes)
    body = {
        "customer_id": CUSTOMER,
        "agent_id": AGENT,
        "day_utc": DAY,
        "schema_version": "log-event-schema-v1.0",
        "log_objects": [
            {"sha256": object_sha, "byte_range": [0, len(object_bytes)],
             "content_type": "application/jsonl", "source_kind": "stdout"}
        ],
    }
    sig = sign_manifest_body(body, KEY)
    manifest = {**body, "signature": sig, "signature_alg": "hmac-sha256"}
    return root, manifest, object_sha


def test_record_and_check_first_call_returns_false(tmp_path: Path) -> None:
    store = IdempotencyStore(store_path=tmp_path / "idem.jsonl")
    assert store.record_and_check(CUSTOMER, "sha256:" + "a" * 64) is False


def test_record_and_check_replay_returns_true(tmp_path: Path) -> None:
    store = IdempotencyStore(store_path=tmp_path / "idem.jsonl")
    sha = "sha256:" + "a" * 64
    store.record_and_check(CUSTOMER, sha)
    assert store.record_and_check(CUSTOMER, sha) is True


def test_idempotency_persists_across_instances(tmp_path: Path) -> None:
    path = tmp_path / "idem.jsonl"
    store1 = IdempotencyStore(store_path=path)
    sha = "sha256:" + "b" * 64
    store1.record_and_check(CUSTOMER, sha)
    store2 = IdempotencyStore(store_path=path)
    assert store2.has_seen(CUSTOMER, sha) is True


def test_gateway_replay_marks_result_replayed(tmp_path: Path) -> None:
    root, manifest, _ = _make_manifest(tmp_path)
    receiver = ManifestReceiver(
        root=root,
        signing_keys={CUSTOMER: KEY},
        idempotency=IdempotencyStore(store_path=tmp_path / "idem.jsonl"),
        dead_letter=DeadLetterQueue(queue_path=tmp_path / "dlq.jsonl"),
    )
    first = receiver.receive(manifest)
    second = receiver.receive(manifest)
    assert first.accepted is True and first.replayed is False
    assert second.accepted is True and second.replayed is True
    assert first.manifest_sha == second.manifest_sha
