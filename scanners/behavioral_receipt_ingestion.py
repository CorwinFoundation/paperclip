"""Scorer-owned production ingestion path for behavioral grader receipts."""
from __future__ import annotations

import socket
from dataclasses import dataclass, field
from typing import Callable, Mapping

from rubric.behavioral_receipt import (
    EvidenceResolver,
    KeyRecord,
    ProductionKeyring,
    ReceiptReplayState,
    VerificationContext,
    VerifiedBehavioralReceipt,
    receive_receipt_frame,
    verify_behavioral_grader_receipt_v1,
)

DEVELOPMENT_ACTIVATION_PROFILE = "development-behavioral-v1"


@dataclass(frozen=True)
class TrustedReceiptChannel:
    """Supervisor-owned receive endpoint; never populated from scan input."""

    reader: socket.socket
    expected_grader_pid: int
    resolver: EvidenceResolver


@dataclass(frozen=True)
class DevelopmentBehavioralActivation:
    """Pinned internal configuration for Development Mode activation."""

    profile: str
    scanner_build_digest: str
    keyring: "ProductionKeyring"
    channels_for_scan: Callable[[str], tuple[TrustedReceiptChannel, ...]]

    def validate(self) -> Mapping[str, KeyRecord]:
        if self.profile != DEVELOPMENT_ACTIVATION_PROFILE:
            raise ValueError("behavioral_activation_profile_unpinned")
        records = self.keyring.for_build(self.scanner_build_digest)
        if not records:
            raise ValueError("behavioral_activation_keyring_empty")
        return records


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
