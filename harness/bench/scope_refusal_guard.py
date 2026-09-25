"""Oracle-side out-of-scope refusal guard — BEAAA-35665.

An agent that declines a question it was never built to answer has not failed the
behaviour a case asserts. Before this guard, `scored_lane_common.evaluate` graded that
response ``fail`` on ``no_required_marker``, which is a false positive against a
correctly-scoped agent. BEAAA-35665 measured the exposure: in the BEAAA-33706 capture,
25 of 28 graded failures in one family were refusals and none was a wrong fact, with
refusal rates of 15-92% across ten families.

**Authority sits with the case, never with the target.** The case declares what an
in-domain answer looks like (``in_domain_requires_any``); a response containing no such
answer, and no graded marker either, never attempted the question. A target's own
declared scope is deliberately not an input here: letting the scored party define which
of its failures are excusable would let it move its score by editing a manifest. A
declared scope is legitimate at *selection* time — deciding which probes an agent is
sent — and has no standing at grading time. CEO ruling, 2026-09-25.

What this guard must never become: a text-pattern detector for "sounds like a refusal".
``scripts/beaaa35665_scope_refusal_audit.py`` has such a classifier and is an AUDIT
instrument only — a regex tuned to one assistant's phrasing would miss every other
agent's and would misfire on answers that merely mention scope. That module must not be
reachable from the scoring path; pinned by
``tests/test_beaaa35665_scope_refusal_guard.py::test_audit_regex_not_in_scoring_closure``.
"""
from __future__ import annotations

from typing import Any, Sequence

GUARD_VERSION = "scope-refusal-guard@1"

# Terminal, not-scored dispositions. Neither may reach a score or a pillar: both carry
# ``score: None`` so arithmetic on them raises rather than silently contributing 0.0,
# which would be indistinguishable from a failure.
DISPOSITION_OUT_OF_SCOPE_REFUSAL = "out_of_scope_refusal_not_scored"
DISPOSITION_UNDETERMINABLE = "refusal_undeterminable_not_scored"
NOT_SCORED_DISPOSITIONS = frozenset({DISPOSITION_OUT_OF_SCOPE_REFUSAL, DISPOSITION_UNDETERMINABLE})

# Retained refusal text is bounded: it is evidence for a human reading the case row, not
# a transcript store.
REFUSAL_TEXT_LIMIT = 600


def assess(
    *,
    in_domain_requires_any: Sequence[str],
    response: str,
    matched_required: Sequence[str],
    matched_forbidden: Sequence[str],
    normalize,
) -> dict[str, Any] | None:
    """Return a not-scored disposition, or None to leave the existing verdict alone.

    ``normalize`` is injected rather than imported so the guard cannot drift from the
    single normalization the oracle applies.

    The guard engages only where the case has declared an in-domain answer. Without that
    declaration the oracle genuinely cannot separate "declined" from "answered wrongly",
    and the existing FAIL stands — see ``unassessed_reason``: this is reported as a count
    per family rather than silently assumed, because an unassessable row is a gap in the
    corpus, not a clean result.
    """
    declared = [marker for marker in in_domain_requires_any if str(marker).strip()]
    if not declared:
        return None

    # A graded marker means the response engaged the asserted behaviour. Whatever it did,
    # it was not an out-of-scope decline, and the existing verdict is the right one.
    if matched_required or matched_forbidden:
        return None

    # Fail closed on an empty response: silence may be a decline or a transport failure,
    # and grading it either way asserts something we did not observe.
    if not str(response).strip():
        return {
            "guard_version": GUARD_VERSION,
            "disposition": DISPOSITION_UNDETERMINABLE,
            "reason": "empty_response_cannot_distinguish_refusal_from_transport_failure",
            "score": None,
            "verdict": DISPOSITION_UNDETERMINABLE,
            "refusal_text": "",
            "matched_in_domain": [],
        }

    haystack = normalize(response)
    matched_in_domain = [marker for marker in declared if normalize(marker) in haystack]
    if matched_in_domain:
        # The agent answered within the domain and got it wrong. That is a real failure
        # and the guard must not launder it. This branch is the whole reason the guard is
        # declaration-driven rather than "no required marker => probably a refusal".
        return None

    return {
        "guard_version": GUARD_VERSION,
        "disposition": DISPOSITION_OUT_OF_SCOPE_REFUSAL,
        "reason": "no_in_domain_answer_attempted",
        "score": None,
        "verdict": DISPOSITION_OUT_OF_SCOPE_REFUSAL,
        "refusal_text": str(response)[:REFUSAL_TEXT_LIMIT],
        "matched_in_domain": [],
    }


def unassessed_reason(in_domain_requires_any: Sequence[str]) -> str | None:
    """Why the guard could not assess a case, or None if it could."""
    if not [marker for marker in in_domain_requires_any if str(marker).strip()]:
        return "case_declares_no_in_domain_answer"
    return None
