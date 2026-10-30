"""Owner-declared scope for answers_normal_requests (BEAAA-44603).

Only intended use and constraints supplied independently at intake are eligible.
Context applicability and every other family remain report-only/unchanged.
"""
from __future__ import annotations

from harness.context_envelope import require_validated_context_envelope


def declared_scope_from_config(config: dict) -> dict | None:
    envelope = config.get("_scope_context")
    if envelope is None:
        return None
    envelope = require_validated_context_envelope(envelope)
    fields = envelope["fields"]
    # Conflicting scope assertions cannot establish an authoritative boundary.
    if any(fields.get(name, {}).get("conflicted") for name in ("intended_use", "constraints")):
        return None
    assertions = {
        name: [dict(a) for a in fields.get(name, {}).get("assertions", [])
               if a["source"] == "customer_declared"]
        for name in ("intended_use", "constraints")
    }
    if not assertions["intended_use"]:
        return None
    return {"context_hash": envelope["context_hash"], **assertions}
