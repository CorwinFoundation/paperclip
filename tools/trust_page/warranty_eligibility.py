"""Warranty remedy eligibility — Trust Page v1 wiring (BEAAA-11926 / WI-3).

Wires the trust page's "warranty remedy eligibility" field to the WI-6 Warranty
Terms v1 structure (BEAAA-11920 §2 Eligibility Rule) rather than a hardcoded
placeholder. The WI-6 doc defines attachment as a *conjunction* of conditions,
several of which are Ari-bracketed / counsel-gated and therefore NOT ratified in
Phase 1. Evaluating the real rule against the current (unratified) inputs
deterministically yields ``pending`` — which is the correct and honest v1 posture
(confirmed by Trust Page spec §1 and the Legal wording review, BEAAA-11927).

There is deliberately NO code path that returns ``eligible`` in v1: eligibility
approval is a per-agent Head-of-Underwriting workflow (WI-6 §11) that requires a
ratified minimum band, allocated caps/reserve, and counsel clearance. Until those
land, the only reachable states are ``pending`` and ``not_eligible``.

Warranty-safe: eligibility is a *status*, never an attached warranty. See the
disclaimer copy enforced by ``render.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# WI-6 (BEAAA-11920) is a Legal drafting artifact, not code. Pin its identity so
# every rendered record traces the warranty field back to its source of truth.
WARRANTY_TERMS_SOURCE = "BEAAA-11920 Warranty Terms v1 structure v0.1 (§2 Eligibility Rule)"
WARRANTY_ELIGIBILITY_STATES = ("eligible", "not_eligible", "pending")

# The WI-6 §2 conjunction. Each entry is a gating condition that must be TRUE for a
# warranty remedy to attach. Conditions marked ``ratified=False`` are Ari-bracketed
# or counsel-gated in the WI-6 doc and cannot be satisfied in Phase 1.
_WI6_CONDITIONS = (
    # key,                       description,                                           ratified_in_v1
    ("ratified_minimum_band",    "minimum eligible BackBond Score band is ratified",    False),  # §3 [Ari bracket]
    ("agent_at_or_above_band",   "agent's current band is at/above the minimum",        None),   # depends on band + ratified min
    ("huw_agent_approval",       "Head of Underwriting approved this agent",            False),  # §11 per-agent workflow
    ("monitoring_continuity_ok", "monitoring-continuity condition is live",             None),   # §8, from WI-2 continuity
    ("caps_reserve_allocated",   "caps / reserve capacity is allocated",                False),  # §6 [Ari bracket]
    ("counsel_cleared",          "outside counsel cleared the warranty wording",        False),  # §12 gate
)


@dataclass(frozen=True)
class WarrantyEligibility:
    status: str                       # one of WARRANTY_ELIGIBILITY_STATES
    unmet_conditions: tuple[str, ...] = ()
    source: str = WARRANTY_TERMS_SOURCE
    rationale: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "unmet_conditions": list(self.unmet_conditions),
            "source": self.source,
            "rationale": self.rationale,
        }


def evaluate_warranty_eligibility(
    *,
    continuity_status: str,
    band_letter: str | None = None,
    inputs: dict[str, bool] | None = None,
) -> WarrantyEligibility:
    """Evaluate WI-6 §2 eligibility against current inputs.

    ``inputs`` may override the ratified/derived conditions for testing or once a
    condition genuinely lands (e.g. HUW approval). Unsupplied conditions default to
    their WI-6 v1 ratification state (unratified brackets -> False).

    Returns ``pending`` when the blockers are *program-level unratified brackets*
    (min band / caps / counsel) — i.e. no per-agent determination is even possible
    yet. Returns ``not_eligible`` when an agent-specific condition fails (e.g.
    continuity not healthy) even though the program conditions are ratified.
    """
    inputs = dict(inputs or {})
    unmet: list[str] = []
    program_bracket_unmet: list[str] = []

    for key, _desc, ratified in _WI6_CONDITIONS:
        if key == "monitoring_continuity_ok":
            ok = continuity_status == "healthy"
            program = False
        elif key == "agent_at_or_above_band":
            # Cannot be judged until the minimum band is ratified; treat as a
            # program bracket dependency in v1.
            ok = inputs.get(key, False)
            program = True
        else:
            ok = inputs.get(key, bool(ratified))
            program = ratified is False  # unratified bracket = program-level blocker

        if not ok:
            unmet.append(key)
            if program:
                program_bracket_unmet.append(key)

    if not unmet:
        # Only reachable if a caller explicitly ratifies every condition (not in v1).
        return WarrantyEligibility(status="eligible", rationale="all WI-6 §2 conditions satisfied")

    if program_bracket_unmet:
        return WarrantyEligibility(
            status="pending",
            unmet_conditions=tuple(unmet),
            rationale=(
                "Warranty program conditions are not yet ratified "
                f"(unratified: {', '.join(program_bracket_unmet)}); per-agent "
                "eligibility cannot be determined. Status is pending, not a warranty in force."
            ),
        )

    return WarrantyEligibility(
        status="not_eligible",
        unmet_conditions=tuple(unmet),
        rationale=(
            "Program conditions ratified but agent-specific conditions unmet "
            f"({', '.join(unmet)}). Not eligible until re-verified/re-approved."
        ),
    )
