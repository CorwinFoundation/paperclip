"""P1 emit helper — wraps the P1.6 Path B eval-coverage-breadth aggregator
behind a single entry point that scanner adapters call to add the P1.6
dim cell to their `eval_outputs` before calling `rubric.scoring.score`.

BEAAA-209 Phase 2. Mirrors `harness/p2_emit.py` for symmetry across the
two meta-aggregators in v0.1.

The aggregator itself lives in `harness/bench/eval_coverage_aggregator.py`
(zero LLM calls, pure function of input cells + loaded rubric).
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from harness.bench import eval_coverage_aggregator as cov
from rubric.scoring import Rubric


def populate_p1_6_cell(
    eval_outputs: Mapping[str, Mapping[str, Any]],
    *,
    rubric: Rubric,
    evidence_uri: str | None = None,
    explicit_dim_status: Mapping[str, str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Return a new eval_outputs dict with a Path B P1.6 cell added.

    The aggregator inspects every in-scope dim's cell in the supplied
    `eval_outputs` (which MUST NOT already contain P1.6 — if it does, it
    is dropped before classification to avoid circular dependence) and
    classifies it as measured / inconclusive / decline-to-attest /
    missing. The weighted coverage drives the P1.6 raw via the locked
    cuts in the aggregator, and the resulting cell carries the three
    v0.2.4 additive fields (coverage_breadth_pct / covered_dim_ids /
    evidence_path="B") that `rubric.scoring.score` propagates onto the
    ScoreResponse P1.6 dim block.

    Caller passes `explicit_dim_status` when probe-runners have already
    classified specific cells (e.g. P4 attestation runner emits an
    explicit "decline-to-attest" instead of asking the classifier to
    infer it). Heuristic classification handles the rest.

    Returns a NEW dict — does not mutate the input. The caller should
    swap this for the original `eval_outputs` before computing
    snapshot_hash and calling `score`.
    """
    # Drop any pre-existing P1.6 entry from the classifier input — Path B
    # is a meta-dim and would otherwise be classified against itself.
    classifier_input = {
        d: e for d, e in eval_outputs.items() if d != cov.THIS_DIM_ID
    }

    result = cov.run(
        rubric=rubric,
        eval_outputs=classifier_input,
        dim_status=explicit_dim_status,
    )

    p1_6_cell: dict[str, Any] = {
        "raw": result["dim_raw"],
        "evidence_uri": evidence_uri or "",
        "coverage_breadth_pct": result["coverage_breadth_pct"],
        "covered_dim_ids": list(result["covered_dim_ids"]),
        "evidence_path": result["evidence_path"],
    }

    out: dict[str, dict[str, Any]] = {
        d: dict(e) for d, e in eval_outputs.items()
    }
    out[cov.THIS_DIM_ID] = p1_6_cell
    return out
