"""Normalize -> (local rule eval on content) -> redact to no-content emittable event.

CRITICAL BOUNDARY: raw content (post text, recipient, CRM data, transcript, PII)
is available ONLY transiently, in-process, inside the partner's environment, for
local rule evaluation. It is NEVER placed on an emittable event. Redaction is
fail-closed: if a content field cannot be hashed/stripped, the event is dropped,
not emitted.

What leaves the appliance = action_class, counts, peril determinations, and an
evidence-commitment hash. Nothing else.
"""
from __future__ import annotations
import hashlib, json
from typing import Dict, Any, Tuple

# Fields that may carry partner/customer content. These NEVER appear on an
# emittable event; they are hashed into evidence_hash and then dropped.
CONTENT_FIELDS = {
    "content", "text", "body", "message", "transcript", "prompt", "output",
    "completion", "recipient", "recipients", "to", "email", "phone", "address",
    "pii", "customer_data", "crm_record", "audio", "recording", "attachment",
    "subject", "caption", "dm", "post_text", "payload",
    "command", "cmd", "argv", "script",   # coding-agent command text (may carry secrets/paths)
}

# Enso action type -> contract-v1.0 action_class
ACTION_CLASS_MAP = {
    "social_post": "message",
    "post": "message",
    "newsletter_generated": "message",
    "community_message": "message",
    "dm": "message",
    "reply": "message",
    "tool_call": "tool_call",
    "api_call": "tool_call",
    "meeting_booked": "transaction",
    "booking": "transaction",
    "payment": "transaction",
    "purchase": "transaction",
    "crm_update": "data_access",
    "data_access": "data_access",
    "record_read": "data_access",
    "human_handoff": "human_escalation",
    "escalation": "human_escalation",
}


def _canonical(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def evidence_hash(raw: Dict[str, Any]) -> str:
    """A stable commitment over the event's content, so a sealed artifact retained
    by the partner can later be verified against this hash at claim time — without
    the content ever traveling."""
    content = {k: raw[k] for k in raw if k in CONTENT_FIELDS}
    return "sha256:" + hashlib.sha256(_canonical(content)).hexdigest()


def normalize(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Produce a WORKING event (content retained in `_content` for local rule eval only)."""
    etype = str(raw.get("type") or raw.get("event_type") or raw.get("action") or "unknown")
    action_class = ACTION_CLASS_MAP.get(etype, "message")
    content = {k: v for k, v in raw.items() if k in CONTENT_FIELDS}
    return {
        "tenant_id": str(raw.get("tenant_id") or raw.get("customer_id") or raw.get("account") or "unknown"),
        "agent_id": str(raw.get("agent_id") or raw.get("agent") or "unknown"),
        "event_id": str(raw.get("event_id") or raw.get("id") or ""),
        "ts": str(raw.get("ts") or raw.get("timestamp") or ""),
        "event_type": etype,
        "operation": raw.get("operation") or ("execute_tool" if action_class == "tool_call" else "chat"),
        "gen_ai.operation.name": raw.get("operation") or ("execute_tool" if action_class == "tool_call" else "chat"),
        "action_class": action_class,
        "channel": raw.get("channel"),
        "outcome": raw.get("outcome", "ok"),
        "billable": bool(raw.get("billable", True)),
        "evidence_hash": evidence_hash(raw),
        "_content": content,           # LOCAL ONLY — stripped before emission
        "_raw": dict(raw),             # LOCAL ONLY — full event for rule eval; never emitted
        "_raw_field_names": sorted(raw.keys()),  # for the no-content audit, names only
    }


# Emittable event: an explicit allowlist. If a field is not here, it does not leave.
EMIT_ALLOWLIST = (
    "tenant_id", "agent_id", "event_id", "ts", "event_type",
    "operation", "gen_ai.operation.name", "action_class", "channel",
    "outcome", "billable", "evidence_hash",
)


def redact(norm: Dict[str, Any]) -> Dict[str, Any]:
    """Fail-closed no-content projection. Build the emittable event from an
    allowlist; then assert no content field or content value survived."""
    out = {k: norm.get(k) for k in EMIT_ALLOWLIST}
    # fail-closed check 1: no content field name present
    for f in CONTENT_FIELDS:
        if f in out:
            raise ContentLeak(f"content field '{f}' present in emittable event")
    # fail-closed check 2: no content VALUE present (defends against value copied into a metadata field)
    content_values = {json.dumps(v, sort_keys=True) for v in norm.get("_content", {}).values()}
    for k, v in out.items():
        if k == "evidence_hash":
            continue
        if json.dumps(v, sort_keys=True) in content_values and v not in (None, "", "unknown"):
            raise ContentLeak(f"content value leaked into metadata field '{k}'")
    return out


class ContentLeak(Exception):
    """Raised when redaction detects content in an emittable event. Fail closed."""
