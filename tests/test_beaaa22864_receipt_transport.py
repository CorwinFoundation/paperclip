import json
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


def test_seqpacket_preserves_one_way_receipt_frame():
    reader, writer = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    try:
        writer.send(json.dumps({"receipt_id": "r1"}).encode())
        assert receive_receipt_frame(reader) == {"receipt_id": "r1"}
    finally:
        reader.close(); writer.close()


def test_stream_transport_rejected():
    reader, writer = socket.socketpair()
    try:
        with pytest.raises(BehavioralReceiptError, match="transport_not_seqpacket"):
            receive_receipt_frame(reader)
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
