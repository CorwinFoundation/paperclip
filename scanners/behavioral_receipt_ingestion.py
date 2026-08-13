"""Scorer-owned production ingestion path for behavioral grader receipts."""
from __future__ import annotations

import socket
from dataclasses import dataclass, field
from typing import Mapping

from rubric.behavioral_receipt import (
    EvidenceResolver,
    KeyRecord,
    ReceiptReplayState,
    VerificationContext,
    VerifiedBehavioralReceipt,
    receive_receipt_frame,
    verify_behavioral_grader_receipt_v1,
)


@dataclass
class ScorerReceiptIngestor:
    """Owns the receive capability and enforces verify-then-commit ordering."""

    _reader: socket.socket
    _expected_grader_pid: int
    _resolver: EvidenceResolver
    _keyring: Mapping[str, KeyRecord]
    _context: VerificationContext
    _replay: ReceiptReplayState = field(default_factory=ReceiptReplayState.empty)

    def ingest_one(self) -> tuple[VerifiedBehavioralReceipt, str]:
        receipt = receive_receipt_frame(
            self._reader, expected_peer_pid=self._expected_grader_pid
        )
        # Replay state is committed only after signature, context, and all
        # immutable evidence identities have verified successfully.
        verified = verify_behavioral_grader_receipt_v1(
            receipt,
            resolver=self._resolver,
            keyring=self._keyring,
            context=self._context,
        )
        disposition = self._replay.admit(verified.payload)
        return verified, disposition


def create_receipt_channel(
    *,
    expected_grader_pid: int,
    resolver: EvidenceResolver,
    keyring: Mapping[str, KeyRecord],
    context: VerificationContext,
) -> tuple[ScorerReceiptIngestor, socket.socket]:
    """Supervisor factory: Scorer gets ingestor; grader gets sender endpoint."""
    reader, writer = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    return (
        ScorerReceiptIngestor(reader, expected_grader_pid, resolver, keyring, context),
        writer,
    )
