"""P2.4 Representation-in-eval-set coverage aggregator — BEAAA-203 Tier A.

Meta-dimension: scores the eval-set provenance itself rather than running
new probes against the agent. Pure computation — no LLM calls, no
ModelRunner. Reads:

  (1) Scanner-side: per-axis pair counts from the canonical 60-pair
      counterfactual corpus (counterfactual_stability.yaml#seed_pairs),
      counted only on axes the scan actually ran.
  (2) Customer-attested: optional eval_set_inventory dict from the
      customer attestation (BEAAA-200 P4 evidence-tier scale).

For each protected axis:
  - per_axis_count = scanner_count + customer_count
  - per_axis_coverage = piecewise on (min, good) thresholds:
      below min:      0.0
      [min, good):    linear ramp 0.0 → 1.0
      >= good:        1.0

Overall coverage = min(per_axis_coverage) (weakest-link). Raw maps via the
eval_representation.yaml `raw_mapping.cuts` curve.

UW co-sign on thresholds: BEAAA-448 cmt 1d73afe6 2026-05-17 (Yael); all six
axes signed (name_ethnicity min revised 20→10).

Reproducibility (BEAAA-108): deterministic given pinned thresholds and
inventory inputs.
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml


# v0.1.1 canonical axes (six total; locale_zip is documented but dropped
# from the v0.1.1 corpus per counterfactual_stability.yaml#demographic_axes).
CANONICAL_AXES = (
    "name_ethnicity",
    "locale_zip",
    "dialect_register",
    "pronoun",
    "age_signal",
    "religious_marker",
)


def load_scanner_inventory(corpus_yaml_path: str | Path) -> dict[str, int]:
    """Count per-axis pairs in the canonical counterfactual corpus.

    Returns a dict keyed by axis with the pair count. Axes not present in
    the corpus (e.g. locale_zip in v0.1.1) carry count 0.
    """
    data = yaml.safe_load(Path(corpus_yaml_path).read_text())
    counts: dict[str, int] = defaultdict(int)
    for p in data.get("seed_pairs", []):
        axis = p.get("axis", "")
        if axis.startswith("TBD"):
            continue
        if not p.get("pair_a") or not p.get("pair_b"):
            continue
        counts[axis] += 1
    out: dict[str, int] = {}
    for axis in CANONICAL_AXES:
        out[axis] = int(counts.get(axis, 0))
    return out


def load_thresholds(eval_repr_yaml_path: str | Path) -> dict[str, dict[str, int]]:
    """Load UW-signed per-axis (min, good) thresholds from eval_representation.yaml."""
    data = yaml.safe_load(Path(eval_repr_yaml_path).read_text())
    out: dict[str, dict[str, int]] = {}
    for axis, row in (data.get("coverage_thresholds") or {}).items():
        out[axis] = {"min": int(row["min"]), "good": int(row["good"])}
    return out


def load_raw_cuts(eval_repr_yaml_path: str | Path) -> list[dict[str, float]]:
    data = yaml.safe_load(Path(eval_repr_yaml_path).read_text())
    cuts = (data.get("raw_mapping") or {}).get("cuts") or []
    return [{"min_coverage": float(c["min_coverage"]), "raw": float(c["raw"])} for c in cuts]


def _axis_coverage(count: int, thresholds: dict[str, int]) -> float:
    """Piecewise per-axis coverage in [0.0, 1.0]."""
    lo = thresholds["min"]
    hi = thresholds["good"]
    if count < lo:
        return 0.0
    if count >= hi:
        return 1.0
    span = max(1, hi - lo)
    return (count - lo) / span


def _coverage_to_raw(min_coverage: float, cuts: list[dict[str, float]]) -> float:
    # cuts are listed highest min_coverage → lowest with corresponding raw.
    for c in cuts:
        if min_coverage >= c["min_coverage"]:
            return float(c["raw"])
    return 0.0


def run(
    *,
    corpus_yaml_path: str | Path,
    eval_repr_yaml_path: str | Path,
    scanner_axes_actually_ran: tuple[str, ...] | list[str] | None = None,
    customer_attestation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compute P2.4 representation coverage for a scan.

    `scanner_axes_actually_ran` filters the scanner-side inventory to the
    axes the scan actually exercised (P2.1/P2.2/P2.3/P2.5). Defaults to
    all axes present in the corpus.

    `customer_attestation` shape (per eval_representation.yaml):
        {
          "eval_set_inventory": {<axis>: <int count>, ...},
          "evidence_artifact_pointer": "...",
          "evidence_tier": <int 0-4>,
        }
    """
    scanner_counts = load_scanner_inventory(corpus_yaml_path)
    thresholds = load_thresholds(eval_repr_yaml_path)
    cuts = load_raw_cuts(eval_repr_yaml_path)

    if scanner_axes_actually_ran is not None:
        allow = set(scanner_axes_actually_ran)
        scanner_counts = {
            axis: (cnt if axis in allow else 0)
            for axis, cnt in scanner_counts.items()
        }

    customer_counts: dict[str, int] = {}
    customer_tier: int = 0
    customer_pointer: str | None = None
    if customer_attestation is not None:
        inv = customer_attestation.get("eval_set_inventory") or {}
        customer_counts = {
            axis: int(inv.get(axis, 0)) for axis in CANONICAL_AXES
        }
        try:
            customer_tier = int(customer_attestation.get("evidence_tier") or 0)
        except (TypeError, ValueError):
            customer_tier = 0
        customer_pointer = customer_attestation.get("evidence_artifact_pointer")

    axes_report: list[dict[str, Any]] = []
    for axis in CANONICAL_AXES:
        s = scanner_counts.get(axis, 0)
        c = customer_counts.get(axis, 0)
        total = s + c
        thr = thresholds.get(axis)
        if thr is None:
            # No threshold configured → cannot score this axis; skip it but
            # surface the fact in the report. Should not happen in v0.1.1.
            axes_report.append({
                "axis": axis, "scanner_count": s, "customer_count": c,
                "total_count": total, "coverage": None,
                "status": "no_threshold",
            })
            continue
        cov = _axis_coverage(total, thr)
        axes_report.append({
            "axis": axis, "scanner_count": s, "customer_count": c,
            "total_count": total, "threshold_min": thr["min"],
            "threshold_good": thr["good"], "coverage": cov, "status": "ok",
        })

    scorable = [r for r in axes_report if r.get("coverage") is not None]
    if not scorable:
        return {
            "dim_id": "P2.4",
            "benchmark": "eval_representation-v0.1.1",
            "headline": {
                "min_coverage": None,
                "raw": None,
                "status": "inconclusive",
                "reason": "no axis had a configured threshold",
            },
            "dim_raw": None,
            "dim_status": "inconclusive",
            "axes": axes_report,
            "customer_evidence_tier": customer_tier,
            "customer_evidence_pointer": customer_pointer,
            "calibration_pinned_to": "rubric/probe_mappings/eval_representation.yaml#raw_mapping",
        }
    min_cov = min(r["coverage"] for r in scorable)
    raw = _coverage_to_raw(min_cov, cuts)
    # Identify weakest-link axis for evidence trail.
    weakest = min(scorable, key=lambda r: r["coverage"])["axis"]
    return {
        "dim_id": "P2.4",
        "benchmark": "eval_representation-v0.1.1",
        "headline": {
            "min_coverage": min_cov,
            "weakest_axis": weakest,
            "raw": raw,
            "status": "ok",
        },
        "dim_raw": raw,
        "dim_status": "ok",
        "axes": axes_report,
        "customer_evidence_tier": customer_tier,
        "customer_evidence_pointer": customer_pointer,
        "calibration_pinned_to": "rubric/probe_mappings/eval_representation.yaml#raw_mapping",
    }
