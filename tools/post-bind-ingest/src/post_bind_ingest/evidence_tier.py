"""Server-owned evidence-tier assignment for BEAAA-20234.

The module implements ``evidence-tier-assignment/1.0`` without accepting a
client-declared tier or changing an in-force multiplier.  It carries only
source classes, pins, determinations, counts, and hashes.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence
import uuid


POLICY_VERSION = "evidence-tier-assignment/1.0"
TIERS = ("T0", "T1", "T2", "T3", "T4")
SOURCE_TIERS = {
    "unverified_or_missing": "T0",
    "in_code_otel": "T1",
    "gateway_derived": "T2",
    "platform_native": "T2",
    "endpoint_adr": "T3",
    "kernel_runtime": "T2",
    "semantic_plus_kernel": "T4",
}
RESERVED_CLIENT_FIELDS = frozenset(
    {"tier", "evidence_tier", "source_class", "basis_codes", "policy_version", "override_id", "exception_id"}
)
REQUIRED_PINS = frozenset(
    {"driver_version", "collector_version", "mapping_version", "evidence_profile_version", "signing_identity_ref"}
)


class EvidenceTierError(ValueError):
    """Stable evidence-tier validation failure."""


@dataclass(frozen=True)
class VerifiedSource:
    tenant_id: str
    source_instance_id: str
    source_class: str
    supported_surfaces: tuple[str, ...]
    source_pins: Mapping[str, str]
    identity_verified: bool
    signature_valid: bool
    hash_chain_valid: bool
    redaction_fail_closed: bool
    replay_idempotency_valid: bool
    continuity_verified: bool
    silence_distinguished_from_zero: bool
    coverage_bounded: bool
    class_specific_verified: bool
    verification_snapshot_hash: str
    verification_window_start: str
    verification_window_end: str
    stale: bool = False
    independent_corroboration_valid: bool = True
    registered_agent_id: str | None = None


@dataclass(frozen=True)
class SurfaceAssignment:
    surface: str
    tier: str
    status: str
    basis_codes: tuple[str, ...]
    source_class: str
    source_instance_id: str | None
    supported_surfaces: tuple[str, ...]
    source_pins: Mapping[str, str]
    verification_snapshot_hash: str | None
    verification_window_start: str | None
    verification_window_end: str | None
    limitations: tuple[str, ...]


@dataclass(frozen=True)
class AssignmentResult:
    event_id: str
    tenant_id: str
    agent_id: str
    term_id: str
    policy_version: str
    assigned_at: str
    agent_tier: str
    status: str
    surface_assignments: tuple[SurfaceAssignment, ...]
    basis_codes: tuple[str, ...]
    event_type: str
    before_tier: str | None
    after_tier: str
    scope: str
    actor: str
    reason: str
    approval_ref: str
    mapping_version: str
    exception_id: str | None = None
    override_id: str | None = None

    def audit_payload(self) -> dict[str, Any]:
        return asdict(self)


def reject_reserved_client_fields(payload: Mapping[str, Any]) -> None:
    """Reject, rather than ignore, spoofed server-assignment fields."""

    supplied = sorted(RESERVED_CLIENT_FIELDS.intersection(payload))
    if supplied:
        raise EvidenceTierError(f"client_evidence_tier_forbidden:{','.join(supplied)}")


def _iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _tier_index(tier: str) -> int:
    return TIERS.index(tier)


def _source_assignment(source: VerifiedSource, surface: str, *, expected_pins: Mapping[str, str], as_of: str) -> SurfaceAssignment:
    source_class = source.source_class if source.source_class in SOURCE_TIERS else "unverified_or_missing"
    target = SOURCE_TIERS[source_class]
    basis: list[str] = [f"source_class:{source_class}"]
    failures: list[str] = []
    base_controls = {
        "identity_failed": source.identity_verified,
        "signature_failed": source.signature_valid,
        "tamper_failed": source.hash_chain_valid,
        "redaction_fail_open": source.redaction_fail_closed,
        "replay_idempotency_failed": source.replay_idempotency_valid,
        "continuity_failed": source.continuity_verified,
        "silence_zero_ambiguous": source.silence_distinguished_from_zero,
        "coverage_unbounded": source.coverage_bounded,
        "class_verification_failed": source.class_specific_verified,
        "stale_source_pin": not source.stale,
    }
    failures.extend(code for code, passed in base_controls.items() if not passed)
    missing_pins = sorted(key for key in REQUIRED_PINS if not str(source.source_pins.get(key, "")).strip())
    if source_class in {"endpoint_adr", "kernel_runtime", "semantic_plus_kernel"}:
        if not ({"source_build", "source_commit", "binary_hash"} & set(source.source_pins)):
            missing_pins.append("source_build_or_commit_or_binary_hash")
    if missing_pins:
        failures.append("source_pins_missing:" + ",".join(missing_pins))
    for key, expected in sorted(expected_pins.items()):
        actual = source.source_pins.get(key)
        if not expected or actual != expected:
            failures.append(f"source_pin_mismatch:{key}")
    if not source.verification_snapshot_hash.strip():
        failures.append("verification_snapshot_missing")
    try:
        window_start = _iso(source.verification_window_start)
        window_end = _iso(source.verification_window_end)
        observed_at = _iso(as_of)
        if window_start > window_end:
            failures.append("verification_window_invalid")
        elif observed_at < window_start:
            failures.append("verification_window_future")
        elif observed_at > window_end:
            failures.append("verification_window_stale")
    except (TypeError, ValueError):
        failures.append("verification_window_invalid")

    if failures:
        tier = "T0"
        status = "downgraded"
        basis.extend(failures)
    elif target == "T4" and not source.independent_corroboration_valid:
        tier = "T3"
        status = "downgraded"
        basis.append("independent_corroboration_failed")
    else:
        tier = target
        status = "assigned"
        basis.append("minimum_controls_passed")
    limitations = {
        "in_code_otel": ("vendor_controlled", "source_completeness_unproven"),
        "gateway_derived": ("gateway_bypass_not_covered", "local_actions_not_covered"),
        "platform_native": ("platform_supported_surface_only",),
        "endpoint_adr": ("whole_host_truth_unproven", "unsupported_segments_not_covered"),
        "kernel_runtime": ("genai_semantics_require_correlation",),
        "semantic_plus_kernel": ("off_host_activity_not_covered",),
        "unverified_or_missing": ("operation_unverified",),
    }[source_class]
    return SurfaceAssignment(
        surface=surface,
        tier=tier,
        status=status,
        basis_codes=tuple(basis),
        source_class=source_class,
        source_instance_id=source.source_instance_id,
        supported_surfaces=source.supported_surfaces,
        source_pins=dict(source.source_pins),
        verification_snapshot_hash=source.verification_snapshot_hash,
        verification_window_start=source.verification_window_start,
        verification_window_end=source.verification_window_end,
        limitations=limitations,
    )


def assign_evidence_tier(
    *, tenant_id: str, agent_id: str, term_id: str, required_surfaces: Sequence[str],
    verified_sources: Sequence[VerifiedSource], as_of: str, exception_id: str | None = None,
    exception_expires_at: str | None = None, audit_actor: str = "post-bind-ingest",
    expected_pins: Mapping[str, str] | None = None, before_tier: str | None = None,
    event_type: str = "assignment", scope: str = "agent", reason: str = "receiver_ingest",
    approval_ref: str = "policy:evidence-tier-assignment/1.0",
) -> AssignmentResult:
    """Derive per-surface and agent tiers from tenant-bound verified sources."""

    if not required_surfaces:
        raise EvidenceTierError("required_surfaces_empty")
    if not expected_pins or not all(str(expected_pins.get(key, "")).strip() for key in REQUIRED_PINS):
        raise EvidenceTierError("tenant_pin_registration_missing")
    foreign = [source.source_instance_id for source in verified_sources if source.tenant_id != tenant_id]
    if foreign:
        raise EvidenceTierError("cross_tenant_source_forbidden")
    foreign_agents = [
        source.source_instance_id for source in verified_sources
        if source.registered_agent_id is not None and source.registered_agent_id != agent_id
    ]
    if foreign_agents:
        raise EvidenceTierError("cross_agent_source_forbidden")
    assignments: list[SurfaceAssignment] = []
    for surface in sorted(set(required_surfaces)):
        candidates = [_source_assignment(source, surface, expected_pins=expected_pins or {}, as_of=as_of)
                      for source in verified_sources if surface in source.supported_surfaces]
        if candidates:
            assignments.append(max(candidates, key=lambda item: _tier_index(item.tier)))
        else:
            assignments.append(SurfaceAssignment(
                surface=surface, tier="T0", status="pending_verification",
                basis_codes=("required_surface_missing",), source_class="unverified_or_missing",
                source_instance_id=None, supported_surfaces=(), source_pins={}, verification_snapshot_hash=None,
                verification_window_start=None, verification_window_end=None,
                limitations=("operation_unverified",),
            ))
    agent_tier = min((row.tier for row in assignments), key=_tier_index)
    status = "downgraded" if any(row.status == "downgraded" for row in assignments) else "assigned"
    active_exception = bool(exception_id and exception_expires_at and _iso(exception_expires_at) > _iso(as_of))
    if active_exception:
        status = "exception_active"
    basis = tuple(sorted({code for row in assignments for code in row.basis_codes} | {f"audit_actor:{audit_actor}"}))
    return AssignmentResult(
        event_id=str(uuid.uuid4()), tenant_id=tenant_id, agent_id=agent_id, term_id=term_id,
        policy_version=POLICY_VERSION, assigned_at=as_of, agent_tier=agent_tier, status=status,
        surface_assignments=tuple(assignments), basis_codes=basis,
        event_type=event_type, before_tier=before_tier, after_tier=agent_tier,
        scope=scope, actor=audit_actor, reason=reason, approval_ref=approval_ref,
        mapping_version=expected_pins["mapping_version"],
        exception_id=exception_id if active_exception else None,
    )


def hub_view(result: AssignmentResult, *, requesting_tenant_id: str) -> dict[str, Any]:
    if requesting_tenant_id != result.tenant_id:
        raise EvidenceTierError("cross_tenant_assignment_forbidden")
    payload = result.audit_payload()
    payload.pop("tenant_id")
    rows = result.surface_assignments
    payload["continuity_state"] = "verified" if rows and all(r.status == "assigned" for r in rows) else "partial_or_missing"
    payload["freshness_state"] = "current" if rows and all(
        "verification_window_stale" not in r.basis_codes and "verification_window_future" not in r.basis_codes
        and "verification_window_invalid" not in r.basis_codes for r in rows
    ) else "invalid_or_stale"
    return payload


def rate_multiplier_interface(
    result: AssignmentResult, *, term_id: str, bound_current_term_multiplier: float, as_of: str,
) -> dict[str, Any]:
    if term_id != result.term_id:
        raise EvidenceTierError("term_id_mismatch")
    return {
        "term_id": term_id,
        "as_of": as_of,
        "policy_version": result.policy_version,
        "agent_tier": result.agent_tier,
        "surface_assignments": [asdict(row) for row in result.surface_assignments],
        "verification_snapshot_hashes": sorted(
            {row.verification_snapshot_hash for row in result.surface_assignments if row.verification_snapshot_hash}
        ),
        "current_term_multiplier": bound_current_term_multiplier,
        "next_policy_candidate": {"status": "shadow_only", "agent_tier": result.agent_tier},
    }
