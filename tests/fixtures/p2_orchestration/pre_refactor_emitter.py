"""P2 Bias & Fairness measurement emitter — BEAAA-199 Phase 2.

Wraps the five P2 dim runners + the P2.4 aggregator behind a single entry
point that scanner adapters call instead of orchestrating the runners
themselves. Produces a measurements list ready for
`harness.pipeline.merge_measurements_with_priors`, plus a `details` dict
for artifact persistence.

Applicability gating: P2.2 only fires when `has_uw_signed_labels=True` per
`rubric/applicability_policy.yaml`. When it does not fire, the emitter
skips the EO run entirely and the N/A justification carries through in
`details["P2.2"]` for the BEAAA-198 Phase 3 Option C scoring path.

Dim runners:
  - P2.1 / P2.2 / P2.5  → harness.bench.p2_paired_attr.run(mode=…)
  - P2.3                → harness.bench.counterfactual_pairs.run(…)
  - P2.4                → harness.bench.p2_coverage.run(…)  (no LLM spend)
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from harness import applicability
from harness.bench import counterfactual_pairs as cfp
from harness.bench import p2_coverage as cov
from harness.bench import p2_paired_attr as pa
from harness.runner import ModelRunner

_REPO = Path(__file__).resolve().parent.parent
DEFAULT_POLICY_PATH = _REPO / "rubric" / "applicability_policy.yaml"
DEFAULT_CORPUS_YAML = _REPO / "rubric" / "probe_mappings" / "counterfactual_stability.yaml"
DEFAULT_EO_YAML = _REPO / "rubric" / "probe_mappings" / "equal_opportunity.yaml"
DEFAULT_EVAL_REPR_YAML = _REPO / "rubric" / "probe_mappings" / "eval_representation.yaml"

P2_DIMS = ("P2.1", "P2.2", "P2.3", "P2.4", "P2.5")


def _slim(result: dict[str, Any]) -> dict[str, Any]:
    """Strip the verbose per-pair trace; keep a 3-pair sample for artifacts."""
    out = {k: v for k, v in result.items() if k != "trace"}
    if "trace" in result:
        out["trace_sample"] = result["trace"][:3]
    return out


def _emit_measurement(dim_id: str, result: dict[str, Any]) -> dict[str, Any] | None:
    """Convert a dim runner result into a measurement entry, or None if the
    dim is inconclusive (the prior carries through in that case)."""
    if result.get("dim_status") != "ok":
        return None
    raw = result.get("dim_raw")
    if raw is None:
        return None
    return {
        "dim_id": dim_id,
        "raw": float(raw),
        "benchmark": result.get("benchmark") or f"{dim_id}-unknown",
    }


def emit_p2_measurements(
    subject: ModelRunner,
    *,
    parity_judge: ModelRunner,
    eo_judge: ModelRunner,
    cfp_judge: ModelRunner,
    sent_judge: ModelRunner,
    architecture_flags: dict[str, bool],
    customer_attestation: dict[str, Any] | None = None,
    n_pairs: int | None = None,
    ensemble_n: int = 3,
    corpus_yaml: str | Path = DEFAULT_CORPUS_YAML,
    eo_yaml: str | Path = DEFAULT_EO_YAML,
    eval_repr_yaml: str | Path = DEFAULT_EVAL_REPR_YAML,
    policy_path: str | Path = DEFAULT_POLICY_PATH,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Run all applicable P2 dims and emit measurements + details.

    ``architecture_flags`` is the adapter's full architecture_flags dict
    (see scripts/scan_lawconnect_atticus.py ARCHITECTURE_FLAGS for the
    canonical shape). The emitter delegates P2 applicability gating to
    ``harness.applicability.decide``; the rest of the flags pass through
    unchanged so the adapter contract is preserved end-to-end.

    Returns ``(measurements, details)``:
      - ``measurements`` is a list of ``{dim_id, raw, benchmark}`` dicts ready
        for ``harness.pipeline.merge_measurements_with_priors``. Inconclusive
        and N/A dims are omitted (the prior / N/A path carries them).
      - ``details`` is a per-dim dict with the slim runner result + an
        ``_applicability_decisions`` block keyed by dim_id, suitable for
        passing into the scorer via the BEAAA-198 Phase 3 Option C path.
    """
    policy = applicability.load_policy(policy_path)
    decisions = applicability.decide(policy, architecture_flags)
    p2_decisions = {did: d for did, d in decisions.items() if did in P2_DIMS}

    pairs = pa.load_pairs(corpus_yaml)
    if n_pairs is not None:
        pairs = pairs[:n_pairs]
    labeled = pa.load_eo_labeled_subset(pairs, eo_yaml)

    details: dict[str, Any] = {}
    measurements: list[dict[str, Any]] = []

    # P2.1 — demographic parity (always fires per v0.1 policy).
    p2_1 = pa.run("parity", subject, parity_judge, pairs, ensemble_n=ensemble_n)
    details["P2.1"] = _slim(p2_1)
    m = _emit_measurement("P2.1", p2_1)
    if m is not None:
        measurements.append(m)

    # P2.2 — equal opportunity (conditional on has_uw_signed_labels=True).
    if p2_decisions["P2.2"].fires:
        if not labeled:
            details["P2.2"] = {
                "dim_status": "inconclusive",
                "reason": (
                    "has_uw_signed_labels=True but no labeled pairs returned "
                    "by load_eo_labeled_subset; check rubric/probe_mappings/"
                    "equal_opportunity.yaml scenario_ids coverage"
                ),
            }
        else:
            p2_2 = pa.run(
                "equal_opportunity", subject, eo_judge, labeled,
                ensemble_n=ensemble_n,
            )
            details["P2.2"] = _slim(p2_2)
            m = _emit_measurement("P2.2", p2_2)
            if m is not None:
                measurements.append(m)
    else:
        details["P2.2"] = {
            "dim_status": "na",
            "na_justification_key": p2_decisions["P2.2"].na_justification_key,
            "na_justification": p2_decisions["P2.2"].na_justification,
        }

    # P2.3 — counterfactual stability (always fires).
    p2_3 = cfp.run(subject, cfp_judge, corpus_yaml, ensemble_n=ensemble_n)
    details["P2.3"] = _slim(p2_3)
    m = _emit_measurement("P2.3", p2_3)
    if m is not None:
        measurements.append(m)

    # P2.4 — representation coverage (no LLM spend; pure aggregation).
    axes_ran = sorted({p.axis for p in pairs})
    p2_4 = cov.run(
        corpus_yaml_path=corpus_yaml,
        eval_repr_yaml_path=eval_repr_yaml,
        scanner_axes_actually_ran=tuple(axes_ran),
        customer_attestation=customer_attestation,
    )
    details["P2.4"] = p2_4
    m = _emit_measurement("P2.4", p2_4)
    if m is not None:
        measurements.append(m)

    # P2.5 — sentiment skew (always fires).
    p2_5 = pa.run("sentiment", subject, sent_judge, pairs, ensemble_n=ensemble_n)
    details["P2.5"] = _slim(p2_5)
    m = _emit_measurement("P2.5", p2_5)
    if m is not None:
        measurements.append(m)

    details["_applicability_decisions"] = {
        did: {
            "fires": d.fires,
            "na_justification_key": d.na_justification_key,
            "na_justification": d.na_justification,
        }
        for did, d in p2_decisions.items()
    }

    return measurements, details
