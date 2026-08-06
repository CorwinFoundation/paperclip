"""Production receiver-to-Log-Tracker evidence-tier boundary."""
from __future__ import annotations

from dataclasses import asdict
from typing import Any, Mapping, Sequence

from .evidence_tier import VerifiedSource, assign_evidence_tier, hub_view, rate_multiplier_interface, reject_reserved_client_fields
from .normalizer import normalize_receiver_events


def ingest_evidence_tier(*, receiver_payload: Mapping[str, Any], jwt_keyring: Mapping[str, Any], conn: Any,
                         tenant_id: str, agent_id: str, term_id: str, required_surfaces: Sequence[str],
                         verified_sources: Sequence[VerifiedSource], registered_pins: Mapping[str, str],
                         as_of: str, append_assignment: Any, bound_current_term_multiplier: float) -> dict[str, Any]:
    """Validate a real receiver batch, persist its tier event, and return tenant-safe boundaries."""
    reject_reserved_client_fields(receiver_payload)
    for event in receiver_payload.get("events", ()):
        if isinstance(event, Mapping):
            reject_reserved_client_fields(event)
            payload = event.get("payload")
            if isinstance(payload, Mapping):
                reject_reserved_client_fields(payload)
    normalized = normalize_receiver_events(dict(receiver_payload), jwt_keyring=dict(jwt_keyring))
    result = assign_evidence_tier(
        tenant_id=tenant_id, agent_id=agent_id, term_id=term_id, required_surfaces=required_surfaces,
        verified_sources=verified_sources, expected_pins=registered_pins, as_of=as_of,
    )
    append_assignment(conn, asdict(result))
    return {
        "accepted_event_count": len(normalized),
        "hub": hub_view(result, requesting_tenant_id=tenant_id),
        "rate": rate_multiplier_interface(result, term_id=term_id,
            bound_current_term_multiplier=bound_current_term_multiplier, as_of=as_of),
    }
