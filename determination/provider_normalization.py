"""BEAAA-16490 cross-provider Passive Monitor normalization.

Makes provider-native runtime telemetry an explicit, provenance-preserving
*input class* to the existing Determination Engine (BEAAA-11372) without adding a
parallel monitor and without ever sitting inline in a vendor request path. This
module is an observability-plane consumer of already-collected, signed evidence:
it maps a provider-native envelope into the immutable ``EvidencePacket`` /
``StructuredEvent`` model the deterministic engine already reads, while carrying
every provenance field through normalization intact.

Governing constraints (BEAAA-16490 / parent BEAAA-16489, Track E):

* Deterministic BackBond rules decide. Any provider-native determination or
  attestation is an INPUT ONLY — recorded as provenance, never consulted by the
  deciding path. This module deliberately does not expose the provider verdict to
  the rule DSL: it is stored under ``provenance.provider_native_determination``
  and is not copied into the event ``attributes`` the engine evaluates.
* Provenance preserved through normalization: source provider identity, the
  immutable source event ids, attestation/feed type, received + event timestamps,
  integrity/authentication status, and the independence-check result.
* No customer weights or proprietary prompts are retained, and PII is redacted
  before retention (fail-closed: disallowed keys are dropped, not passed through).
* No BackBond Score math, rubric weights, judge models, band cuts, or
  ``are-scanner-api-v1`` response semantics change here. This is additive
  evidence-plane plumbing; rollback is reverting the isolated new files.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from determination.records import EvidencePacket
from determination.rule_dsl import canonical_json_bytes

NORMALIZER_VERSION = "provider-normalizer@1.0.0"

# Integrity/authentication status vocabulary. Only ``authenticated`` evidence is
# eligible to corroborate a cross-provider determination; anything else is a
# degraded/fail-closed input.
INTEGRITY_AUTHENTICATED = "authenticated"
INTEGRITY_UNVERIFIED = "unverified"
INTEGRITY_FAILED = "failed"
_INTEGRITY_STATES = frozenset(
    {INTEGRITY_AUTHENTICATED, INTEGRITY_UNVERIFIED, INTEGRITY_FAILED}
)

# Keys that must never be retained: customer weights, proprietary prompts, and
# raw high-sensitivity payloads. Presence anywhere in the provider payload is a
# redaction event (the value is dropped and the key is recorded as redacted).
_DISALLOWED_KEYS = frozenset(
    {
        "customer_weights",
        "model_weights",
        "proprietary_prompt",
        "system_prompt",
        "prompt_template",
        "raw_prompt",
        "raw_transcript",
        "transcript",
        "api_key",
        "api_secret",
        "credential",
        "credentials",
        "secret",
        "authorization",
        "access_token",
        "refresh_token",
    }
)

# Coarse PII value detectors applied to any retained string leaf. Matches are
# masked; the key is retained so structure/counts survive but the value does not.
_PII_PATTERNS = (
    ("email", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("pan", re.compile(r"\b(?:\d[ -]?){13,16}\b")),
    ("phone", re.compile(r"\b\+?\d[\d\s().-]{7,}\d\b")),
)
_REDACTED = "[REDACTED]"


class ProviderNormalizationError(ValueError):
    """A provider envelope is malformed or violates a fail-closed invariant."""


def _redact_value(value: Any, redactions: list[str], key_path: str) -> Any:
    """Recursively drop disallowed keys and mask PII leaves before retention."""
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for k, v in value.items():
            child_path = f"{key_path}.{k}" if key_path else str(k)
            if str(k).lower() in _DISALLOWED_KEYS:
                redactions.append(child_path)
                continue
            out[str(k)] = _redact_value(v, redactions, child_path)
        return out
    if isinstance(value, (list, tuple)):
        return [
            _redact_value(item, redactions, f"{key_path}[{i}]")
            for i, item in enumerate(value)
        ]
    if isinstance(value, str):
        masked = value
        for label, pattern in _PII_PATTERNS:
            if pattern.search(masked):
                masked = pattern.sub(f"{_REDACTED}:{label}", masked)
        if masked != value and key_path:
            redactions.append(key_path)
        return masked
    return value


def _parse_ts(value: Any, *, field_name: str) -> datetime:
    """Parse an ISO-8601 timestamp (``Z`` or offset) to an aware UTC datetime."""
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str) and value:
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ProviderNormalizationError(
                f"malformed {field_name}: {value!r}"
            ) from exc
    else:
        raise ProviderNormalizationError(f"missing {field_name}")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@dataclass(frozen=True)
class ProviderProvenance:
    """Immutable provenance for one provider-native evidence source.

    Every field survives normalization and is embedded, unmodified, into the
    continuity decision so the cross-provider bundle stays machine-verifiable and
    attributable back to each source.
    """

    source_provider_id: str
    provider_stack: str
    attestation_type: str
    attestation_key_id: str
    authentication_method: str
    integrity_status: str
    received_timestamp: str
    source_event_ids: tuple[str, ...]
    provider_native_determination: Any = None
    independence_group: str | None = None
    redactions: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_provider_id": self.source_provider_id,
            "provider_stack": self.provider_stack,
            "attestation_type": self.attestation_type,
            "attestation_key_id": self.attestation_key_id,
            "authentication_method": self.authentication_method,
            "integrity_status": self.integrity_status,
            "received_timestamp": self.received_timestamp,
            "source_event_ids": list(self.source_event_ids),
            # INPUT ONLY: recorded for reviewer context and never consulted by the
            # deterministic deciding path (see module docstring).
            "provider_native_determination": self.provider_native_determination,
            "independence_group": self.independence_group,
            "redactions": list(self.redactions),
        }

    def authenticated(self) -> bool:
        return self.integrity_status == INTEGRITY_AUTHENTICATED


@dataclass(frozen=True)
class NormalizedProviderInput:
    """One provider source normalized into the engine's evidence model."""

    provenance: ProviderProvenance
    packet: EvidencePacket
    event_timestamp: str
    normalizer_version: str = NORMALIZER_VERSION

    def provenance_digest(self) -> str:
        """Stable hash binding this source's provenance to its evidence lineage."""
        preimage = {
            "normalizer_version": self.normalizer_version,
            "provenance": self.provenance.to_dict(),
            "packet_ref": self.packet.packet_ref(),
            "event_timestamp": self.event_timestamp,
        }
        return hashlib.sha256(canonical_json_bytes(preimage)).hexdigest()


