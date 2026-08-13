"""Trusted behavioral grader receipt boundary (BEAAA-22861/22863)."""
from __future__ import annotations

import base64
import hashlib
import json
import re
import socket
import struct
import uuid
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


class BehavioralReceiptError(ValueError):
    """A receipt failed authentication or immutable evidence resolution."""


MAX_RECEIPT_FRAME = 64 * 1024


def receive_receipt_frame(sock: socket.socket, *, expected_peer_pid: int | None = None) -> dict[str, Any]:
    """Receive exactly one bounded seqpacket from the trusted supervisor."""
    if (sock.type & socket.SOCK_SEQPACKET) != socket.SOCK_SEQPACKET:
        raise BehavioralReceiptError("transport_not_seqpacket")
    if expected_peer_pid is not None and hasattr(socket, "SO_PEERCRED"):
        creds = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
        pid, _uid, _gid = struct.unpack("3i", creds)
        if pid != expected_peer_pid:
            raise BehavioralReceiptError("transport_peer_mismatch")
    frame, _anc, flags, _address = sock.recvmsg(MAX_RECEIPT_FRAME + 1)
    if flags & getattr(socket, "MSG_TRUNC", 0) or len(frame) > MAX_RECEIPT_FRAME:
        raise BehavioralReceiptError("transport_frame_oversized")
    if not frame:
        raise BehavioralReceiptError("transport_frame_empty")
    try:
        value = json.loads(frame)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BehavioralReceiptError("transport_frame_invalid_json") from exc
    if not isinstance(value, dict):
        raise BehavioralReceiptError("transport_frame_invalid_json")
    return value


@dataclass
class ReceiptReplayState:
    """Scan-scoped replay/conflict guard; persist one instance per scan."""
    by_receipt_id: dict[str, bytes]
    by_predicate: dict[tuple[str, str], bytes]

    @classmethod
    def empty(cls) -> "ReceiptReplayState":
        return cls({}, {})

    def admit(self, receipt: Mapping[str, Any]) -> str:
        receipt_id = receipt.get("receipt_id")
        try:
            uuid.UUID(str(receipt_id))
        except (ValueError, AttributeError) as exc:
            raise BehavioralReceiptError("receipt_id_invalid") from exc
        scan_id, predicate = str(receipt.get("scan_id", "")), str(receipt.get("predicate", ""))
        digest = hashlib.sha256(canonicalize_receipt(receipt)).digest()
        prior_id = self.by_receipt_id.get(str(receipt_id))
        if prior_id is not None:
            if prior_id != digest: raise BehavioralReceiptError("receipt_id_conflict")
            return "duplicate"
        prior_predicate = self.by_predicate.get((scan_id, predicate))
        if prior_predicate is not None:
            if prior_predicate != digest: raise BehavioralReceiptError("scan_predicate_conflict")
            return "duplicate"
        self.by_receipt_id[str(receipt_id)] = digest
        self.by_predicate[(scan_id, predicate)] = digest
        return "accepted"


_SHA = re.compile(r"sha256:[0-9a-f]{64}\Z")
_ROLES = ("fixture", "trace", "grader_receipt")


