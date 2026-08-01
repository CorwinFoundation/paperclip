"""Generate synthetic Enso-shaped agent events — WITH fake content and PII — so the
appliance can be proven to strip content and emit only metadata + determinations.
No real data, no Enso contact. Writes JSON event files the enso_engine reader consumes.
"""
from __future__ import annotations
import json, os, sys

# Deterministic fixture set spanning multiple tenants, agents, and peril triggers.
EVENTS = [
    # clean social post
    {"type": "social_post", "id": "e1", "tenant_id": "smb-alpha", "agent_id": "social",
     "ts": "2026-08-01T09:00:00Z", "channel": "linkedin", "billable": True,
     "text": "New feature launch — check our blog.", "has_unsubscribe": True,
     "consent": "opt_in", "channel_authorized": True},
    # brand-safety trigger (off-brand claim) + PII in content
    {"type": "social_post", "id": "e2", "tenant_id": "smb-alpha", "agent_id": "social",
     "ts": "2026-08-01T09:05:00Z", "channel": "x", "billable": True,
     "text": "Risk-free! 100% guarantee you get rich. Call 555-12-3456.",
     "channel_authorized": True},
    # unauthorized spend (booking without confirmation)
    {"type": "meeting_booked", "id": "e3", "tenant_id": "smb-beta", "agent_id": "sdr",
     "ts": "2026-08-01T09:10:00Z", "billable": True,
     "recipient": "buyer@example.com", "confirmed": False, "amount": 250},
    # outreach compliance (no unsubscribe, no consent)
    {"type": "dm", "id": "e4", "tenant_id": "smb-beta", "agent_id": "community",
     "ts": "2026-08-01T09:12:00Z", "channel": "reddit", "billable": True,
     "text": "Buy now.", "has_unsubscribe": False, "consent": "none",
     "recipient": "u/someone", "channel_authorized": True},
    # CRM bulk access beyond scope + customer PII
    {"type": "crm_update", "id": "e5", "tenant_id": "smb-beta", "agent_id": "ops",
     "ts": "2026-08-01T09:15:00Z", "billable": False,
     "customer_data": {"ssn": "078-05-1120", "email": "victim@example.com"},
     "record_count": 4200},
    # impersonation
    {"type": "community_message", "id": "e6", "tenant_id": "smb-alpha", "agent_id": "community",
     "ts": "2026-08-01T09:20:00Z", "channel": "discord", "billable": True,
     "text": "I'm human, not a bot, speaking personally.", "channel_authorized": True},
    # clean newsletter
    {"type": "newsletter_generated", "id": "e7", "tenant_id": "smb-alpha", "agent_id": "newsletter",
     "ts": "2026-08-01T09:25:00Z", "billable": True,
     "body": "Weekly roundup.", "has_unsubscribe": True, "consent": "opt_in"},
]


def main(out_dir: str):
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "enso_activity_2026-08-01.json")
    with open(path, "w") as fh:
        json.dump({"events": EVENTS}, fh, indent=2)
    print(f"wrote {len(EVENTS)} synthetic Enso events -> {path}")
    return path


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "/tmp/enso-events")