@dataclass(frozen=True)
class ProviderEvidenceEnvelope:
    """Raw provider-native telemetry as received, before normalization.

    ``packet`` is an Evidence Profile v1 packet dict (the same shape the engine
    already reads); it carries immutable source event ids, event timestamps, and
    content hashes. Everything else is transport/attestation provenance.
    """

    source_provider_id: str
    provider_stack: str
    attestation_type: str
    attestation_key_id: str
    authentication_method: str
    integrity_status: str
    received_timestamp: str
    packet: Mapping[str, Any]
    provider_native_determination: Any = None
    independence_group: str | None = None
    raw_provider_metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ProviderEvidenceEnvelope":
        try:
            return cls(
                source_provider_id=str(data["source_provider_id"]),
                provider_stack=str(data["provider_stack"]),
                attestation_type=str(data["attestation_type"]),
                attestation_key_id=str(data["attestation_key_id"]),
                authentication_method=str(data["authentication_method"]),
                integrity_status=str(data["integrity_status"]),
                received_timestamp=str(data["received_timestamp"]),
                packet=dict(data["packet"]),
                provider_native_determination=data.get(
                    "provider_native_determination"
                ),
                independence_group=data.get("independence_group"),
                raw_provider_metadata=dict(data.get("raw_provider_metadata", {}) or {}),
            )
        except KeyError as exc:
            raise ProviderNormalizationError(
                f"provider envelope missing required field {exc}"
            ) from exc


