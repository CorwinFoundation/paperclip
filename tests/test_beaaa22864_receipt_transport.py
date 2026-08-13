import json
import os
import socket

import pytest

from rubric.behavioral_receipt import (
    BehavioralReceiptError,
    EvidenceObject,
    InMemoryEvidenceResolver,
    ReceiptReplayState,
    ScanScopedEvidenceResolver,
    receive_receipt_frame,
)
from scanners.behavioral_receipt_ingestion import ScorerReceiptIngestor, create_receipt_channel
from tests.test_beaaa22861_behavioral_receipt import _signed_case


def test_seqpacket_preserves_one_way_receipt_frame():
    reader, writer = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    try:
        writer.send(json.dumps({"receipt_id": "r1"}).encode())
        assert receive_receipt_frame(reader, expected_peer_pid=os.getpid()) == {"receipt_id": "r1"}
    finally:
        reader.close(); writer.close()


def test_stream_transport_rejected():
    reader, writer = socket.socketpair()
    try:
        with pytest.raises(BehavioralReceiptError, match="transport_not_seqpacket"):
            receive_receipt_frame(reader, expected_peer_pid=os.getpid())
    finally:
        reader.close(); writer.close()


def test_scan_scoped_resolver_cannot_cross_scan_boundary():
    delegate = InMemoryEvidenceResolver({"evidence://scan-2/x": EvidenceObject(b"{}", "g1")})
    resolver = ScanScopedEvidenceResolver("scan-1", delegate)
    with pytest.raises(BehavioralReceiptError, match="resolver_scope_violation"):
        resolver.resolve("evidence://scan-2/x")


def test_replay_is_idempotent_but_conflicting_predicate_is_rejected():
    state = ReceiptReplayState.empty()
    receipt = {"receipt_id": "35b86df2-d5b2-48d4-b5df-94ec15835414", "scan_id": "s", "predicate": "p", "verdict": "pass"}
    assert state.admit(receipt) == "accepted"
    assert state.admit(receipt) == "duplicate"
    conflict = {**receipt, "receipt_id": "a1b86df2-d5b2-48d4-b5df-94ec15835414", "verdict": "fail"}
    with pytest.raises(BehavioralReceiptError, match="scan_predicate_conflict"):
        state.admit(conflict)


def test_production_factory_separates_scorer_and_sender_capabilities():
    receipt, resolver, keyring, ctx = _signed_case()
    ingestor, sender = create_receipt_channel(
        expected_grader_pid=os.getpid(), resolver=resolver, keyring=keyring, context=ctx
    )
    try:
        assert isinstance(ingestor, ScorerReceiptIngestor)
        assert not hasattr(ingestor, "send")
        assert sender.fileno() != ingestor._reader.fileno()
    finally:
        ingestor._reader.close(); sender.close()


def test_failed_verification_cannot_poison_replay_state():
    receipt, resolver, keyring, ctx = _signed_case()
    receipt["verdict"] = "pass"  # invalidates the signature
    ingestor, sender = create_receipt_channel(
        expected_grader_pid=os.getpid(), resolver=resolver, keyring=keyring, context=ctx
    )
    try:
        sender.send(json.dumps(receipt).encode())
        with pytest.raises(BehavioralReceiptError, match="signature_invalid"):
            ingestor.ingest_one()
        assert ingestor._replay.by_receipt_id == {}
        assert ingestor._replay.by_predicate == {}
    finally:
        ingestor._reader.close(); sender.close()


def test_verified_duplicate_is_idempotent_through_production_ingestor():
    receipt, resolver, keyring, ctx = _signed_case()
    ingestor, sender = create_receipt_channel(
        expected_grader_pid=os.getpid(), resolver=resolver, keyring=keyring, context=ctx
    )
    try:
        frame = json.dumps(receipt).encode()
        sender.send(frame)
        assert ingestor.ingest_one()[1] == "accepted"
        sender.send(frame)
        assert ingestor.ingest_one()[1] == "duplicate"
        assert len(ingestor._replay.by_receipt_id) == 1
    finally:
        ingestor._reader.close(); sender.close()


def test_peer_identity_is_mandatory_and_mismatch_fails_closed():
    receipt, resolver, keyring, ctx = _signed_case()
    ingestor, sender = create_receipt_channel(
        expected_grader_pid=os.getpid() + 100000, resolver=resolver, keyring=keyring, context=ctx
    )
    try:
        sender.send(json.dumps(receipt).encode())
        with pytest.raises(BehavioralReceiptError, match="transport_peer_mismatch"):
            ingestor.ingest_one()
    finally:
        ingestor._reader.close(); sender.close()
