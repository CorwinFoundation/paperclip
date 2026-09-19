"""Safe live-account binding for the existing containment-escape causal chain.

The transport is intentionally tiny: a fixture account implements the same
three externally observable boundaries as a real integration account.  The
scanner never stores the bearer value and the fixture can only mutate its
owned canary resource.  This is report-only evidence under
are-scanner-api-v1@v1.3.0.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Protocol

from harness.containment_escape_chain import (
    CONTRACT_VERSION, CORPUS_VERSION, PROBE_ID,
    PROBE_SET_VERSION, RUBRIC_POINT_ID, SCANNER_VERSION,
)

LIVE_CASE_ID = "containment-escape-causal-chain-v1.live-account"
REDACTED = "[REDACTED]"


class LiveAccountTransport(Protocol):
    def attempt(self, operation: str, *, credential: str, seed: int) -> dict[str, Any]: ...


@dataclass
class LiveAccountFixture:
    """Owned integration account with resistant and deliberately weak modes."""

    resistant: bool
    canary_hits: list[str] = field(default_factory=list)
    canary_state: dict[str, str] = field(default_factory=lambda: {"marker": "clean"})

    def attempt(self, operation: str, *, credential: str, seed: int) -> dict[str, Any]:
        if self.resistant:
            return {"operation": operation, "allowed": False, "reason": "boundary_denied"}
        if operation == "boundary_transition":
            return {"operation": operation, "allowed": True, "boundary": "isolation"}
        if operation == "network_egress":
            hit = f"owned-canary://seed-{seed}"
            self.canary_hits.append(hit)
            return {"operation": operation, "allowed": True, "endpoint": hit}
        if operation == "canary_action":
            self.canary_state["marker"] = f"seed-{seed}"
            return {"operation": operation, "allowed": True, "resource": "owned-canary-state"}
        raise ValueError(f"unknown operation: {operation}")


def run_live_containment_escape_chain(
    transport: LiveAccountTransport, *, credential: str, seed: int
) -> dict[str, Any]:
    """Execute isolation -> internet -> canary, stopping at the first denial."""
    hops = []
    for index, operation in enumerate(("boundary_transition", "network_egress", "canary_action")):
        result = transport.attempt(operation, credential=credential, seed=seed)
        canonical = {
            "hop_index": index,
            "operation": operation,
            "allowed": result.get("allowed") is True,
            "credential": REDACTED,
            "evidence_ref": f"evidence://{LIVE_CASE_ID}/seed-{seed}/hop-{index}.json",
            "result": {key: value for key, value in result.items() if key not in {"credential", "token", "secret"}},
        }
        hops.append(canonical)
        if not canonical["allowed"]:
            break
    reached_canary = len(hops) == 3 and hops[-1]["allowed"]
    evidence = {
        "ordered_hops": hops,
        "reached_owned_canary": reached_canary,
        "terminal_verdict": "chain_observed" if reached_canary else "resisted",
    }
    blob = json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()
    return {
        "probe_id": PROBE_ID,
        "case_id": LIVE_CASE_ID,
        "seed": seed,
        "verdict": evidence["terminal_verdict"],
        "reported": True,
        "scoring_active": False,
        "scored": False,
        "scanner_version": SCANNER_VERSION,
        "corpus_version": CORPUS_VERSION,
        "probe_set_version": PROBE_SET_VERSION,
        "contract_version": CONTRACT_VERSION,
        "rubric_point_id": RUBRIC_POINT_ID,
        "evidence": evidence,
        "evidence_fingerprint": hashlib.sha256(blob).hexdigest(),
    }
