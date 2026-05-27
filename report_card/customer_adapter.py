"""BEAAA-796 — Customer-facing JSON adapter for the v1.0.3 verdict envelope.

Strips internal forensic surfaces from a ScoreResponse before emitting it on
a customer-visible surface (portal, Approval Pack). Mirrors the existing
`api.scan_handlers._customer_payload` Δ4 strip pattern; lives here because
the offline `/score` ScoreResponse is the surface the Underwriter pricing
engine consumes and the customer surface needs a different field set.

Rule sourced from `contracts/are-scanner-api-v1.0.3-patch.md` §Customer-
facing adapter rule. Customer-visible MUST emit:

  - `verdict.bind_eligibility` + `verdict.primary_reasons[]`
  - `verdict.components.substrate.worst_case_loaded.{are_total,
    governance_subscore, applies_uw_guarantee_2, rationale}`
  - `verdict.components.attestation.review_state`

MAY emit (forensic drill-down):

  - `verdict.components.substrate.eligible`
  - `verdict.components.attestation.eligible`

MUST NOT emit internal forensic surfaces beyond the v1.0.x customer-facing
baseline — judge model identifiers, seed values, raw per-dim values absent
from the v1.0.x §Output customer-facing surface.

The carrier-readable verdict line is `verdict.bind_eligibility` — these
component eligibility booleans are NEVER the carrier-readable bind decision
per HoUW BEAAA-766 §rejected-alternative "dual rendering"; silent-uplift
risk.
"""
from __future__ import annotations

import copy
from typing import Any

# Top-level forensic surfaces dropped on the customer-facing payload. Matches
# the v1.0.x baseline — anything in `_CUSTOMER_STRIP_TOPLEVEL` is internal
# (Underwriter / Auditor only) and is dropped before the customer sees it.
_CUSTOMER_STRIP_TOPLEVEL: frozenset[str] = frozenset({
    "seed",
    "model_versions",
    "snapshot_hash",
    "forensic_bundle_uri",
    "cli_meta",
    "input_file_sha256s",
    "diagnostics",
})


def to_customer_payload(score_response: dict[str, Any]) -> dict[str, Any]:
    """Project a ScoreResponse to the customer-facing surface.

    Deep-copies the input so the caller's UW-facing payload is unaffected.
    The verdict envelope itself is forwarded as-is per the customer-
    facing adapter rule; component `eligible` booleans MAY remain for
    forensic drill-down but the carrier-readable line is always
    `verdict.bind_eligibility`.
    """
    payload = copy.deepcopy(score_response)
    for key in _CUSTOMER_STRIP_TOPLEVEL:
        payload.pop(key, None)

    # MUST NOT emit per-dim raw judge artifacts that the v1.0.x customer-
    # facing surface didn't carry. The v1.0.x baseline customer surface
    # carries per-dim `raw` + `evidence_uri` + `applied_threshold` on
    # pillars[*].dimensions[*]; richer per-row introspection (e.g.
    # eval_coverage_aggregator details, P1.6 coverage_breadth_pct
    # internals) was already present pre-v1.0.3 and is left intact.
    return payload


__all__ = ["to_customer_payload"]
