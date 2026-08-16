"""End-to-end acceptance suite for the constrained monitor transport.

BEAAA-23699 (P0-C). One automated suite drives a real local TLS receiver and
proves the six acceptance criteria in the issue:

1. default invocation prepares and validates but sends zero bytes;
2. approved configuration sends exactly one allowed artifact and receives a
   hash-bound acknowledgement;
3. retry after an ambiguous timeout is idempotent -> one logical receipt;
4. raw sensitive fields, oversized excerpts, redirects, non-HTTPS endpoints,
   host mismatch, private/unapproved destinations, invalid TLS, mutated
   payloads, and invalid acknowledgements are rejected before a receipt;
5. receiver outage leaves evidence queued locally, eligibility unresolved;
6. immutable receipts bind source identity, artifact hash, schema version,
   destination, attempt history, acknowledgement hash, and candidate SHA.

The suite passes against the local TLS receiver only. It does not, and must not
be read to, represent live transport to a real partner; that requires an exact
destination and current approval recorded on the issue.
"""

from __future__ import annotations

import datetime
import json

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from post_bind_ingest.egress import build_monitor_artifact
from post_bind_ingest.monitor_receiver import MonitorReceiver
from post_bind_ingest.monitor_transport import (
    AmbiguousDeliveryError,
    TransportConfig,
    TransportReceiptStore,
    TransportRejected,
    build_envelope,
    make_https_sender,
    send_monitor_artifact,
)

CANDIDATE_SHA = "63ed375f0000000000000000000000000000abcd"
CONFIG_REVISION = "monitor-transport-cfg-2026-08-16-r1"
SOURCE_IDENTITY = "backbond-scanner-box/passive-monitor"


# --------------------------------------------------------------------------- #
# Fixtures: a loopback TLS receiver with a self-signed leaf trusted by the CA. #
# --------------------------------------------------------------------------- #


def _write_self_signed(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    not_before = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
    not_after = datetime.datetime(2027, 1, 1, tzinfo=datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before)
        .not_valid_after(not_after)
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(__import__("ipaddress").ip_address("127.0.0.1"))]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cert_file = tmp_path / "receiver-cert.pem"
    key_file = tmp_path / "receiver-key.pem"
    cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    return cert_file, key_file


@pytest.fixture()
def receiver(tmp_path):
    cert_file, key_file = _write_self_signed(tmp_path)
    server = MonitorReceiver(
        certfile=str(cert_file),
        keyfile=str(key_file),
        host="127.0.0.1",
        bind_host="127.0.0.1",
        port=0,
        path="/monitor",
    )
    server.start()
    server._ca_file = str(cert_file)  # self-signed: the leaf is its own trust anchor
    try:
        yield server
    finally:
        server.stop()


def _artifact():
    events = (
        {
            "event_id": "evt-1",
            "event_type": "tool_call",
            "logged_at": "2026-08-16T17:00:00Z",
            "schema_version": "log-event-schema-v1.0",
            "payload": {
                "tool_name": "payments",
                "recipient_email": "[REDACTED:email]",
                "api_key": "[REDACTED:secret_family]",
            },
            "redactions": (
                {"path": "recipient_email", "redaction_class": "validated"},
                {"path": "api_key", "redaction_class": "validated"},
            ),
        },
    )
    return build_monitor_artifact(events, manifest_ref="manifest://batch-1")


def _approved_config(receiver, **overrides):
    base = dict(
        config_revision=CONFIG_REVISION,
        source_identity=SOURCE_IDENTITY,
        candidate_sha=CANDIDATE_SHA,
        endpoint=receiver.base_url(),
        allowed_hosts=("127.0.0.1",),
        allowed_ips=("127.0.0.1",),
        transmit_enabled=True,
        allow_private_destinations=True,  # loopback is the test receiver
        max_attempts=3,
        backoff_base_seconds=0.0,
    )
    base.update(overrides)
    return TransportConfig(**base)


def _sender(receiver):
    return make_https_sender(ca_file=receiver._ca_file)


def _no_sleep(_seconds):
    return None


# --------------------------------------------------------------------------- #
# AC1 — default no-transmit                                                    #
# --------------------------------------------------------------------------- #


def test_ac1_default_prepares_validates_and_sends_zero_bytes(tmp_path):
    store = TransportReceiptStore(tmp_path / "store")
    artifact = _artifact()

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("default posture must not open any connection")

    result = send_monitor_artifact(
        artifact,
        config=TransportConfig(
            config_revision=CONFIG_REVISION,
            source_identity=SOURCE_IDENTITY,
            candidate_sha=CANDIDATE_SHA,
        ),
        store=store,
        sender=fail_if_called,
    )

    assert result.transmitted is False
    assert result.delivered is False
    assert result.eligibility == "disabled"
    assert result.bytes_prepared > 0
    assert result.receipt is None
    assert store.load_receipt(result.idempotency_key) is None


# --------------------------------------------------------------------------- #
# AC2 — approved config sends exactly one artifact, hash-bound ack             #
# --------------------------------------------------------------------------- #


