from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Iterable, Mapping

from .invariants import utc_now_iso


CONTINUITY_GATE_VERSION = "0.1.0"
ATTACH_RENEW_ACTION_CLASSES = frozenset({"attach", "renew"})
DEFAULT_HEARTBEAT_MAX_AGE_SECONDS = 6 * 60 * 60

# Continuity status is the single partner-facing label derived from the raw
# gap state. A heartbeat older than this fraction of the max age (but not yet
# stale) is still eligible but flagged as "grace" so a partner integration can
# warn before eligibility is actually lost.
GRACE_HEARTBEAT_FRACTION = 0.5

# The four partner-facing continuity statuses. ``healthy`` and ``grace`` are
# attach/renew eligible; ``gap`` and ``stale_ineligible`` are not.
CONTINUITY_STATUS_HEALTHY = "healthy"
CONTINUITY_STATUS_GRACE = "grace"
CONTINUITY_STATUS_GAP = "gap"
CONTINUITY_STATUS_STALE_INELIGIBLE = "stale_ineligible"

# Reasons that make attach/renew hard-ineligible (no live/complete telemetry).
_STALE_INELIGIBLE_REASONS = frozenset({"heartbeat_stale", "heartbeat_missing", "no_sequence_data"})


class ContinuityGateError(ValueError):
    """Raised when the continuity gate cannot evaluate an action request."""


@dataclass(frozen=True)
class TelemetryGapState:
    tenant_id: str
    agent_id: str
    deployment_id: str
    collector_id: str
    active: bool
    missing_batch_seq: tuple[int, ...] = ()
    latest_batch_seq: int | None = None
    latest_heartbeat_at: str | None = None
    heartbeat_age_seconds: int | None = None
    heartbeat_max_age_seconds: int = DEFAULT_HEARTBEAT_MAX_AGE_SECONDS
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["missing_batch_seq"] = list(self.missing_batch_seq)
        out["reasons"] = list(self.reasons)
        out["continuity_status"] = classify_continuity_status(self)
        return out


@dataclass(frozen=True)
class ContinuityGateDecision:
    action_class: str
    permitted: bool
    refusal_reason: str | None
    gap_state: TelemetryGapState
    audit_events: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    gate_version: str = CONTINUITY_GATE_VERSION
    contract_ref: str = "evidence-profile-v1@v1.0"
    scanner_contract_ref: str = "are-scanner-api-v1@v1.3.0"

    @property
    def continuity_status(self) -> str:
        return classify_continuity_status(self.gap_state)

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_class": self.action_class,
            "permitted": self.permitted,
            "refusal_reason": self.refusal_reason,
            "continuity_status": self.continuity_status,
            "gap_state": self.gap_state.to_dict(),
            "audit_events": list(self.audit_events),
            "gate_version": self.gate_version,
            "contract_ref": self.contract_ref,
            "scanner_contract_ref": self.scanner_contract_ref,
        }


