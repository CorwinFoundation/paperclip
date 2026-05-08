"""Scan-vs-prior-scan diff — BEAAA-130 rec E.

Given the current ScoreResponse and a prior ScoreResponse for the same
agent, compute per-dim and BMI-level deltas. Render a small "What changed
since last scan" block. Pure analysis + render — never mutates either side.

Closes the customer-CTO gripe that quarterly re-scans without diff lose
the trend story.
"""
from __future__ import annotations

from html import escape
from typing import Any


def compute_diff(
    current: dict[str, Any],
    prior: dict[str, Any],
) -> dict[str, Any]:
    """Compute the diff between a current and prior ScoreResponse.

    Returns:
        {
          "bmi_delta": int,
          "band_letter_changed": bool,
          "band_modifier_changed": bool,
          "current_band_display": str,
          "prior_band_display": str,
          "prior_scored_at": str,
          "dim_deltas": {dim_id: {"current_raw": float, "prior_raw": float, "delta": float}},
          "movers": [(dim_id, delta), ...],   # top 5 by abs delta
        }
    """
    cur_bmi = int(current["bmi"])
    pri_bmi = int(prior["bmi"])
    cur_band = current["band"]
    pri_band = prior["band"]

    dim_deltas: dict[str, dict[str, float]] = {}
    for pid, pdata in current.get("pillars", {}).items():
        for did, entry in pdata.get("dimensions", {}).items():
            cur_raw = float(entry.get("raw", 0))
            pri_raw = _find_prior_raw(prior, did)
            if pri_raw is None:
                continue
            dim_deltas[did] = {
                "current_raw": cur_raw,
                "prior_raw": pri_raw,
                "delta": cur_raw - pri_raw,
            }

    movers = sorted(
        ((did, d["delta"]) for did, d in dim_deltas.items() if abs(d["delta"]) > 0),
        key=lambda kv: abs(kv[1]),
        reverse=True,
    )[:5]

    return {
        "bmi_delta": cur_bmi - pri_bmi,
        "band_letter_changed": cur_band["letter"] != pri_band["letter"],
        "band_modifier_changed": cur_band["modifier"] != pri_band["modifier"],
        "current_band_display": cur_band["display"],
        "prior_band_display": pri_band["display"],
        "prior_scored_at": str(prior.get("scored_at", "")),
        "dim_deltas": dim_deltas,
        "movers": movers,
    }


def _find_prior_raw(prior: dict[str, Any], dim_id: str) -> float | None:
    for pid, pdata in prior.get("pillars", {}).items():
        dims = pdata.get("dimensions", {})
        if dim_id in dims:
            return float(dims[dim_id].get("raw", 0))
    return None


_DIFF_STYLE = """
.diff-block {
  margin-bottom: 32px; padding: 18px 20px; border: 1px solid #e2e8f0;
  border-radius: 10px; background: #fafbfc;
}
.diff-headline {
  display: flex; align-items: baseline; flex-wrap: wrap; gap: 14px;
  margin-bottom: 10px;
}
.diff-bmi {
  font: 700 14px/1 ui-monospace, Menlo, monospace;
}
.diff-pos { color: #2e7d32; }
.diff-neg { color: #b71c1c; }
.diff-flat { color: #4a5568; }
.diff-meta { font: 11px/1.4 ui-monospace, Menlo, monospace; color: #718096; }
.diff-band-shift {
  display: inline-block; font: 700 10px/1 ui-monospace, monospace;
  background: #fff7ed; color: #9a3412; border: 1px solid #fdba74;
  padding: 2px 6px; border-radius: 3px;
}
.diff-table { width: 100%; border-collapse: collapse; margin-top: 8px; }
.diff-table th {
  text-align: left; padding: 5px 10px; font-size: 11px; font-weight: 600;
  text-transform: uppercase; letter-spacing: 0.04em; color: #718096;
  border-bottom: 1px solid #e2e8f0;
}
.diff-table td {
  padding: 5px 10px; border-bottom: 1px solid #f0f4f8;
  font: 12px/1.4 ui-monospace, Menlo, monospace; color: #1a2027;
}
.diff-table tr:last-child td { border-bottom: none; }
.diff-table .delta-pos { color: #2e7d32; font-weight: 700; }
.diff-table .delta-neg { color: #b71c1c; font-weight: 700; }
.diff-note { margin-top: 10px; font-size: 11px; color: #718096; line-height: 1.5; }
.dim-delta-pill {
  display: inline-block; font: 700 10px/1 ui-monospace, Menlo, monospace;
  margin-left: 6px; padding: 2px 5px; border-radius: 3px; vertical-align: middle;
}
.dim-delta-pos {
  background: #f0faf4; color: #1b5e20; border: 1px solid #4caf50;
}
.dim-delta-neg {
  background: #fff5f5; color: #b71c1c; border: 1px solid #ef5350;
}
"""


