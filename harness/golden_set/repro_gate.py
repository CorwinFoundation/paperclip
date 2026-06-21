"""Layer-2 reproducibility gate (BEAAA-163 Option C/B+C combined).

The L1 gate (tests/test_golden_agents.py) catches rubric/scoring engine
regressions on every CI run — deterministic. The L2 gate runs quarterly
against re-scanned replicates of each golden agent and answers a different
question: is the *probe-runner pipeline* stable enough to underwrite?

Tolerances per the golden-set v1 spec (BEAAA-163 attachment 1934b351 §3.3):
  - per-dim raw within +/- 0.05 across N replicates
  - ARE total within +/- 1 across N replicates
  - corpus_version, seed, scanner_version, rubric_version IDENTICAL across
    replicates (a mismatch is a contract violation, not stochastic noise)

A breach surfaces to UW as either (a) probe-runner needs a seed-pinning fix
or (b) the calibration target itself moved and needs UW co-sign on a retag.
The gate does not decide which — it surfaces the breach with enough detail
that UW can adjudicate.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

DEFAULT_DIM_TOLERANCE = 0.05
DEFAULT_BMI_TOLERANCE = 1.0
DEFAULT_PILLAR_TOLERANCE = 0.5


@dataclass
class ReproVerdict:
    """Outcome of running the L2 gate on N replicates of a golden agent."""

    passed: bool
    n_replicates: int
    bmi_min: int
    bmi_max: int
    bmi_breach: bool
    dim_breaches: dict[str, tuple[float, float]] = field(default_factory=dict)
    pillar_breaches: dict[str, tuple[float, float]] = field(default_factory=dict)
    contract_drift: list[str] = field(default_factory=list)
    summary: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "n_replicates": self.n_replicates,
            "bmi": {"min": self.bmi_min, "max": self.bmi_max, "breach": self.bmi_breach},
            "dim_breaches": {k: list(v) for k, v in self.dim_breaches.items()},
            "pillar_breaches": {k: list(v) for k, v in self.pillar_breaches.items()},
            "contract_drift": list(self.contract_drift),
            "summary": self.summary,
        }


def evaluate_replicates(
    replicates: list[dict[str, Any]],
    *,
    dim_tolerance: float = DEFAULT_DIM_TOLERANCE,
    bmi_tolerance: float = DEFAULT_BMI_TOLERANCE,
    pillar_tolerance: float = DEFAULT_PILLAR_TOLERANCE,
) -> ReproVerdict:
    """Run the L2 gate against N >= 2 replicate ScoreResponses.

    Pass iff:
      - bmi_max - bmi_min <= bmi_tolerance
      - max - min for every dim's raw <= dim_tolerance
      - max - min for every pillar's score <= pillar_tolerance
      - corpus_version, seed, scanner_version, rubric_version identical across all replicates

    Any contract-drift field that varies across replicates is a hard fail
    regardless of numeric tolerance — those fields must be byte-identical
    by construction or the harness has a bug.
    """
    if len(replicates) < 2:
        raise ValueError("L2 gate requires at least 2 replicates")

    bmis = [int(r["bmi"]) for r in replicates]
    bmi_min, bmi_max = min(bmis), max(bmis)
    bmi_breach = (bmi_max - bmi_min) > bmi_tolerance

    contract_drift: list[str] = []
    for field_name in ("corpus_version", "seed", "scanner_version", "rubric_version"):
        values = {r.get(field_name) for r in replicates}
        if len(values) > 1:
            contract_drift.append(field_name)

    dim_breaches: dict[str, tuple[float, float]] = {}
    pillar_breaches: dict[str, tuple[float, float]] = {}
    pillar_ids = list(replicates[0].get("pillars", {}).keys())

    for pid in pillar_ids:
        pillar_scores = [float(r["pillars"][pid]["score"]) for r in replicates]
        ps_min, ps_max = min(pillar_scores), max(pillar_scores)
        if (ps_max - ps_min) > pillar_tolerance:
            pillar_breaches[pid] = (ps_min, ps_max)

        first_pillar = replicates[0]["pillars"][pid]
        for did in first_pillar.get("dimensions", {}):
            # BEAAA-7235: N/A dims (Option C — fires=False, raw=null) carry no
            # measurement, so they cannot exhibit reproducibility drift and are
            # excluded from the per-dim variance band. Skip when raw is None in
            # any replicate; float(None) would otherwise raise TypeError once
            # the rubric-1.1.0 TOOL_MISUSE/PII N/A dims enter golden baselines.
            raw_vals = [
                r["pillars"][pid]["dimensions"][did].get("raw")
                for r in replicates
            ]
            if any(rv is None for rv in raw_vals):
                continue
            raws = [float(rv) for rv in raw_vals]
            r_min, r_max = min(raws), max(raws)
            if (r_max - r_min) > dim_tolerance:
                dim_breaches[did] = (r_min, r_max)

    passed = (
        not bmi_breach
        and not dim_breaches
        and not pillar_breaches
        and not contract_drift
    )

    parts: list[str] = []
    parts.append(f"N={len(replicates)}")
    parts.append(f"ARE {bmi_min}-{bmi_max}")
    if contract_drift:
        parts.append(f"contract drift: {','.join(contract_drift)}")
    if bmi_breach:
        parts.append(f"BMI delta {bmi_max - bmi_min} > tol {bmi_tolerance}")
    if dim_breaches:
        parts.append(f"{len(dim_breaches)} dim breach(es)")
    if pillar_breaches:
        parts.append(f"{len(pillar_breaches)} pillar breach(es)")
    if passed:
        parts.append("PASS")
    summary = " | ".join(parts)

    return ReproVerdict(
        passed=passed,
        n_replicates=len(replicates),
        bmi_min=bmi_min,
        bmi_max=bmi_max,
        bmi_breach=bmi_breach,
        dim_breaches=dim_breaches,
        pillar_breaches=pillar_breaches,
        contract_drift=contract_drift,
        summary=summary,
    )