def normalize_provider_evidence(
    envelope: ProviderEvidenceEnvelope,
) -> NormalizedProviderInput:
    """Normalize one provider envelope into a provenance-preserving input.

    Fail-closed behavior:

    * unknown ``integrity_status`` -> error (never silently trusted);
    * malformed packet / timestamps -> error;
    * disallowed keys (customer weights, proprietary prompts, raw transcripts) and
      PII leaves are redacted out of the retained provenance metadata.

    The provider-native determination is copied into provenance UNTOUCHED for
    audit, but is never merged into the event attributes the rule engine reads.
    """
    if envelope.integrity_status not in _INTEGRITY_STATES:
        raise ProviderNormalizationError(
            f"unknown integrity_status {envelope.integrity_status!r} "
            f"for provider {envelope.source_provider_id!r}"
        )

    # EvidencePacket is the existing immutable model; it already carries only
    # minimized/hashed content per Evidence Profile v1. We do not mutate it.
    try:
        packet = EvidencePacket.from_dict(envelope.packet)
    except (KeyError, TypeError) as exc:
        raise ProviderNormalizationError(
            f"malformed evidence packet for provider "
            f"{envelope.source_provider_id!r}: {exc}"
        ) from exc

    if not packet.events:
        raise ProviderNormalizationError(
            f"provider {envelope.source_provider_id!r} packet has no events"
        )

    received = _parse_ts(
        envelope.received_timestamp, field_name="received_timestamp"
    )
    # Event time of the source batch = latest event timestamp in the packet.
    event_dt = max(
        _parse_ts(e.event_timestamp, field_name="event_timestamp")
        for e in packet.events
    )

    redactions: list[str] = []
    # Redact only the free-form provider metadata bag before retention; the
    # Evidence Profile packet is already minimized upstream and stays immutable.
    _redact_value(dict(envelope.raw_provider_metadata), redactions, "raw_provider_metadata")

    source_event_ids = tuple(e.evidence_event_id for e in packet.events)

    provenance = ProviderProvenance(
        source_provider_id=envelope.source_provider_id,
        provider_stack=envelope.provider_stack,
        attestation_type=envelope.attestation_type,
        attestation_key_id=envelope.attestation_key_id,
        authentication_method=envelope.authentication_method,
        integrity_status=envelope.integrity_status,
        received_timestamp=received.isoformat().replace("+00:00", "Z"),
        source_event_ids=source_event_ids,
        provider_native_determination=envelope.provider_native_determination,
        independence_group=envelope.independence_group,
        redactions=tuple(sorted(set(redactions))),
    )
    return NormalizedProviderInput(
        provenance=provenance,
        packet=packet,
        event_timestamp=event_dt.isoformat().replace("+00:00", "Z"),
    )


@dataclass(frozen=True)
class IndependenceResult:
    """Result of checking that a set of sources are mutually independent."""

    independent: bool
    reason: str
    distinct_providers: tuple[str, ...]
    distinct_stacks: tuple[str, ...]
    distinct_attestation_keys: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "independent": self.independent,
            "reason": self.reason,
            "distinct_providers": list(self.distinct_providers),
            "distinct_stacks": list(self.distinct_stacks),
            "distinct_attestation_keys": list(self.distinct_attestation_keys),
        }


def check_independence(
    sources: Sequence[NormalizedProviderInput],
) -> IndependenceResult:
    """Two+ sources are independent iff they share no provider id, provider
    stack, attestation signing key, or operator-declared independence group.

    Independence is a precondition for corroboration: if the same stack/key
    signs both feeds, agreement proves nothing. Fails closed (not independent)
    when the shared-lineage evidence is present.
    """
    providers = tuple(sorted({s.provenance.source_provider_id for s in sources}))
    stacks = tuple(sorted({s.provenance.provider_stack for s in sources}))
    keys = tuple(sorted({s.provenance.attestation_key_id for s in sources}))
    groups = [
        s.provenance.independence_group
        for s in sources
        if s.provenance.independence_group is not None
    ]

    if len(sources) < 2:
        return IndependenceResult(
            independent=False,
            reason="fewer_than_two_sources",
            distinct_providers=providers,
            distinct_stacks=stacks,
            distinct_attestation_keys=keys,
        )
    if len(providers) < len(sources):
        reason = "shared_source_provider_id"
    elif len(stacks) < len(sources):
        reason = "shared_provider_stack"
    elif len(keys) < len(sources):
        reason = "shared_attestation_key"
    elif len(groups) == len(sources) and len(set(groups)) < len(sources):
        reason = "shared_independence_group"
    else:
        return IndependenceResult(
            independent=True,
            reason="distinct_provider_stack_and_signing_lineage",
            distinct_providers=providers,
            distinct_stacks=stacks,
            distinct_attestation_keys=keys,
        )
    return IndependenceResult(
        independent=False,
        reason=reason,
        distinct_providers=providers,
        distinct_stacks=stacks,
        distinct_attestation_keys=keys,
    )


__all__ = [
    "INTEGRITY_AUTHENTICATED",
    "INTEGRITY_FAILED",
    "INTEGRITY_UNVERIFIED",
    "IndependenceResult",
    "NORMALIZER_VERSION",
    "NormalizedProviderInput",
    "ProviderEvidenceEnvelope",
    "ProviderNormalizationError",
    "ProviderProvenance",
    "check_independence",
    "normalize_provider_evidence",
]
