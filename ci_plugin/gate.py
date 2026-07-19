"""Pure gate-decision logic for the CI/CD plugin — the whole point: break the
build on a low score, but never on a BackBond outage.

This module is deliberately I/O-free and deterministic so the acceptance
matrix (BEAAA-11729) exercises every exit path without a network. The runner
(:mod:`ci_plugin.runner`) supplies the observed facts; this module maps them
to an exit code with a single, auditable decision function.

Exit-code semantics (WS-5 frozen design on BEAAA-11715):

========================================  ====  ============
condition                                 exit  breaks build?
========================================  ====  ============
score >= threshold                          0   no
score <  threshold                          1   yes
endpoint self-identifies production (§6)    3   yes (refuse)
webhook HMAC mismatch / tamper              4   yes (fail-closed)
infra-taxonomy error, retries exhausted    75   **no** (advisory)
gate threshold is not configured           78   yes (configuration error)
smoke tier below threshold                  1   yes, but NON-bindable
========================================  ====  ============

``75`` is ``EX_TEMPFAIL`` from ``sysexits.h`` — a conventional "temporary
failure, try again later" that CI treats as a distinct, non-security signal.
The Carrier Rule: a network flake on our side must not red a partner's build.
"""
from __future__ import annotations

from dataclasses import dataclass

# The shipped WS-4 taxonomy — reuse, don't re-implement (wrap, don't fork).
from api.ci_scan_ext import ERROR_CLASS_INFRA

# ---------------------------------------------------------------------------
# Exit codes (the contract with the CI runner).
# ---------------------------------------------------------------------------
#: Score at/above threshold — gate passes, pipeline continues.
EXIT_PASS: int = 0
#: Score below threshold — gate fails, break the build.
EXIT_GATE_FAIL: int = 1
#: Endpoint self-identifies as production (§6) — refuse to scan prod.
EXIT_PRODUCTION_REFUSED: int = 3
#: Webhook HMAC mismatch/tamper — fail closed, never trust the score.
EXIT_HMAC_MISMATCH: int = 4
#: Infra-taxonomy error with retries exhausted — neutral advisory
#: (``EX_TEMPFAIL``). MUST NOT red the build; a BackBond outage is not a
#: risk signal.
EXIT_INFRA_ADVISORY: int = 75
#: Neither the vendor policy floor nor a stricter override was configured —
#: fail the job as ``EX_CONFIG`` instead of disguising the gap as an outage.
EXIT_CONFIG_ERROR: int = 78


@dataclass(frozen=True)
class GateDecision:
    """The resolved gate outcome. ``exit_code`` is what the process returns."""

    exit_code: int
    reason: str
    #: The score-based gate result was reached against a ``smoke``-tier scan.
    #: Smoke results are deterministic partials — advisory only, NEVER
    #: bindable and MUST NOT feed Underwriter pricing.
    bindable: bool = True
    #: True when the result is advisory (smoke partial, or infra-exhausted).
    advisory: bool = False
    #: The score the decision was made on, when one was measured.
    score: float | None = None
    #: The threshold the score was compared against, when one applied.
    threshold: float | None = None


def resolve_threshold(
    fail_below_score: float | None,
    vendor_policy_band_floor: float | None,
) -> float | None:
    """Resolve the gate threshold.

    Per the BEAAA-11725 ruling, the vendor's contracted policy band (looked
    up against their BackBond account) is the single source of truth for the
    default gate. ``fail_below_score`` is a **stricter-only** override: it is
    honoured only when it is *higher* than the policy floor, so a vendor can
    tighten but never silently loosen the carrier's minimum bar.

    Returns ``None`` only when neither source is available (the runner then
    treats the scan as un-gateable — advisory).
    """
    override = None if fail_below_score is None else float(fail_below_score)
    policy = None if vendor_policy_band_floor is None else float(
        vendor_policy_band_floor
    )
    if override is None:
        return policy
    if policy is None:
        return override
    # Stricter-only: the effective bar is the higher of the two.
    return max(override, policy)


