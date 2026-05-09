"""Reproducibility band — BEAAA-130 rec D.

Aggregates N replicate ScoreResponses (re-runs of the same probe set against
the same agent) into a per-dim and ARE-level confidence band. Pure analysis
+ render — operator decides which run is the primary; this module never
mutates it. Closes the customer-CTO gripe that the sensitivity panel covers
*weight*-side uncertainty but not *measurement*-side noise.

Inputs are ScoreResponses, so the math reuses the canonical rubric/scoring
output without re-implementing pillar weights or band cuts.
"""
from __future__ import annotations

import statistics
from html import escape
from typing import Any


def compute_reproducibility(replicates: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate N >= 2 replicate ScoreResponses into a confidence band.

    Returns:
        {
          "n_replicates": int,
          "bmi": {"median": float, "p2_5": float, "p97_5": float, "stdev": float, "min": int, "max": int},
          "dims": {dim_id: {"median": float, "p2_5": float, "p97_5": float, "stdev": float, "min": float, "max": float}},
          "high_noise_dims": [dim_id, ...],   # top 3 by stdev
        }
    """
    if len(replicates) < 2:
        raise ValueError("compute_reproducibility requires at least 2 replicates")

    bmis = [int(r["bmi"]) for r in replicates]
    bmi_stats = _percentile_stats([float(b) for b in bmis])
    bmi_stats["min"] = int(min(bmis))
    bmi_stats["max"] = int(max(bmis))

    dim_ids = []
    first_pillars = replicates[0]["pillars"]
    for pid, pdata in first_pillars.items():
        for did in pdata["dimensions"]:
            dim_ids.append(did)

    dim_stats: dict[str, dict[str, float]] = {}
    for did in dim_ids:
        raws: list[float] = []
        for r in replicates:
            for pid, pdata in r["pillars"].items():
                if did in pdata["dimensions"]:
                    raws.append(float(pdata["dimensions"][did]["raw"]))
                    break
        if raws:
            stats = _percentile_stats(raws)
            stats["min"] = float(min(raws))
            stats["max"] = float(max(raws))
            dim_stats[did] = stats

    high_noise = sorted(
        dim_stats.items(),
        key=lambda kv: kv[1].get("stdev", 0.0),
        reverse=True,
    )
    high_noise_ids = [did for did, _ in high_noise if dim_stats[did].get("stdev", 0.0) > 0][:3]

    return {
        "n_replicates": len(replicates),
        "bmi": bmi_stats,
        "dims": dim_stats,
        "high_noise_dims": high_noise_ids,
    }


def _percentile_stats(values: list[float]) -> dict[str, float]:
    if not values:
        return {"median": 0.0, "p2_5": 0.0, "p97_5": 0.0, "stdev": 0.0}
    sv = sorted(values)
    return {
        "median": float(statistics.median(sv)),
        "p2_5": float(_pct(sv, 2.5)),
        "p97_5": float(_pct(sv, 97.5)),
        "stdev": float(statistics.pstdev(sv)) if len(sv) > 1 else 0.0,
    }


def _pct(sorted_values: list[float], pct: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    k = (len(sorted_values) - 1) * (pct / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(sorted_values) - 1)
    frac = k - lo
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * frac


_REPRO_STYLE = """
.repro-block {
  margin-bottom: 32px; padding: 18px 20px; border: 1px solid #e2e8f0;
  border-radius: 10px; background: #fafbfc;
}
.repro-headline {
  display: flex; align-items: baseline; flex-wrap: wrap; gap: 14px;
  margin-bottom: 10px;
}
.repro-bmi-band {
  font: 600 14px/1 ui-monospace, Menlo, monospace; color: #1a2027;
}
.repro-bmi-band .ci { color: #4a5568; font-weight: 500; font-size: 12px; }
.repro-meta { font: 11px/1.4 ui-monospace, Menlo, monospace; color: #718096; }
.repro-noise-table { width: 100%; border-collapse: collapse; margin-top: 8px; }
.repro-noise-table th {
  text-align: left; padding: 5px 10px; font-size: 11px; font-weight: 600;
  text-transform: uppercase; letter-spacing: 0.04em; color: #718096;
  border-bottom: 1px solid #e2e8f0;
}
.repro-noise-table td {
  padding: 5px 10px; border-bottom: 1px solid #f0f4f8;
  font: 12px/1.4 ui-monospace, Menlo, monospace; color: #1a2027;
}
.repro-noise-table tr:last-child td { border-bottom: none; }
.repro-stable { color: #2e7d32; font-weight: 600; }
.repro-flag   { color: #c67c00; font-weight: 600; }
.repro-note { margin-top: 10px; font-size: 11px; color: #718096; line-height: 1.5; }
"""


def render_reproducibility_block(reproducibility: dict[str, Any]) -> str:
    """Render the reproducibility block as embedded HTML."""
    n = int(reproducibility["n_replicates"])
    bmi = reproducibility["bmi"]
    high_noise = reproducibility.get("high_noise_dims") or []
    dims = reproducibility.get("dims") or {}

    bmi_min = int(bmi["min"])
    bmi_max = int(bmi["max"])
    bmi_median = float(bmi["median"])
    bmi_stdev = float(bmi["stdev"])
    half_width = (bmi_max - bmi_min) / 2.0

    if bmi_min == bmi_max:
        verdict_class = "repro-stable"
        verdict_text = (
            f"ARE is identical across all {n} replicates "
            f"\u2014 measurement noise is below 1 ARE point."
        )
    elif bmi_max - bmi_min <= 2:
        verdict_class = "repro-stable"
        verdict_text = (
            f"ARE varies by {bmi_max - bmi_min} point(s) across {n} replicates "
            f"\u2014 within the \u00b11 reproducibility band."
        )
    else:
        verdict_class = "repro-flag"
        verdict_text = (
            f"ARE varies by {bmi_max - bmi_min} point(s) across {n} replicates "
            f"\u2014 measurement noise exceeds the \u00b11 reproducibility band."
        )

    rows = []
    for did in high_noise:
        s = dims.get(did, {})
        rows.append(
            f"        <tr>\n"
            f"          <td>{escape(did)}</td>\n"
            f'          <td>{s.get("median", 0):.2f}</td>\n'
            f'          <td>{s.get("min", 0):.2f} \u2013 {s.get("max", 0):.2f}</td>\n'
            f'          <td>{s.get("stdev", 0):.3f}</td>\n'
            f"        </tr>\n"
        )
    table_html = ""
    if rows:
        table_html = (
            f'  <table class="repro-noise-table">\n'
            f"    <thead><tr>"
            f"<th>Highest-noise dim</th><th>Median</th><th>Min \u2013 Max</th>"
            f"<th>StDev</th>"
            f"</tr></thead>\n"
            f'    <tbody>\n{"".join(rows)}    </tbody>\n'
            f"  </table>\n"
        )

    return (
        f"<style>{_REPRO_STYLE}</style>\n"
        f'<div class="repro-block">\n'
        f'  <div class="section-title">Reproducibility (n={n})</div>\n'
        f'  <div class="repro-headline">\n'
        f'    <span class="repro-bmi-band">ARE {bmi_min}\u2013{bmi_max} '
        f'<span class="ci">(median {bmi_median:.0f} \u00b1 {half_width:.1f}, '
        f"\u03c3={bmi_stdev:.2f})</span></span>\n"
        f'    <span class="{verdict_class}">{escape(verdict_text)}</span>\n'
        f"  </div>\n"
        f"{table_html}"
        f'  <div class="repro-note">Replicates were re-runs of the same probe set '
        f"against the same agent under the same rubric. Weight-side uncertainty is "
        f'shown in the Sensitivity panel; this is the measurement-side companion.</div>\n'
        f"</div>\n"
    )