def evaluate_attach_renew_continuity(
    events: Iterable[Mapping[str, Any]],
    *,
    action_class: str,
    tenant_id: str,
    agent_id: str,
    deployment_id: str,
    collector_id: str = "default",
    evaluated_at: datetime | str | None = None,
    heartbeat_max_age_seconds: int = DEFAULT_HEARTBEAT_MAX_AGE_SECONDS,
    prior_gap_state: TelemetryGapState | Mapping[str, Any] | None = None,
) -> ContinuityGateDecision:
    """Evaluate no-logs-no-coverage enforcement for attach/renew actions.

    The gate is outside BackBond Scanner scoring. It reads runtime evidence
    continuity fields from Evidence Profile v1: identity tuple, heartbeat
    records, and monotonic ``batch_seq`` values. ``attach`` and ``renew`` refuse
    while a discontinuity is active; other action classes pass through but still
    return the computed state for observability.
    """
    action = str(action_class).strip().lower()
    if not action:
        raise ContinuityGateError("action_class is required")
    if heartbeat_max_age_seconds <= 0:
        raise ContinuityGateError("heartbeat_max_age_seconds must be positive")

    now = _parse_dt(evaluated_at) if evaluated_at is not None else datetime.now(UTC)
    state = compute_gap_state(
        events,
        tenant_id=tenant_id,
        agent_id=agent_id,
        deployment_id=deployment_id,
        collector_id=collector_id,
        evaluated_at=now,
        heartbeat_max_age_seconds=heartbeat_max_age_seconds,
    )
    refusal_reason = _refusal_reason(state) if action in ATTACH_RENEW_ACTION_CLASSES and state.active else None
    audit_events = []
    if refusal_reason is not None:
        audit_events.append(_audit_event("attach_renew_refused", state, action, now, refusal_reason))
    if _was_active(prior_gap_state) and not state.active:
        audit_events.append(
            _audit_event(
                "telemetry_gap_recovered",
                state,
                action,
                now,
                "telemetry continuity restored; attach/renew eligibility restored",
            )
        )
    return ContinuityGateDecision(
        action_class=action,
        permitted=refusal_reason is None,
        refusal_reason=refusal_reason,
        gap_state=state,
        audit_events=tuple(audit_events),
    )


def compute_gap_state(
    events: Iterable[Mapping[str, Any]],
    *,
    tenant_id: str,
    agent_id: str,
    deployment_id: str,
    collector_id: str = "default",
    evaluated_at: datetime | str | None = None,
    heartbeat_max_age_seconds: int = DEFAULT_HEARTBEAT_MAX_AGE_SECONDS,
) -> TelemetryGapState:
    now = _parse_dt(evaluated_at) if evaluated_at is not None else datetime.now(UTC)
    key = {
        "tenant_id": _required_str(tenant_id, "tenant_id"),
        "agent_id": _required_str(agent_id, "agent_id"),
        "deployment_id": _required_str(deployment_id, "deployment_id"),
        "collector_id": _required_str(collector_id, "collector_id"),
    }

    seqs: set[int] = set()
    latest_heartbeat: datetime | None = None
    for event in events:
        if not _matches_stream(event, key):
            continue
        seq = _event_batch_seq(event)
        if seq is not None:
            seqs.add(seq)
        heartbeat_at = _event_heartbeat_at(event)
        if heartbeat_at is not None and (latest_heartbeat is None or heartbeat_at > latest_heartbeat):
            latest_heartbeat = heartbeat_at

    latest_seq = max(seqs) if seqs else None
    missing = _missing_sequences(seqs)
    reasons: list[str] = []
    if not seqs:
        reasons.append("no_sequence_data")
    elif missing:
        reasons.append("batch_sequence_gap")

    heartbeat_age_seconds: int | None = None
    latest_heartbeat_at: str | None = None
    if latest_heartbeat is None:
        reasons.append("heartbeat_missing")
    else:
        latest_heartbeat_at = _iso(latest_heartbeat)
        heartbeat_age_seconds = max(0, int((now - latest_heartbeat).total_seconds()))
        if heartbeat_age_seconds > heartbeat_max_age_seconds:
            reasons.append("heartbeat_stale")

    return TelemetryGapState(
        tenant_id=key["tenant_id"],
        agent_id=key["agent_id"],
        deployment_id=key["deployment_id"],
        collector_id=key["collector_id"],
        active=bool(reasons),
        missing_batch_seq=tuple(missing),
        latest_batch_seq=latest_seq,
        latest_heartbeat_at=latest_heartbeat_at,
        heartbeat_age_seconds=heartbeat_age_seconds,
        heartbeat_max_age_seconds=heartbeat_max_age_seconds,
        reasons=tuple(reasons),
    )


