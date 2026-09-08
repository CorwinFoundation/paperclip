"""Shared behavioral-family runner (BEAAA-16272, parent BEAAA-16264).

This is the single library every Path-B lane-C family calls into. It owns the
non-semantic machinery — case iteration, deterministic phrasing rotation
(surface_seed), the fired-probe ledger, canary anti-gaming registration, K=5
replication, and the canonical sha256 evidence fingerprint — so that no family
duplicates rubric/judge logic (AGENTS.md anti-pattern). Each family supplies
only its own case corpus and its own semantic grader.

Design invariants (six-point validation convention, BEAAA-16264):
  1. In-repo, embedded cases, NO external framework on the critical path.
  5. reported-not-scored: `scored=False`; nothing here imports `rubric.scoring`,
     so golden N=5 total/pillar deltas are 0 by construction.
  6. Fired-probe ledger + canary anti-gaming registration; surface_seed
     phrasing rotation enabled.

Determinism contract: for a fixed (family, policy, seed) the evidence blob is
byte-identical. `seed` selects a phrasing variant per case (anti-memorization),
but the *verdict* must be invariant across phrasings — enforced in CI.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Callable

from . import applicability

# Candidate metadata, pinned per BEAAA-16272 (baseline 0.4.1 -> candidate 0.5.0).
SCANNER_VERSION = "0.5.0"
CORPUS_VERSION_TEMPLATE = "{family}@0.1.0"
CONTRACT = "are-scanner-api-v1@v1.0"
REPLICATION_K = 5

# Verdict vocabulary. reported-not-scored: these are evidence verdicts, never a
# rubric raw score.
PASS = "PASS"
FAIL = "FAIL"
NA = "N/A"
# Terminal verdict for a decision that violates its family's expected-result
# contract — a required boolean field is absent or is not a genuine bool.
# INVALID is NOT a fail-open PASS: an omitted field can never be graded PASS
# (BEAAA-16297; CTO changes-requested on 46b66411). Distinct from FAIL so a
# malformed decision is auditable separately from a genuinely-wrong behavior.
INVALID = "INVALID"


def require_bool(decision: dict[str, Any], field: str) -> tuple[bool, bool | None]:
    """Strictly read a required boolean decision field — NO fail-open coercion.

    Returns ``(ok, value)``. ``ok`` is ``False`` when the field is absent or is
    not a genuine ``bool``; callers map that to :data:`INVALID`. ``bool`` is a
    subclass of ``int``, so an ``int``/``float`` like ``1`` or ``0`` is rejected
    on purpose — only ``True``/``False`` are accepted (BEAAA-16297).
    """
    if field not in decision:
        return False, None
    value = decision[field]
    if not isinstance(value, bool):
        return False, None
    return True, value


def binary_match_grade(decision: dict[str, Any], *, field: str, expected: Any) -> str:
    """Single anti-fail-open grading chokepoint every lane-C family calls.

    Strict-reads ``field`` as a bool and returns PASS iff it equals ``expected``.
    A missing field or non-boolean value yields :data:`INVALID` (never PASS), so
    an omitted field can no longer sail past a false-oracle control case. No
    family re-implements the coercion (AGENTS.md no-duplicate-logic rule).
    """
    ok, value = require_bool(decision, field)
    if not ok:
        return INVALID
    return PASS if value == bool(expected) else FAIL


@dataclass(frozen=True)
class Case:
    """One behavioral scenario.

    `scenario_variants` are semantically-equivalent phrasings; surface_seed
    rotation picks one per seed so an agent cannot memorize exact wording. The
    oracle is family-specific ground truth the grader reads.
    """

    case_id: str
    category: str
    rubric_point_ids: tuple[str, ...]
    scenario_variants: tuple[str, ...]
    oracle: dict[str, Any]
    is_canary: bool = False

    def __post_init__(self) -> None:
        if not self.scenario_variants:
            raise ValueError(f"{self.case_id}: at least one scenario variant required")
        if not self.rubric_point_ids:
            raise ValueError(f"{self.case_id}: rubric_point_ids required (both namespaces)")


# A policy maps (case, rendered_prompt) -> a structured behavioral decision.
Policy = Callable[[Case, str], dict[str, Any]]
# A grader maps (case, decision) -> one of PASS/FAIL/N/A.
Grader = Callable[[Case, dict[str, Any]], str]


@dataclass(frozen=True)
class FamilySpec:
    name: str
    version: str  # e.g. "human_escalation_flagging@0.1.0"
    rubric_point_ids: tuple[str, ...]  # union across cases; both namespaces
    expected_result_contract: str
    cases: tuple[Case, ...]
    grader: Grader
    replication_required: int = REPLICATION_K
    # Applicability gate (Tier-4). None => always applicable. When set, the
    # runner emits N/A for the whole family unless the architecture flag is set.
    # When set, the family is exercised only if the target's capability manifest
    # declares this flag true; otherwise every case is N/A and nothing is
    # exercised (BEAAA-29788). Residual risk, accepted and recorded there: the
    # manifest is self-declared, so a target that declines the capability also
    # silences this family's anti-gaming canaries.
    applicability_flag: str | None = None

    def __post_init__(self) -> None:
        if not self.cases:
            raise ValueError(f"{self.name}: no cases")
        if not any(c.is_canary for c in self.cases):
            raise ValueError(f"{self.name}: at least one canary case required (anti-gaming)")


def surface_seed_phrasing(case: Case, seed: int) -> str:
    """Deterministically pick a phrasing variant for this (case, seed).

    Rotation index folds a stable hash of the case_id with the seed so the
    corpus surface differs across seeds (anti-memorization) while staying
    byte-stable for a fixed seed. Uses blake2b for a portable, stable digest
    (Python's builtin str hash is salted per-process and unusable here).
    """
    digest = hashlib.blake2b(case.case_id.encode("utf-8"), digest_size=8).digest()
    stable = int.from_bytes(digest, "big")
    idx = (stable + seed) % len(case.scenario_variants)
    return case.scenario_variants[idx]


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


# BEAAA-29793 Rule A: `undeclared` is not `false`.
#
# `architecture_flags.get(flag, False)` collapsed two different epistemic states:
# the target answered "no", and the target never answered at all. The collapse
# reached the evidence record, not just the branch — `applicability_reason` was
# built from the same defaulted lookup, so an undeclared flag was written out as
# `has_mcp_tool_integration=False`, a declaration the target never made.
#
# Resolution lives in `evals.aiuc_path_b.applicability`, which the production
# resolver (`tools.registered_case_executor._resolve_applicability`) also calls.
# Per the CTO ruling this is convergence, not invention: production already told
# the two states apart, and `_declared_applicability_flag`'s docstring states the
# invariant that the two paths cannot disagree about which surfaces a conditional
# family applies to. On the undeclared case they did. Sharing the resolver makes
# the invariant structural rather than asserted, and keeps this seat from minting
# a second vocabulary for the same decision.
#
# Execution semantics are deliberately unchanged (Rule C). `undeclared` gates the
# family exactly as a declared decline does, so BEAAA-29788 stands and no absent
# surface is exercised. Only the evidence changes: it now carries which of the
# states it was, as data, and stops asserting a decline nobody made.
UNDECLARED = applicability.UNDECLARED
NOT_GATED = applicability.NOT_GATED
DECLARED_TRUE = applicability.DECLARED_TRUE
DECLARED_FALSE = applicability.DECLARED_FALSE


def run_family(
    spec: FamilySpec,
    policy: Policy,
    *,
    seed: int,
    architecture_flags: dict[str, bool] | None = None,
) -> dict[str, Any]:
    """Exercise one family under one reference policy at one seed.

    Returns an evidence dict with a canonical sha256 fingerprint. reported-not-
    scored: `scored=False`, and this function never touches a scoring path.
    """
    resolution = applicability.resolve(spec.applicability_flag, architecture_flags)
    applicable = resolution.applicable

    # BEAAA-29788: the roll-up lists below record what was *exercised*. When the
    # family is not applicable to the target no policy is invoked and no grader
    # runs, so nothing is exercised: both lists stay empty and the cases are
    # recorded in the parallel skipped-* lists instead. Element type is unchanged
    # (list[str] of case_id) so a consumer reading element shape still works,
    # while a consumer counting entries now gets the truth. Per-case rows below
    # remain individually visible with verdict=NA and their skip reason.
    fired_probe_ledger: list[str] = []
    canary_registration: list[str] = []
    skipped_probe_ledger: list[str] = []
    skipped_canary_registration: list[str] = []
    case_results: list[dict[str, Any]] = []

    for case in spec.cases:
        prompt = surface_seed_phrasing(case, seed)
        if not applicable:
            verdict = NA
            decision: dict[str, Any] = {"skipped": "not_applicable"}
            skipped_probe_ledger.append(case.case_id)
            if case.is_canary:
                skipped_canary_registration.append(case.case_id)
        else:
            decision = policy(case, prompt)
            verdict = spec.grader(case, decision)
            fired_probe_ledger.append(case.case_id)
            if case.is_canary:
                canary_registration.append(case.case_id)
        case_results.append(
            {
                "case_id": case.case_id,
                "category": case.category,
                "rubric_point_ids": list(case.rubric_point_ids),
                "is_canary": case.is_canary,
                "rendered_prompt": prompt,
                "decision": decision,
                "verdict": verdict,
            }
        )

    evidence: dict[str, Any] = {
        "family": spec.name,
        "probe_set_version": spec.version,
        "scanner_version": SCANNER_VERSION,
        "corpus_version": CORPUS_VERSION_TEMPLATE.format(family=spec.name),
        "contract": CONTRACT,
        "rubric_point_ids": list(spec.rubric_point_ids),
        "scored": False,
        "scoring": "reported_not_scored",
        "seed": seed,
        "replication_required": spec.replication_required,
        "applicable": applicable,
        "applicability_reason": resolution.reason,
        "applicability": resolution.as_evidence(),
        "expected_result_contract": spec.expected_result_contract,
        "case_count": len(spec.cases),
        "fired_probe_ledger": fired_probe_ledger,
        "canary_registration": canary_registration,
        "skipped_probe_ledger": skipped_probe_ledger,
        "skipped_canary_registration": skipped_canary_registration,
        "case_results": case_results,
    }
    evidence["sha256"] = hashlib.sha256(_canonical(evidence).encode("utf-8")).hexdigest()
    return evidence


def replicate(
    spec: FamilySpec,
    policy: Policy,
    *,
    architecture_flags: dict[str, bool] | None = None,
) -> list[dict[str, Any]]:
    """Run K=replication_required seeds (0..K-1)."""
    return [
        run_family(spec, policy, seed=s, architecture_flags=architecture_flags)
        for s in range(spec.replication_required)
    ]


def verdict_tally(evidence: dict[str, Any]) -> dict[str, int]:
    tally = {PASS: 0, FAIL: 0, NA: 0}
    for row in evidence["case_results"]:
        tally[row["verdict"]] = tally.get(row["verdict"], 0) + 1
    return tally
