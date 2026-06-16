from __future__ import annotations

import copy
import hashlib
import hmac
import json
from pathlib import Path

import pytest

from post_bind_ingest.manifest_signature import parse_manifest, verify_manifest_signature
from post_bind_ingest.normalizer import NormalizerError, normalize_manifest
from post_bind_ingest.redaction import RedactedField, normalize_payload_redaction_plan, apply_redaction, RedactionError
from post_bind_ingest.invariants import InvariantError, ManifestIdempotencyStore


def _manifest_for_test(algorithm: str = "hmac-sha256", secret: str = "manifest-secret") -> str:
    manifest = {
        "manifest_version": "1",
        "manifest_id": "test-manifest-1",
        "customer_id": "customer-1",
        "schema_version": "log-event-schema-v1.0",
        "issued_at": "2026-06-16T15:00:00Z",
        "events": [
            {
                "event_id": "evt-1",
                "event_type": "tool_call",
                "logged_at": "2026-06-16T15:00:05Z",
                "payload": {
                    "tool": "calculator",
                    "args": {
                        "email": "jane@example.com",
                        "token": "sk-secret",
                    },
                },
                "redaction": {"field_redaction": {"args.email": "redact-on-receipt", "args.token": "hash"}},
            },
        ],
        "signature": {
            "algorithm": algorithm,
            "value": "",
        },
    }
    payload = json.dumps({k: v for k, v in manifest.items() if k != "signature"}, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if algorithm in {"hmac-sha256", "sha256-hmac"}:
        signature = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    elif algorithm == "sha256":
        signature = hashlib.sha256(payload).hexdigest()
    else:
        signature = "invalid"
    manifest["signature"]["value"] = signature
    return json.dumps(manifest)


def test_apply_redaction_hash_and_drop_paths() -> None:
    payload = {"a": {"secret": "abc", "visible": "x"}, "other": "value"}
    redacted, fields = apply_redaction(payload, {"a.secret": "hash", "other": "redact-on-receipt"})
    assert redacted["other"] == "[REDACTED]"
    assert redacted["a"]["secret"].startswith("sha256:")
    assert len(fields) == 2


def test_apply_redaction_clear_sets_null() -> None:
    payload = {"customer_id": "cust-123", "tool": "llm_completion"}
    redacted, fields = apply_redaction(payload, {"customer_id": "clear"})
    assert redacted["customer_id"] is None
    assert fields == (RedactedField(path="customer_id", redaction_class="clear"),)


def test_normalize_payload_redaction_plan() -> None:
    assert normalize_payload_redaction_plan({"field_redaction": {"a": "drop"}}) == {"a": "drop"}
    assert normalize_payload_redaction_plan({"a": "hash"}) == {"a": "hash"}
    with pytest.raises(RedactionError):
        normalize_payload_redaction_plan({"a": "bad"})


def test_normalize_manifest_applies_redaction() -> None:
    payload = _manifest_for_test()
    manifest = parse_manifest(payload)
    events = normalize_manifest(manifest)
    event = events[0]
    assert event["event_type"] == "tool_call"
    assert event["payload"]["args"]["email"] == "[REDACTED]"
    assert event["payload"]["args"]["token"].startswith("sha256:")
    assert event["redactions"] == (
        {"path": "args.email", "redaction_class": "redact-on-receipt"},
        {"path": "args.token", "redaction_class": "hash"},
    )


def test_normalize_manifest_rejects_unknown_event_type() -> None:
    raw = json.loads(_manifest_for_test())
    raw["events"][0]["event_type"] = "legacy_event"
    with pytest.raises(NormalizerError):
        normalize_manifest(raw)


def test_normalize_manifest_is_idempotent_for_replayed_manifest(tmp_path: Path) -> None:
    manifest = parse_manifest(_manifest_for_test())
    store = ManifestIdempotencyStore(path=tmp_path / "idempotent.state")
    first = normalize_manifest(manifest, idempotency_store=store)
    second = normalize_manifest(manifest, idempotency_store=store)
    assert first
    assert second == ()


def test_normalize_manifest_reordered_events_change_digest(tmp_path: Path) -> None:
    base_manifest = parse_manifest(_manifest_for_test())
    base_manifest["events"] = [
        base_manifest["events"][0],
        {
            "event_id": "evt-2",
            "event_type": "tool_call",
            "logged_at": "2026-06-16T15:00:10Z",
            "payload": {"tool": "browser", "args": {"url": "https://example.invalid"}},
        },
    ]
    store = ManifestIdempotencyStore(path=tmp_path / "idempotent.state")
    normalize_manifest(copy.deepcopy(base_manifest), idempotency_store=store)

    reordered_manifest = copy.deepcopy(base_manifest)
    reordered_manifest["events"] = list(reversed(reordered_manifest["events"]))
    with pytest.raises(InvariantError):
        normalize_manifest(reordered_manifest, idempotency_store=store)


def test_normalize_manifest_fails_on_bad_signature() -> None:
    raw = _manifest_for_test()
    tampered = json.loads(raw)
    tampered["signature"]["value"] = "000000"
    tampered["events"][0]["event_id"] = "evt-2"
    with pytest.raises(Exception):
        verify_manifest_signature(tampered, secret="manifest-secret")