def classify_continuity_status(state: TelemetryGapState) -> str:
    """Map a raw gap state to the single partner-facing continuity status.

    Precedence (strongest failure wins):
      * ``stale_ineligible`` — heartbeat stale/missing or no sequence data.
      * ``gap`` — batch sequence gap while the heartbeat is still fresh.
      * ``grace`` — no active discontinuity but the heartbeat is aging past the
        grace fraction of its max age (eligible, warn).
      * ``healthy`` — fresh heartbeat, contiguous sequences.
    """
    reasons = set(state.reasons)
    if _STALE_INELIGIBLE_REASONS & reasons:
        return CONTINUITY_STATUS_STALE_INELIGIBLE
    if "batch_sequence_gap" in reasons:
        return CONTINUITY_STATUS_GAP
    age = state.heartbeat_age_seconds
    if age is not None and age > int(state.heartbeat_max_age_seconds * GRACE_HEARTBEAT_FRACTION):
        return CONTINUITY_STATUS_GRACE
    return CONTINUITY_STATUS_HEALTHY


def _matches_stream(event: Mapping[str, Any], key: Mapping[str, str]) -> bool:
    return all(_event_attr(event, field) == value for field, value in key.items())


def _event_batch_seq(event: Mapping[str, Any]) -> int | None:
    value = _event_attr(event, "batch_seq")
    if value is None:
        return None
    try:
        seq = int(value)
    except (TypeError, ValueError):
        return None
    return seq if seq >= 1 else None


def _event_heartbeat_at(event: Mapping[str, Any]) -> datetime | None:
    event_type = str(event.get("event_type") or "")
    payload = event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
    action_class = str(payload.get("action_class") or payload.get("backbond.evidence.action_class") or "")
    if event_type not in {"agent_invocation", "security_event"} and action_class not in {
        "heartbeat",
        "telemetry_heartbeat",
    }:
        return None
    value = (
        payload.get("heartbeat_at")
        or payload.get("backbond.evidence.heartbeat_at")
        or event.get("logged_at")
    )
    return _parse_dt(value) if isinstance(value, str) else None


def _event_attr(event: Mapping[str, Any], field: str) -> Any:
    payload = event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
    return (
        payload.get(field)
        or payload.get(f"backbond.evidence.{field}")
        or event.get(field)
        or event.get(f"backbond.evidence.{field}")
    )


def _missing_sequences(seqs: set[int]) -> list[int]:
    if not seqs:
        return []
    return [seq for seq in range(min(seqs), max(seqs) + 1) if seq not in seqs]


def _refusal_reason(state: TelemetryGapState) -> str:
    return "telemetry_discontinuity_active: " + ",".join(state.reasons)


def _audit_event(
    event_name: str,
    state: TelemetryGapState,
    action_class: str,
    emitted_at: datetime,
    reason: str,
) -> dict[str, Any]:
    return {
        "event_id": f"continuity-gate:{event_name}:{state.agent_id}:{int(emitted_at.timestamp())}",
        "event_type": "security_event",
        "logged_at": _iso(emitted_at),
        "schema_version": "log-event-schema-v1.0",
        "payload": {
            "event": event_name,
            "action_class": action_class,
            "refusal_reason": reason if event_name == "attach_renew_refused" else None,
            "reason": reason,
            "tenant_id": state.tenant_id,
            "agent_id": state.agent_id,
            "deployment_id": state.deployment_id,
            "collector_id": state.collector_id,
            "gap_state": state.to_dict(),
            "gate_version": CONTINUITY_GATE_VERSION,
            "contract_ref": "evidence-profile-v1@v1.0",
            "scanner_contract_ref": "are-scanner-api-v1@v1.3.0",
        },
        "provenance": {
            "class": "backbond_policy_engine",
            "component": "post_bind_ingest.continuity_gate",
            "gate_version": CONTINUITY_GATE_VERSION,
        },
        "event_ingest": {"normalized_at": utc_now_iso()},
    }


def _was_active(state: TelemetryGapState | Mapping[str, Any] | None) -> bool:
    if state is None:
        return False
    if isinstance(state, TelemetryGapState):
        return state.active
    return bool(state.get("active"))


def _required_str(value: str, field_name: str) -> str:
    out = str(value).strip()
    if not out:
        raise ContinuityGateError(f"{field_name} is required")
    return out


def _parse_dt(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
