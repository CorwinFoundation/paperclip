from __future__ import annotations

import base64
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
import pytest

from post_bind_ingest.cli import main
from post_bind_ingest.receiver.schema import compute_batch_sha256


PRIVATE_KEY = ed25519.Ed25519PrivateKey.generate()
KEY_ID = "bind-key-1"
PUBLIC_KEY_B64 = base64.urlsafe_b64encode(
    PRIVATE_KEY.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
).rstrip(b"=").decode("ascii")
KEYRING_JSON = json.dumps({KEY_ID: PUBLIC_KEY_B64})


def _jwt_token(payload: dict) -> str:
    header = {"alg": "EdDSA", "typ": "JWT", "kid": KEY_ID}
    header_b64 = base64.urlsafe_b64encode(json.dumps(header, separators=(",", ":")).encode("utf-8")).rstrip(
        b"="
    ).decode("ascii")
    payload_b64 = base64.urlsafe_b64encode(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).rstrip(b"=").decode("ascii")
    signing = f"{header_b64}.{payload_b64}".encode("ascii")
    signature = PRIVATE_KEY.sign(signing)
    sig_b64 = base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")
    return f"{header_b64}.{payload_b64}.{sig_b64}"


def _receiver_payload() -> dict:
    issued_at = (datetime.now(timezone.utc) - timedelta(seconds=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
    events = [
        {
            "event_type": "tool_call",
            "event_id": "evt-1",
            "logged_at": "2026-06-16T15:00:05Z",
            "payload": {"tool_name": "calculator", "arg_count": 2},
            "redaction_applied": [],
        }
    ]
    batch_sha256 = f"sha256:{compute_batch_sha256(events)}"
    payload = {
        "schema_version": "log-event-schema-v1.0",
        "customer_id": "cust-1",
        "agent": "agent-1",
        "batch_id": "batch-1",
        "batch_sha256": batch_sha256,
        "event_count": 1,
        "issued_at": issued_at,
        "events": events,
    }
    payload["jwt"] = _jwt_token(
        {
            "iss": "are-collector",
            "sub": payload["customer_id"],
            "agent": payload["agent"],
            "schema_version": payload["schema_version"],
            "batch_id": payload["batch_id"],
            "batch_sha256": payload["batch_sha256"],
            "event_count": payload["event_count"],
            "issued_at": payload["issued_at"],
            "exp": int(datetime.now(timezone.utc).timestamp()) + 600,
        },
    )
    return payload


def _write_payload(path: Path) -> None:
    payload = _receiver_payload()
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def test_main_dry_run_prints_event_count(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    payload_path = tmp_path / "payload.json"
    _write_payload(payload_path)

    monkeypatch.setattr(
        sys,
        "argv",
        ["post-bind-ingest", str(payload_path), "--jwt-keyring-json", KEYRING_JSON, "--dry-run"],
    )
    main()

    output = capsys.readouterr().out.strip()
    assert output == "events_normalized=1"


def test_main_writes_normalized_events(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    payload_path = tmp_path / "payload.json"
    _write_payload(payload_path)

    monkeypatch.setattr(sys, "argv", ["post-bind-ingest", str(payload_path), "--jwt-keyring-json", KEYRING_JSON])
    main()

    first_line, *_ = capsys.readouterr().out.strip().splitlines()
    event = json.loads(first_line)
    assert event["event_id"] == "evt-1"
    assert event["event_type"] == "tool_call"
    assert event["event_ingest"]["manifest_id"] == "batch-1"


def test_main_receiver_mode_requires_public_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload_path = tmp_path / "payload.json"
    _write_payload(payload_path)

    monkeypatch.setattr(sys, "argv", [
        "post-bind-ingest",
        str(payload_path),
        "--mode",
        "receiver",
    ])
    with pytest.raises(SystemExit) as exc:
        main()

    assert str(exc.value) == "receiver mode requires --jwt-keyring-json"


def test_main_rejects_missing_jwt_keyring(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload_path = tmp_path / "payload.json"
    _write_payload(payload_path)

    monkeypatch.setattr(sys, "argv", ["post-bind-ingest", str(payload_path), "--mode", "receiver"])
    with pytest.raises(SystemExit):
        main()


def test_main_rejects_empty_payload(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload_path = tmp_path / "empty.json"
    payload_path.write_text("", encoding="utf-8")

    monkeypatch.setattr(sys, "argv", [
        "post-bind-ingest",
        str(payload_path),
        "--jwt-keyring-json",
        KEYRING_JSON,
    ])
    with pytest.raises(SystemExit) as exc:
        main()

    assert str(exc.value) == "receiver payload is empty"