def canonicalize_receipt(receipt: Mapping[str, Any]) -> bytes:
    """Canonical signing preimage for the v1 restricted JCS value domain.

    Receipt v1 admits only JSON strings, integers, lists, objects, booleans and
    null.  Floats are rejected so Python's canonical encoder is byte-equivalent
    to RFC 8785 for every admitted value.
    """
    value = {key: item for key, item in receipt.items() if key != "signature"}

    def check(item: Any) -> None:
        if isinstance(item, float):
            raise BehavioralReceiptError("non-integer JSON number rejected")
        if isinstance(item, dict):
            if not all(isinstance(k, str) for k in item):
                raise BehavioralReceiptError("non-string object key rejected")
            for child in item.values(): check(child)
        elif isinstance(item, list):
            for child in item: check(child)
        elif item is not None and not isinstance(item, (str, int, bool)):
            raise BehavioralReceiptError("non-JSON receipt value rejected")
    check(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


@dataclass(frozen=True)
class EvidenceObject:
    content: bytes
    generation: str
    immutable: bool = True


class EvidenceResolver(Protocol):
    def resolve(self, ref: str) -> EvidenceObject: ...


@dataclass
class InMemoryEvidenceResolver:
    """Test/frozen-fixture resolver; production supplies a capability resolver."""
    objects: dict[str, EvidenceObject]

    def resolve(self, ref: str) -> EvidenceObject:
        try: return self.objects[ref]
        except KeyError as exc: raise BehavioralReceiptError("evidence_unresolved") from exc


@dataclass(frozen=True)
class KeyRecord:
    public_key: Ed25519PublicKey
    grader_id: str
    grader_version: str
    grader_build_digest: str
    status: str
    predicates: frozenset[str]
    fixture_only: bool = False


@dataclass(frozen=True)
class ProductionKeyring:
    """Immutable key capability pinned to the production scanner build."""
    scanner_build_digest: str
    records: Mapping[str, KeyRecord]

    def for_build(self, scanner_build_digest: str) -> Mapping[str, KeyRecord]:
        if scanner_build_digest != self.scanner_build_digest:
            raise BehavioralReceiptError("keyring_build_mismatch")
        return self.records


@dataclass(frozen=True)
class ScanScopedEvidenceResolver:
    """Restrict an immutable resolver capability to one scan namespace."""
    scan_id: str
    delegate: EvidenceResolver

    def resolve(self, ref: str) -> EvidenceObject:
        if not ref.startswith(f"evidence://{self.scan_id}/"):
            raise BehavioralReceiptError("resolver_scope_violation")
        obj = self.delegate.resolve(ref)
        if not obj.immutable or not obj.generation:
            raise BehavioralReceiptError("evidence_mutable")
        return obj


@dataclass(frozen=True)
class VerificationContext:
    scan_id: str
    agent_id: str
    scanner_version: str
    corpus_version: str
    model_versions: tuple[str, ...]
    seed: int
    test_mode: bool = False
    frozen_golden: bool = False


class VerifiedBehavioralReceipt:
    """Opaque capability constructed only after complete verification."""
    __slots__ = ("_payload", "evidence_generations")
    _TOKEN = object()

    def __init__(self, token: object, payload: Mapping[str, Any], generations: tuple[str, ...]):
        if token is not self._TOKEN: raise TypeError("VerifiedBehavioralReceipt is verifier-owned")
        self._payload = dict(payload)
        self.evidence_generations = generations

    @property
    def payload(self) -> Mapping[str, Any]: return self._payload


def _object_json(obj: EvidenceObject, reason: str) -> dict[str, Any]:
    if not obj.immutable or not obj.generation:
        raise BehavioralReceiptError("evidence_mutable")
    if len(obj.content) > 64 * 1024:
        raise BehavioralReceiptError("evidence_oversized")
    try: value = json.loads(obj.content)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc: raise BehavioralReceiptError(reason) from exc
    if not isinstance(value, dict): raise BehavioralReceiptError(reason)
    return value


def verify_behavioral_grader_receipt_v1(receipt: Mapping[str, Any], *, resolver: EvidenceResolver,
                                         keyring: Mapping[str, KeyRecord], context: VerificationContext) -> VerifiedBehavioralReceipt:
    from .behavioral_activation import MAPPINGS
    if receipt.get("receipt_version") != "behavioral_grader_receipt_v1": raise BehavioralReceiptError("receipt_version")
    predicate = receipt.get("predicate")
    if predicate not in MAPPINGS or receipt.get("rubric_point_id") != MAPPINGS[predicate]: raise BehavioralReceiptError("mapping_mismatch")
    for field, expected in (("scan_id", context.scan_id), ("agent_id", context.agent_id), ("scanner_version", context.scanner_version),
                            ("corpus_version", context.corpus_version), ("seed", context.seed)):
        if receipt.get(field) != expected: raise BehavioralReceiptError(f"context_{field}_mismatch")
    if tuple(receipt.get("model_versions") or ()) != context.model_versions: raise BehavioralReceiptError("context_model_versions_mismatch")
    if receipt.get("verdict") not in {"pass", "fail", "error", "not_applicable"}: raise BehavioralReceiptError("verdict")
    if receipt.get("signature_alg") != "ed25519": raise BehavioralReceiptError("signature_algorithm")
    key = keyring.get(str(receipt.get("signing_key_id", "")))
    if key is None or key.status != "active": raise BehavioralReceiptError("signing_key_not_current")
    if key.fixture_only and not (context.test_mode and context.frozen_golden): raise BehavioralReceiptError("fixture_key_forbidden")
    grader = receipt.get("grader") or {}
    if (grader.get("grader_id"), grader.get("grader_version"), grader.get("grader_build_digest")) != (key.grader_id, key.grader_version, key.grader_build_digest):
        raise BehavioralReceiptError("grader_identity_mismatch")
    if predicate not in key.predicates: raise BehavioralReceiptError("predicate_not_authorized")
    try:
        signature = base64.b64decode(receipt.get("signature", ""), validate=True)
        key.public_key.verify(signature, canonicalize_receipt(receipt))
    except (ValueError, InvalidSignature) as exc: raise BehavioralReceiptError("signature_invalid") from exc
    refs = receipt.get("evidence_refs")
    if not isinstance(refs, list) or len(refs) != 3: raise BehavioralReceiptError("evidence_cardinality")
    roles = [row.get("role") for row in refs if isinstance(row, dict)]
    values = [row.get("ref") for row in refs if isinstance(row, dict)]
    if sorted(roles) != sorted(_ROLES) or len(set(values)) != 3: raise BehavioralReceiptError("evidence_duplicate_or_role_mismatch")
    resolved: dict[str, dict[str, Any]] = {}; generations = []
    for row in refs:
        ref, digest = row.get("ref"), row.get("sha256")
        if not isinstance(ref, str) or not ref.startswith(f"evidence://{context.scan_id}/") or not isinstance(digest, str) or not _SHA.fullmatch(digest):
            raise BehavioralReceiptError("evidence_reference_invalid")
        obj = resolver.resolve(ref); generations.append(obj.generation)
        if "sha256:" + hashlib.sha256(obj.content).hexdigest() != digest: raise BehavioralReceiptError("evidence_digest_mismatch")
        resolved[row["role"]] = _object_json(obj, "evidence_json_invalid")
    fixture, trace, output = resolved["fixture"], resolved["trace"], resolved["grader_receipt"]
    f = receipt.get("fixture") or {}; t = receipt.get("trace") or {}
    if (fixture.get("fixture_id"), fixture.get("fixture_version"), refs[roles.index("fixture")]["sha256"]) != (f.get("fixture_id"), f.get("fixture_version"), f.get("fixture_sha256")): raise BehavioralReceiptError("fixture_identity_mismatch")
    if (trace.get("trace_id"), trace.get("trace_schema_version"), trace.get("scan_id"), trace.get("agent_id"), trace.get("seed"), refs[roles.index("trace")]["sha256"]) != (t.get("trace_id"), t.get("trace_schema_version"), context.scan_id, context.agent_id, context.seed, t.get("trace_sha256")): raise BehavioralReceiptError("trace_identity_mismatch")
    expected_output = {"predicate": predicate, "verdict": receipt["verdict"], "grader_id": key.grader_id, "grader_version": key.grader_version, "grader_build_digest": key.grader_build_digest, "scanner_version": context.scanner_version, "corpus_version": context.corpus_version, "model_versions": list(context.model_versions), "seed": context.seed}
    if output != expected_output: raise BehavioralReceiptError("grader_output_identity_mismatch")
    return VerifiedBehavioralReceipt(VerifiedBehavioralReceipt._TOKEN, receipt, tuple(generations))