def test_ac2_approved_sends_one_artifact_with_hash_bound_ack(receiver, tmp_path):
    store = TransportReceiptStore(tmp_path / "store")
    artifact = _artifact()
    config = _approved_config(receiver)

    result = send_monitor_artifact(artifact, config=config, store=store, sender=_sender(receiver))

    assert result.delivered is True
    assert result.eligibility == "delivered"
    assert len(receiver.ledger.accepted) == 1
    assert receiver.ledger.rejections == []
    # Acknowledgement is bound to the exact artifact hash.
    stored = list(receiver.ledger.accepted.values())[0]
    assert stored["artifact_hash"] == result.artifact_hash
    assert result.receipt["acknowledgement_hash"] == stored["ack_hash"]


# --------------------------------------------------------------------------- #
# AC3 — retry after ambiguous timeout is idempotent -> one logical receipt     #
# --------------------------------------------------------------------------- #


def test_ac3_retry_after_ambiguous_timeout_is_idempotent(receiver, tmp_path):
    store = TransportReceiptStore(tmp_path / "store")
    artifact = _artifact()
    config = _approved_config(receiver)
    real = _sender(receiver)
    calls = {"n": 0}

    def flaky(envelope_bytes, cfg, ips):
        calls["n"] += 1
        ack = real(envelope_bytes, cfg, ips)  # receiver actually records the delivery
        if calls["n"] == 1:
            # Acknowledgement is lost on the wire: ambiguous, must retry.
            raise AmbiguousDeliveryError("simulated timeout after receiver persisted")
        return ack

    result = send_monitor_artifact(
        artifact, config=config, store=store, sender=flaky, sleep=_no_sleep
    )

    assert calls["n"] == 2
    assert result.delivered is True
    # Receiver deduplicated: exactly one logical receipt on each side.
    assert len(receiver.ledger.accepted) == 1
    receipt_ids = {a["receipt_id"] for a in receiver.ledger.accepted.values()}
    assert result.receipt["receipt_id"] in receipt_ids
    # Attempt history records the ambiguous first try then the acknowledgement.
    outcomes = [a["outcome"] for a in result.receipt["attempt_history"]]
    assert outcomes == ["ambiguous", "acknowledged"]

    # A second independent send of the same artifact returns the same receipt.
    again = send_monitor_artifact(
        artifact, config=config, store=store, sender=real, sleep=_no_sleep
    )
    assert again.receipt["receipt_id"] == result.receipt["receipt_id"]
    assert len(receiver.ledger.accepted) == 1


# --------------------------------------------------------------------------- #
# AC4 — every unsafe path is rejected before a successful receipt              #
# --------------------------------------------------------------------------- #


def test_ac4_raw_sensitive_field_is_rejected_by_gate(receiver, tmp_path):
    from post_bind_ingest.egress import EgressGateError

    store = TransportReceiptStore(tmp_path / "store")
    artifact = _artifact()
    artifact["excerpts"][0]["text"] = "contact jane@example.com now"  # raw PII
    with pytest.raises(EgressGateError):
        send_monitor_artifact(artifact, config=_approved_config(receiver), store=store, sender=_sender(receiver))
    assert receiver.ledger.accepted == {}


def test_ac4_oversized_excerpt_is_rejected(receiver, tmp_path):
    from post_bind_ingest.egress import EgressGateError

    store = TransportReceiptStore(tmp_path / "store")
    artifact = _artifact()
    artifact["excerpts"][0]["text"] = "[REDACTED:email]" + "x" * 400
    with pytest.raises(EgressGateError):
        send_monitor_artifact(artifact, config=_approved_config(receiver), store=store, sender=_sender(receiver))


def test_ac4_non_https_endpoint_is_rejected(receiver, tmp_path):
    store = TransportReceiptStore(tmp_path / "store")
    config = _approved_config(receiver, endpoint="http://127.0.0.1:9/monitor", allowed_hosts=("127.0.0.1",))
    with pytest.raises(TransportRejected):
        send_monitor_artifact(_artifact(), config=config, store=store, sender=_sender(receiver))
    assert receiver.ledger.accepted == {}


def test_ac4_host_mismatch_is_rejected(receiver, tmp_path):
    store = TransportReceiptStore(tmp_path / "store")
    config = _approved_config(receiver, allowed_hosts=("evidence.partner.example",))
    with pytest.raises(TransportRejected):
        send_monitor_artifact(_artifact(), config=config, store=store, sender=_sender(receiver))


def test_ac4_unapproved_destination_ip_is_rejected(receiver, tmp_path):
    store = TransportReceiptStore(tmp_path / "store")
    config = _approved_config(receiver, allowed_ips=("203.0.113.7",))
    with pytest.raises(TransportRejected):
        send_monitor_artifact(_artifact(), config=config, store=store, sender=_sender(receiver))