def render_diff_block(diff: dict[str, Any]) -> str:
    """Render the prior-scan diff block as embedded HTML."""
    bmi_delta = int(diff["bmi_delta"])
    movers = diff.get("movers") or []
    cur_band = diff["current_band_display"]
    pri_band = diff["prior_band_display"]
    prior_scored_at = escape(diff.get("prior_scored_at", "")[:10] or "—")

    if bmi_delta > 0:
        delta_str = f'<span class="diff-pos">+{bmi_delta}</span>'
    elif bmi_delta < 0:
        delta_str = f'<span class="diff-neg">{bmi_delta}</span>'
    else:
        delta_str = '<span class="diff-flat">0</span>'

    band_shift_html = ""
    if diff.get("band_letter_changed") or diff.get("band_modifier_changed"):
        band_shift_html = (
            f' <span class="diff-band-shift">band: {escape(pri_band)} '
            f'\u2192 {escape(cur_band)}</span>'
        )

    rows = []
    for did, delta in movers:
        d = diff["dim_deltas"][did]
        delta_class = "delta-pos" if delta > 0 else "delta-neg"
        sign = "+" if delta > 0 else ""
        rows.append(
            f"        <tr>\n"
            f"          <td>{escape(did)}</td>\n"
            f'          <td>{d["prior_raw"]:.2f}</td>\n'
            f'          <td>{d["current_raw"]:.2f}</td>\n'
            f'          <td class="{delta_class}">{sign}{delta:.2f}</td>\n'
            f"        </tr>\n"
        )
    table_html = ""
    if rows:
        table_html = (
            f'  <table class="diff-table">\n'
            f"    <thead><tr><th>Dim</th><th>Prior</th><th>Current</th>"
            f"<th>\u0394 Raw</th></tr></thead>\n"
            f'    <tbody>\n{"".join(rows)}    </tbody>\n'
            f"  </table>\n"
        )
    else:
        table_html = (
            '  <div class="diff-note">No per-dim raw scores moved between scans.</div>\n'
        )

    return (
        f"<style>{_DIFF_STYLE}</style>\n"
        f'<div class="diff-block">\n'
        f'  <div class="section-title">Change Since Last Scan</div>\n'
        f'  <div class="diff-headline">\n'
        f'    <span class="diff-bmi">\u0394BMI {delta_str}</span>'
        f"{band_shift_html}\n"
        f'    <span class="diff-meta">prior scan: {prior_scored_at}</span>\n'
        f"  </div>\n"
        f"{table_html}"
        f'  <div class="diff-note">Top movers by absolute \u0394 raw, max 5. '
        f"Same rubric version both sides; if rubric changes between scans the "
        f'diff is suppressed.</div>\n'
        f"</div>\n"
    )


def render_dim_delta_pill(diff: dict[str, Any] | None, dim_id: str) -> str:
    """Inline ▲+0.30 / ▼-0.20 pill rendered next to a dim raw column."""
    if not diff:
        return ""
    d = diff.get("dim_deltas", {}).get(dim_id)
    if not d:
        return ""
    delta = float(d["delta"])
    if abs(delta) < 0.005:
        return ""
    if delta > 0:
        return (
            f'<span class="dim-delta-pill dim-delta-pos">'
            f"\u25b2 +{delta:.2f}</span>"
        )
    return (
        f'<span class="dim-delta-pill dim-delta-neg">'
        f"\u25bc {delta:.2f}</span>"
    )
