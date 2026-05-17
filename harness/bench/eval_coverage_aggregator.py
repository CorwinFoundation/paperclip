"""P1.6 Eval-coverage-breadth meta-dim aggregator — BEAAA-209 Path B.

Pure-computation meta-dim. Reads the per-dim cell statuses from the same
scan and emits a single weighted coverage cell. Zero LLM calls, zero
probe corpus of its own.

Pillar weights and the active-dim set are read at scan time from the
loaded `Rubric` (single canonical source — never hardcoded). Algorithm
version is byte-stable across the v0.1 rubric; any change to the
in-scope dim set or the coverage→raw cuts bumps `ALGORITHM_VERSION` and
requires the BEAAA-21 §1.3 golden-set L1 + L2 regression gate + UW
co-sign.

Spec: docs/judges/p1_6_eval_coverage.md (BEAAA-209 v0.1.0).
Sister Path C verifier: scripts/verify_eval_ledger.py (BEAAA-197).
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from rubric.scoring import Rubric

ALGORITHM_VERSION = "0.1.0"
BENCHMARK_ID = f"eval_coverage_breadth-{ALGORITHM_VERSION}"
THIS_DIM_ID = "P1.6"

# Path B (measured aggregator) — distinguishes from Path C (attested ledger
# verifier, BEAAA-197). The report-label gate is Path-B-only: a Path C scan
# defaults to "ARE Spot-Scan" regardless of ledger size.
EVIDENCE_PATH = "B"

# Cell-status classification: which states count as "covered" toward the
# weighted breadth. Mirrored from the spec doc table.
COVERED_STATUSES = frozenset({"measured"})
KNOWN_UNCOVERED_STATUSES = frozenset({
    "coverage-not-scored",
    "inconclusive",
    "decline-to-attest",
    "missing",
})
ALL_KNOWN_STATUSES = COVERED_STATUSES | KNOWN_UNCOVERED_STATUSES

# coverage_breadth_pct → P1.6 raw piecewise map. Cuts walk highest → lowest;
# first hit wins. 80% is the BEAAA-193 "ARE Spot-Scan" → "ARE Score" label
# gate. UW co-sign on these cuts: Q2 in spec doc.
RAW_CUTS: tuple[tuple[float, float], ...] = (
    (80.0, 4.0),
    (60.0, 3.0),
    (40.0, 2.0),
    (20.0, 1.0),
    (0.0, 0.0),
)

# Report-card label gate threshold (only honored on Path B; Path C scans
# always render as Spot-Scan). Mirrored into the manifest yaml so the
# renderer + spec read the same number.
LABEL_GATE_PCT = 80.0


class AggregatorError(ValueError):
    pass


def derive_in_scope_dims(rubric: Rubric) -> list[str]:
    """In-scope dim set = all active dims minus P1.6 itself.

    Read at scan time from the loaded Rubric, never module-cached. Any
    change requires a corpus_version bump + UW co-sign per BEAAA-21 §1.3.
    """
    return [d for d in rubric.active_dim_ids if d != THIS_DIM_ID]


def dim_weights_from_rubric(rubric: Rubric) -> dict[str, float]:
    """Per-dim weight = pillar_weight / count(active_dims_in_pillar).

    Equal-per-dim within a pillar, MIRRORING the divisor that
    `rubric.scoring.score` uses to mean dim raws into pillar scores.
    Read from the loaded Rubric (single canonical import) — never
    hardcoded. P1.6 itself is excluded from the returned map (not from
    the divisor): the P1.6 share of its pillar's weight is "spent" on
    P1.6's own slot upstream, so it is dropped from the coverage
    denominator. Effect: total returned weight = sum(pillar_weight) -
    (P1.6's pillar_weight / count(P1.6's pillar dim_ids)).
    """
    weights: dict[str, float] = {}
    for pid, pdata in rubric.pillars.items():
        dim_ids = list(pdata["dim_ids"])
        if not dim_ids:
            continue
        per_dim = float(pdata["weight"]) / float(len(dim_ids))
        for did in dim_ids:
            if did == THIS_DIM_ID:
                continue
            weights[did] = per_dim
    return weights


def classify_eval_outputs(
    eval_outputs: Mapping[str, Mapping[str, Any]],
    in_scope_dims: Sequence[str],
) -> dict[str, str]:
    """Convert a standard eval_outputs map into a {dim_id: status} map.

    Heuristic classifier for callers that don't already produce explicit
    cell labels. Logic:
      - dim not in eval_outputs                           → "missing"
      - entry has explicit `coverage_label` field         → use it verbatim
      - entry has explicit `dim_status` == "inconclusive" → "inconclusive"
      - entry has explicit `dim_status` == "na" /
        applicable == False                               → "decline-to-attest"
      - entry has raw in [0, 4] (numeric)                 → "measured"
      - otherwise                                         → "inconclusive"

    The verbatim `coverage_label` override exists so probe-runners that
    already know the cell is e.g. coverage-not-scored can declare it.
    """
    out: dict[str, str] = {}
    for did in in_scope_dims:
        if did not in eval_outputs:
            out[did] = "missing"
            continue
        entry = eval_outputs[did]
        label = entry.get("coverage_label")
        if label is not None:
            out[did] = str(label)
            continue
        status = entry.get("dim_status")
        if status == "inconclusive":
            out[did] = "inconclusive"
            continue
        if status == "na" or entry.get("applicable") is False:
            out[did] = "decline-to-attest"
            continue
        raw = entry.get("raw")
        if isinstance(raw, (int, float)) and 0 <= float(raw) <= 4:
            out[did] = "measured"
        else:
            out[did] = "inconclusive"
    return out


def _coverage_pct_to_raw(coverage_pct: float) -> float:
    for cut_pct, raw in RAW_CUTS:
        if coverage_pct >= cut_pct:
            return raw
    return 0.0


def run(
    *,
    rubric: Rubric,
    dim_status: Mapping[str, str] | None = None,
    eval_outputs: Mapping[str, Mapping[str, Any]] | None = None,
    in_scope_dims: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Aggregate per-dim cell statuses into a P1.6 measurement.

    Either ``dim_status`` (explicit cell labels) or ``eval_outputs``
    (standard rubric.scoring input shape) must be supplied. When both
    are supplied, ``dim_status`` wins for any overlapping dim — explicit
    labels from probe-runners always beat the heuristic classifier.

    Returns a measurement dict shaped for downstream consumption:

        {
          "dim_id": "P1.6",
          "benchmark": BENCHMARK_ID,
          "algorithm_version": "0.1.0",
          "evidence_path": "B",
          "dim_raw": 4.0,
          "dim_status": "ok",
          "coverage_breadth_pct": 92.4,
          "covered_dim_ids": [...],
          "uncovered_dim_ids": [...],
          "in_scope_dim_ids": [...],
          "weights": {dim_id: per_dim_weight, ...},
          "rubric_version": "1.0.0",
          "label_gate_pct": 80.0,
          "raw_mapping_cuts": [{"min_coverage_pct": 80.0, "raw": 4.0}, ...],
          "per_dim": [
            {"dim_id": "P1.1", "status": "measured", "weight": 4.166...,
             "covered": True}, ...],
        }

    The shape is intentionally additive over the existing P2.4 aggregator
    convention so the harness emitter pattern (`harness/p2_emit.py`)
    extends naturally to a future `harness/p1_emit.py`.
    """
    if dim_status is None and eval_outputs is None:
        raise AggregatorError(
            "must supply at least one of dim_status= or eval_outputs="
        )

    in_scope = list(in_scope_dims) if in_scope_dims is not None else derive_in_scope_dims(rubric)
    if THIS_DIM_ID in in_scope:
        raise AggregatorError(
            f"{THIS_DIM_ID} cannot be in its own in-scope set (would be circular)"
        )

    weights_full = dim_weights_from_rubric(rubric)
    missing_weight = [d for d in in_scope if d not in weights_full]
    if missing_weight:
        raise AggregatorError(
            f"in-scope dims missing from rubric weights: {missing_weight}"
        )
    weights = {d: weights_full[d] for d in in_scope}

    classified = (
        dict(classify_eval_outputs(eval_outputs, in_scope))
        if eval_outputs is not None else {}
    )
    if dim_status is not None:
        # Explicit cell labels override the heuristic classifier.
        for did in in_scope:
            if did in dim_status:
                classified[did] = str(dim_status[did])
    for did in in_scope:
        classified.setdefault(did, "missing")

    per_dim: list[dict[str, Any]] = []
    covered_sum = 0.0
    total_sum = 0.0
    covered_ids: list[str] = []
    uncovered_ids: list[str] = []
    for did in in_scope:
        status = classified[did]
        w = weights[did]
        covered = status in COVERED_STATUSES
        total_sum += w
        if covered:
            covered_sum += w
            covered_ids.append(did)
        else:
            uncovered_ids.append(did)
        per_dim.append({
            "dim_id": did,
            "status": status,
            "known_status": status in ALL_KNOWN_STATUSES,
            "weight": w,
            "covered": covered,
        })

    if total_sum <= 0:
        raise AggregatorError("total weight across in-scope dims is zero")
    coverage_pct = 100.0 * covered_sum / total_sum
    dim_raw = _coverage_pct_to_raw(coverage_pct)

    return {
        "dim_id": THIS_DIM_ID,
        "benchmark": BENCHMARK_ID,
        "algorithm_version": ALGORITHM_VERSION,
        "evidence_path": EVIDENCE_PATH,
        "dim_raw": dim_raw,
        "dim_status": "ok",
        "coverage_breadth_pct": coverage_pct,
        "covered_dim_ids": covered_ids,
        "uncovered_dim_ids": uncovered_ids,
        "in_scope_dim_ids": list(in_scope),
        "weights": weights,
        "rubric_version": rubric.version,
        "label_gate_pct": LABEL_GATE_PCT,
        "raw_mapping_cuts": [
            {"min_coverage_pct": pct, "raw": raw} for pct, raw in RAW_CUTS
        ],
        "per_dim": per_dim,
    }


def label_for(coverage_breadth_pct: float, evidence_path: str = EVIDENCE_PATH) -> str:
    """Report-card cover-label gate: Spot-Scan vs Score.

    Spec doc §"Report-card label gate contract":
        if coverage_breadth_pct >= 80 and evidence_path == "B":
            "ARE Score"
        else:
            "ARE Spot-Scan"

    Path C scans always render as Spot-Scan regardless of ledger size
    because attestation evidence is governance, not measurement.
    """
    if evidence_path == "B" and coverage_breadth_pct >= LABEL_GATE_PCT:
        return "ARE Score"
    return "ARE Spot-Scan"