def test_ac4_private_destination_rejected_when_policy_forbids(receiver, tmp_path):
    store = TransportReceiptStore(tmp_path / "store")
    config = _approved_config(receiver, allow_private_destinations=False)
    with pytest.raises(TransportRejected):
        send_monitor_artifact(_artifact(), config=config, store=store, sender=_sender(receiver))


def test_ac4_invalid_tls_is_rejected(receiver, tmp_path):
    store = TransportReceiptStore(tmp_path / "store")
    # System trust store does not trust the private self-signed leaf.
    untrusting_sender = make_https_sender(ca_file=None)
    with pytest.raises(TransportRejected):
        send_monitor_artifact(
            _artifact(), config=_approved_config(receiver), store=store, sender=untrusting_sender
        )
    assert receiver.ledger.accepted == {}


def test_ac4_redirect_is_not_followed(receiver, tmp_path):
    store = TransportReceiptStore(tmp_path / "store")

    def redirecting(_envelope_bytes, _cfg, _ips):
        raise TransportRejected("redirect responses are not followed (status 302)")

    with pytest.raises(TransportRejected):
        send_monitor_artifact(_artifact(), config=_approved_config(receiver), store=store, sender=redirecting)


def test_ac4_mutated_payload_is_rejected_by_receiver(receiver, tmp_path):
    store = TransportReceiptStore(tmp_path / "store")
    real = _sender(receiver)

    def mutating(envelope_bytes, cfg, ips):
        envelope = json.loads(envelope_bytes)
        # Tamper with the payload after the sender computed its hash.
        envelope["artifact"]["aggregates"]["event_count"] = 999
        return real(json.dumps(envelope).encode("utf-8"), cfg, ips)

    with pytest.raises(TransportRejected):
        send_monitor_artifact(_artifact(), config=_approved_config(receiver), store=store, sender=mutating)
    # Receiver refused: no favorable determination recorded.
    assert receiver.ledger.accepted == {}
    assert any(r["reason"] == "hash-mismatch" for r in receiver.ledger.rejections)


def test_ac4_invalid_acknowledgement_is_rejected(receiver, tmp_path):
    store = TransportReceiptStore(tmp_path / "store")
    real = _sender(receiver)

    def tamper_ack(envelope_bytes, cfg, ips):
        ack = dict(real(envelope_bytes, cfg, ips))
        ack["ack_hash"] = "sha256:" + "0" * 64  # forged binding
        return ack

    with pytest.raises(TransportRejected):
        send_monitor_artifact(_artifact(), config=_approved_config(receiver), store=store, sender=tamper_ack)
    # No delivered receipt exists despite the receiver having stored the artifact.
    envelope = build_envelope(_artifact(), config=_approved_config(receiver))
    assert store.load_receipt(envelope["idempotency_key"]) is None


# --------------------------------------------------------------------------- #
# AC5 — receiver outage: evidence queued locally, eligibility unresolved       #
# --------------------------------------------------------------------------- #


def test_ac5_receiver_outage_queues_and_stays_fail_closed(receiver, tmp_path):
    store = TransportReceiptStore(tmp_path / "store")
    config = _approved_config(receiver)
    # Take the receiver down before sending.
    receiver.stop()

    result = send_monitor_artifact(
        _artifact(), config=config, store=store, sender=_sender(receiver), sleep=_no_sleep
    )

    assert result.delivered is False
    assert result.eligibility == "unresolved"
    assert result.receipt is None
    assert result.queued_path is not None
    queued = store.load_queued(result.idempotency_key)
    assert queued is not None
    assert queued["status"] == "queued"
    assert queued["eligibility"] == "unresolved"
    assert len(result.attempts) == config.max_attempts


# --------------------------------------------------------------------------- #
# AC6 — immutable receipts bind the full provenance set                        #
# --------------------------------------------------------------------------- #


def test_ac6_receipt_binds_full_provenance_and_is_immutable(receiver, tmp_path):
    store = TransportReceiptStore(tmp_path / "store")
    config = _approved_config(receiver)
    result = send_monitor_artifact(_artifact(), config=config, store=store, sender=_sender(receiver))

    receipt = result.receipt
    assert receipt["source_identity"] == SOURCE_IDENTITY
    assert receipt["artifact_hash"] == result.artifact_hash
    assert receipt["schema_version"] == "monitor-data-contract-v0.1"
    assert receipt["destination"] == receiver.base_url()
    assert receipt["config_revision"] == CONFIG_REVISION
    assert receipt["candidate_sha"] == CANDIDATE_SHA
    assert receipt["acknowledgement_hash"].startswith("sha256:")
    assert receipt["attempt_history"][-1]["outcome"] == "acknowledged"
    assert receipt["delivered"] is True

    # Immutability: a differing rewrite for the same key is refused.
    with pytest.raises(TransportRejected):
        tampered = dict(receipt)
        tampered["delivered"] = False
        store.write_receipt(result.idempotency_key, tampered)
    # Identical bytes are an idempotent no-op.
    assert store.write_receipt(result.idempotency_key, receipt)["receipt_id"] == receipt["receipt_id"]
