import json
import hashlib
import os
import socket
from pathlib import Path

import pytest

from rubric.behavioral_receipt import (
    BehavioralReceiptError,
    EvidenceObject,
    InMemoryEvidenceResolver,
    ReceiptReplayState,
    ScanScopedEvidenceResolver,
    ProductionKeyring,
    receive_receipt_frame,
)
from scanners.behavioral_receipt_ingestion import (
    DEVELOPMENT_ACTIVATION_PROFILE,
    DevelopmentBehavioralActivation,
    ScorerReceiptIngestor,
    create_receipt_channel,
)
from scanners.live_probe_workers import END_OF_SCAN, run_scorer
from scanners.score_runtime import ScoreRuntime
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


def test_development_activation_requires_pinned_profile_build_and_keys():
    _receipt, _resolver, records, _ctx = _signed_case()
    keyring = ProductionKeyring("sha256:" + "a" * 64, records)
    config = DevelopmentBehavioralActivation(
        DEVELOPMENT_ACTIVATION_PROFILE,
        "sha256:" + "a" * 64,
        keyring,
        lambda _scan_id: (),
    )
    assert config.validate() == records
    with pytest.raises(ValueError, match="profile_unpinned"):
        DevelopmentBehavioralActivation(
            "on", "sha256:" + "a" * 64, keyring, lambda _scan_id: ()
        ).validate()
    with pytest.raises(BehavioralReceiptError, match="keyring_build_mismatch"):
        DevelopmentBehavioralActivation(
            DEVELOPMENT_ACTIVATION_PROFILE,
            "sha256:" + "b" * 64,
            keyring,
            lambda _scan_id: (),
        ).validate()


def test_live_scorer_forwards_only_internal_ingestor_capabilities():
    receipt, resolver, keyring, ctx = _signed_case()
    ingestor, sender = create_receipt_channel(
        expected_grader_pid=os.getpid(), resolver=resolver, keyring=keyring, context=ctx
    )
    sender.send(json.dumps(receipt).encode())

    class Channel:
        sent = None
        def recv_row(self, timeout=None): return END_OF_SCAN
        def send_score_envelope(self, value): self.sent = value

    class Runtime:
        seen = None
        def compute_envelope(self, **kwargs):
            self.seen = kwargs["behavioral_receipt_ingestors"]
            assert kwargs["behavioral_activation_enabled"] is True
            self.seen[0].ingest_one()
            return {"ok": True}

    channel, runtime = Channel(), Runtime()
    try:
        run_scorer(
            channel=channel,
            on_validated_row=lambda *_: None,
            score_runtime=runtime,
            score_inputs={},
            behavioral_receipt_ingestors=(ingestor,),
        )
        assert runtime.seen == (ingestor,)
        assert channel.sent == ("ok", {"ok": True})
    finally:
        ingestor._reader.close(); sender.close()


@pytest.mark.parametrize(("predicate", "point"), [
    ("transaction_correctness.unauthorized_call", "ROBUSTNESS.TOOL_MISUSE.UNAUTHORIZED_CALL"),
    ("transaction_correctness.parameter_tampering", "ROBUSTNESS.TOOL_MISUSE.PARAM_TAMPER"),
    ("stop_compliance.post_stop_action", "P4.3"),
    ("stop_compliance.kill_switch_control", "P4.6"),
    ("tool_grounded_factuality.unsupported_claim", "P1.1"),
    ("tool_grounded_factuality.contradictory_claim", "P1.2"),
])
def test_all_six_signed_failures_reach_live_scorer(predicate, point):
    receipt, resolver, keyring, ctx = _signed_case(predicate=predicate, point=point)
    ingestor, sender = create_receipt_channel(
        expected_grader_pid=os.getpid(), resolver=resolver, keyring=keyring, context=ctx
    )
    sender.send(json.dumps(receipt).encode())

    class Channel:
        sent = None
        messages = [{
            "dim_id": point, "raw": 4.0,
            "runner": "beaai-hosted-eval",
            "eval_run_id": "00000000-0000-0000-0000-000000000001",
            "evidence_uri": f"https://evidence.local/{point}",
            "evidence_sha256": "sha256:" + hashlib.sha256(point.encode()).hexdigest(),
            "replicate_index": 0,
        }, END_OF_SCAN]
        def recv_row(self, timeout=None): return self.messages.pop(0)
        def send_score_envelope(self, value): self.sent = value

    channel = Channel()
    try:
        run_scorer(
            channel=channel,
            on_validated_row=lambda *_: None,
            score_runtime=ScoreRuntime.from_rubric_path(Path("rubric/rubric.yaml")),
            score_inputs={
                "scan_id": "scan-1", "agent_id": "agent-1", "seed": 0,
                "scanner_version": "0.5.0", "corpus_version": "0.1.0",
                "run_timestamp": "2026-08-13T00:00:00Z",
                "invocation_context": {"purpose": "pre_bind"}, "prior_scan_refs": [],
            },
            behavioral_receipt_ingestors=(ingestor,),
        )
        assert channel.sent[0] == "ok"
        ref = next(row for row in channel.sent[1]["evidence_refs"] if row["rubric_point_id"] == point)
        assert ref["weight"] == 0.0
        assert ref["artifact_uri"] == "evidence://scan-1/grader_receipt"
    finally:
        ingestor._reader.close(); sender.close()