def decide_exit(
    *,
    production_self_identified: bool,
    infra_exhausted: bool,
    webhook_verified: bool | None,
    score: float | None,
    threshold: float | None,
    scan_tier: str,
) -> GateDecision:
    """Map observed facts to a :class:`GateDecision`.

    Evaluation order encodes precedence (most-fail-closed first):

    1. **§6 production refusal** — if the endpoint self-identified as
       production, refuse *before anything else*. We never scan prod.
    2. **Infra exhausted** — a transport/availability failure that survived
       all retries is advisory (``75``), never a red gate.
    3. **HMAC fail-closed** — if a webhook was delivered and its signature
       did not verify, we do not trust the score at all → distinct exit 4.
    4. **Configuration** — a missing threshold is ``EX_CONFIG`` (exit 78),
       never an infra advisory.
    5. **Score gate** — score < threshold breaks the build (exit 1); at or
       above passes (exit 0). A ``smoke`` scan is always NON-bindable.

    ``webhook_verified`` is ``None`` when no webhook was configured (polling
    was the source of truth); ``True``/``False`` when one was delivered.
    """
    is_smoke = scan_tier == "smoke"

    # 1. §6 — refuse production, unconditionally, first.
    if production_self_identified:
        return GateDecision(
            exit_code=EXIT_PRODUCTION_REFUSED,
            reason=(
                "endpoint self-identified as production (§6); refusing to "
                "scan a live production system"
            ),
            bindable=False,
        )

    # 2. Infra outage that outlived retries — advisory, do NOT red the build.
    if infra_exhausted:
        return GateDecision(
            exit_code=EXIT_INFRA_ADVISORY,
            reason=(
                "infra-taxonomy error, retries exhausted; neutral advisory "
                f"(exit {EXIT_INFRA_ADVISORY}), build not gated on a BackBond "
                "outage"
            ),
            bindable=False,
            advisory=True,
        )

    # 3. Webhook delivered but signature bad — fail closed, never trust score.
    if webhook_verified is False:
        return GateDecision(
            exit_code=EXIT_HMAC_MISMATCH,
            reason=(
                "webhook HMAC-SHA256 signature mismatch; failing closed "
                "without trusting the reported score"
            ),
            bindable=False,
        )

    # 4. A missing threshold is a configuration defect, not an outage. Red
    #    both CI surfaces with EX_CONFIG so operators fix the gate setup.
    if threshold is None:
        return GateDecision(
            exit_code=EXIT_CONFIG_ERROR,
            reason=(
                "no gate threshold configured; set a vendor policy floor "
                "or fail_below_score"
            ),
            bindable=False,
        )

    # 5. No measured score after successful transport is still an infra-like
    #    inability to measure. Never fabricate a pass.
    if score is None:
        return GateDecision(
            exit_code=EXIT_INFRA_ADVISORY,
            reason="scan produced no score; advisory, not a pass",
            bindable=False,
            advisory=True,
        )

    if score < threshold:
        return GateDecision(
            exit_code=EXIT_GATE_FAIL,
            reason=(
                f"BackBond Score {score} below threshold {threshold}; "
                "breaking the build"
                + (" (smoke tier — NON-bindable advisory)" if is_smoke else "")
            ),
            bindable=not is_smoke,
            advisory=is_smoke,
            score=score,
            threshold=threshold,
        )

    return GateDecision(
        exit_code=EXIT_PASS,
        reason=(
            f"BackBond Score {score} at/above threshold {threshold}; gate "
            "passes" + (" (smoke tier — NON-bindable)" if is_smoke else "")
        ),
        bindable=not is_smoke,
        advisory=is_smoke,
        score=score,
        threshold=threshold,
    )


# Re-exported for the runner's error-class checks (single source in WS-4).
__all__ = [
    "EXIT_PASS",
    "EXIT_GATE_FAIL",
    "EXIT_PRODUCTION_REFUSED",
    "EXIT_HMAC_MISMATCH",
    "EXIT_INFRA_ADVISORY",
    "EXIT_CONFIG_ERROR",
    "GateDecision",
    "resolve_threshold",
    "decide_exit",
    "ERROR_CLASS_INFRA",
]
