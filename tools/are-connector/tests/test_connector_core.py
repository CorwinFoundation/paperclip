from __future__ import annotations

import pytest

from are_connector.canonical import canonical_json
from are_connector.crypto import EnvelopeSigner
from are_connector.crypto import b64url_decode
from are_connector.envelope import build_envelope
from are_connector.http import EgressAllowlist, PortalClient
from are_connector.redaction import RedactionPolicy, redacted_excerpts
from are_connector.telemetry import build_redacted_output_record
from cryptography.hazmat.primitives.asymmetric import ed25519


def test_canonical_json_sorts_keys_and_removes_spaces() -> None:
    assert canonical_json({"b": 2, "a": ["x", True, None]}) == b'{"a":["x",true,null],"b":2}'


def test_redacted_excerpt_hashes_without_raw_secret() -> None:
    policy = RedactionPolicy.trusted_for_tests()
    excerpts = redacted_excerpts("token=sk-secretsecretsecretsecret user jane@example.com", policy)
    assert excerpts[0]["pii_class"] == "email"
    assert "sk-" not in str(excerpts)
    assert "jane@example.com" not in str(excerpts)


def test_envelope_contains_signature_and_no_cleartext_body() -> None:
    policy = RedactionPolicy.trusted_for_tests()
    envelope = build_envelope(
        scan_id="scan-1",
        task_id="task-1",
        endpoint_url="https://agent.example/staging",
        auth_method="none",
        policy=policy,
        signer=EnvelopeSigner.generate(),
        expected_assertions=["tool_called"],
        response_text="hello jane@example.com",
    )
    assert envelope["signature"]
    assert envelope["signature_chain"] == []
    assert "hello jane" not in str(envelope)
    assert envelope["assertions"]["response_received"] is True


def test_failure_envelope_has_error_without_assertion_payload() -> None:
    policy = RedactionPolicy.trusted_for_tests()
    envelope = build_envelope(
        scan_id="scan-1",
        task_id="task-2",
        endpoint_url="https://agent.example/staging",
        auth_method="none",
        policy=policy,
        signer=EnvelopeSigner.generate(),
        expected_assertions=["schema_match"],
        response_text=None,
        error_class="timeout",
        error_detail="raw timeout detail with host",
    )
    assert envelope["assertions"] == {"response_received": False}
    assert envelope["error"]["class"] == "timeout"
    assert "raw timeout" not in str(envelope)


def test_egress_allowlist_blocks_unlisted_origin() -> None:
    allowlist = EgressAllowlist("https://portal.example", "https://agent.example/staging")
    allowlist.assert_allowed("https://portal.example/scans/x")
    allowlist.assert_allowed("https://agent.example/staging")
    with pytest.raises(PermissionError):
        allowlist.assert_allowed("https://other.example/")


def test_redacted_output_record_removes_pii_and_signs_canonical_payload() -> None:
    signer = EnvelopeSigner.generate()
    policy = RedactionPolicy(
        version="0.1",
        policy_body={
            "structural_fields": ["ssn"],
            "named_entities": {"deny": ["Acme Secret Project"], "allow": ["Backbond Inc"]},
        },
        policy_signature="customer-policy-sig",
    )
    record = build_redacted_output_record(
        connector_id="connector-1",
        dp_id="dp-1",
        agent_call_id="call-1",
        agent_output_text='{"message":"Jane Smith jane@example.com token=sk-secretsecretsecretsecret","ssn":"123-45-6789","project":"Acme Secret Project"}',
        policy=policy,
        signer=signer,
        agent_endpoint_config={"url": "https://agent.example/staging"},
        capture_window_start="2026-06-12T00:00:00Z",
    )

    body = str(record)
    assert "jane@example.com" not in body
    assert "Jane Smith" not in body
    assert "123-45-6789" not in body
    assert "sk-" not in body
    assert "Acme Secret Project" not in body
    assert record["structural_fields_redacted"] == ["ssn"]

    signature = record.pop("connector_signature")
    public_key = signer.private_key.public_key()
    assert isinstance(public_key, ed25519.Ed25519PublicKey)
    public_key.verify(b64url_decode(signature), canonical_json(record))


def test_post_redacted_record_uses_telemetry_endpoint_and_idempotency_key(monkeypatch) -> None:
    calls = []

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"accepted": True}

    def fake_request(method, url, headers=None, **kwargs):
        calls.append({"method": method, "url": url, "headers": headers or {}, "kwargs": kwargs})
        return Response()

    allowlist = EgressAllowlist("https://portal.example")
    client = PortalClient("https://portal.example", allowlist, bearer_token="token")
    monkeypatch.setattr(client.session, "request", fake_request)

    result = client.post_redacted_record({"connector_id": "connector-1", "agent_call_id": "call-1"})

    assert result == {"accepted": True}
    assert calls[0]["method"] == "POST"
    assert calls[0]["url"] == "https://portal.example/telemetry/redacted-record"
    assert calls[0]["headers"]["Authorization"] == "Bearer token"
    assert calls[0]["headers"]["Idempotency-Key"] == "connector-1:call-1"
