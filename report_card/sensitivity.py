"""Weight-perturbation sensitivity panel — BEAAA-130 rec #3.

Pure render-side explainability: perturb each pillar weight by ±10%, rescale
the other pillars proportionally so the total still sums to 100, recompute
BMI under those alt weights, and surface (a) how many perturbations move BMI
outside the ±1 reproducibility band, (b) how many cause a band-letter shift,
(c) the worst-case ΔBMI and band shift.

No scoring-engine changes. The original `pillars[pid].score` and `bmi` are
untouched. The block answers a carrier-grade reader's question — "would a
different weighting flip this decision?" — without re-probing.

Reproducibility note: deterministic. Same ScoreResponse → same panel.
"""
from __future__ import annotations

from html import escape
from typing import Any

from rubric.bands import assign_band, band_to_dict

PERTURBATION_PCT = 10
REPRODUCIBILITY_BAND = 1


def compute_sensitivity(score_response: dict[str, Any]) -> dict[str, Any]:
    """Run the ±10% per-pillar weight perturbations against a ScoreResponse.

    Returns a dict with `perturbations` (one entry per pillar × {±10%}) and a
    `summary` block aggregated across all of them.
    """
    pillars = score_response["pillars"]
    base_bmi = int(score_response["bmi"])
    base_band = score_response["band"]
    base_letter = base_band["letter"]
    base_modifier = base_band["modifier"]

    normalized: dict[str, float] = {}
    base_weights: dict[str, int] = {}
    for pid, pdata in pillars.items():
        weight = int(pdata["weight"])
        score_val = float(pdata["score"])
        base_weights[pid] = weight
        normalized[pid] = (score_val / weight) if weight else 0.0

    pillar_ids = list(pillars.keys())
    perturbations: list[dict[str, Any]] = []
    for pid in pillar_ids:
        for sign in (+1, -1):
            entry = _perturb_one(
                pid, sign, base_weights, normalized, base_bmi, base_letter, base_modifier
            )
            perturbations.append(entry)

    n_outside = sum(1 for p in perturbations if abs(p["bmi_delta"]) > REPRODUCIBILITY_BAND)
    n_letter = sum(1 for p in perturbations if p["letter_shift"])
    n_modifier_only = sum(
        1 for p in perturbations if p["modifier_shift"] and not p["letter_shift"]
    )

    worst = max(perturbations, key=lambda p: abs(p["bmi_delta"]))
    worst_band_shift = (
        f"{base_band['display']} \u2192 {worst['alt_band']['display']}"
        if worst["letter_shift"] or worst["modifier_shift"]
        else f"{base_band['display']} (stable)"
    )

    return {
        "base_bmi": base_bmi,
        "base_band_display": base_band["display"],
        "perturbation_pct": PERTURBATION_PCT,
        "reproducibility_band": REPRODUCIBILITY_BAND,
        "perturbations": perturbations,
        "summary": {
            "n_total": len(perturbations),
            "n_outside_reproducibility_band": n_outside,
            "n_letter_shift": n_letter,
            "n_modifier_only_shift": n_modifier_only,
            "worst_bmi_delta": worst["bmi_delta"],
            "worst_band_shift": worst_band_shift,
        },
    }


def _perturb_one(
    target_pid: str,
    sign: int,
    base_weights: dict[str, int],
    normalized: dict[str, float],
    base_bmi: int,
    base_letter: str,
    base_modifier: str,
) -> dict[str, Any]:
    base_w = base_weights[target_pid]
    delta = base_w * (PERTURBATION_PCT / 100.0) * sign
    new_target = base_w + delta
    other_total_old = 100 - base_w
    other_total_new = 100 - new_target
    scale = (other_total_new / other_total_old) if other_total_old else 0.0

    alt_weights: dict[str, float] = {}
    for pid, w in base_weights.items():
        alt_weights[pid] = new_target if pid == target_pid else w * scale

    alt_bmi_float = sum(alt_weights[pid] * normalized[pid] for pid in base_weights)
    alt_bmi = int(round(alt_bmi_float))
    alt_bmi = max(0, min(100, alt_bmi))
    alt_band = assign_band(alt_bmi)
    alt_band_dict = band_to_dict(alt_band)
    bmi_delta = alt_bmi - base_bmi

    letter_shift = alt_band.letter != base_letter
    modifier_shift = alt_band.modifier != base_modifier

    return {
        "pillar": target_pid,
        "delta_pct": PERTURBATION_PCT * sign,
        "alt_weights": {pid: round(w, 2) for pid, w in alt_weights.items()},
        "alt_bmi": alt_bmi,
        "alt_band": alt_band_dict,
        "bmi_delta": bmi_delta,
        "letter_shift": letter_shift,
        "modifier_shift": modifier_shift,
    }


_SENSITIVITY_STYLE = """
.sens-block {
  margin-bottom: 32px; padding: 18px 20px; border: 1px solid #e2e8f0;
  border-radius: 10px; background: #fafbfc;
}
.sens-headline {
  display: flex; align-items: baseline; flex-wrap: wrap; gap: 12px;
  margin-bottom: 12px;
}
.sens-verdict {
  font-size: 13px; font-weight: 700; color: #1a2027;
}
.sens-verdict.stable { color: #2e7d32; }
.sens-verdict.churn { color: #c67c00; }
.sens-verdict.flip { color: #b71c1c; }
.sens-meta {
  font: 11px/1.4 ui-monospace, Menlo, monospace; color: #718096;
}
.sens-table { width: 100%; border-collapse: collapse; margin-top: 6px; }
.sens-table th {
  text-align: left; padding: 6px 10px; font-size: 11px; font-weight: 600;
  text-transform: uppercase; letter-spacing: 0.05em; color: #718096;
  border-bottom: 1px solid #e2e8f0;
}
.sens-table td {
  padding: 6px 10px; border-bottom: 1px solid #f0f4f8;
  font: 12px/1.4 ui-monospace, Menlo, monospace; color: #1a2027;
}
.sens-table tr:last-child td { border-bottom: none; }
.sens-table .delta-pos { color: #2e7d32; }
.sens-table .delta-neg { color: #b71c1c; }
.sens-table .shift-letter {
  background: #fff0f0; color: #b71c1c; font-weight: 700;
  padding: 1px 5px; border-radius: 3px; margin-left: 5px;
}
.sens-table .shift-modifier {
  background: #fff7ed; color: #c2410c; font-weight: 700;
  padding: 1px 5px; border-radius: 3px; margin-left: 5px;
}
.sens-note { margin-top: 10px; font-size: 11px; color: #718096; line-height: 1.5; }
"""


def render_sensitivity_block(score_response: dict[str, Any]) -> str:
    """Render the sensitivity panel as embedded HTML.

    Self-contained: returns a `<style>` block plus the markup. Caller embeds
    the result inside the report body.
    """
    sens = compute_sensitivity(score_response)
    summary = sens["summary"]
    n_letter = summary["n_letter_shift"]
    n_outside = summary["n_outside_reproducibility_band"]

    if n_letter > 0:
        verdict_class = "flip"
        verdict_text = (
            f"{n_letter} of {summary['n_total']} weight perturbation(s) "
            f"flip the band-letter \u2014 decision is sensitive to weighting."
        )
    elif n_outside > 0:
        verdict_class = "churn"
        verdict_text = (
            f"{n_outside} of {summary['n_total']} weight perturbation(s) move "
            f"BMI by more than \u00b11 (reproducibility band) but no letter flips."
        )
    else:
        verdict_class = "stable"
        verdict_text = (
            f"All {summary['n_total']} weight perturbations land within \u00b11 BMI "
            f"of the reported score \u2014 decision is stable to weighting."
        )

    rows = []
    for p in sens["perturbations"]:
        delta = p["bmi_delta"]
        delta_class = "delta-pos" if delta > 0 else ("delta-neg" if delta < 0 else "")
        sign = "+" if p["delta_pct"] > 0 else ""
        shift_badge = ""
        if p["letter_shift"]:
            shift_badge = (
                f' <span class="shift-letter">letter \u2192 '
                f'{escape(p["alt_band"]["display"])}</span>'
            )
        elif p["modifier_shift"]:
            shift_badge = (
                f' <span class="shift-modifier">modifier \u2192 '
                f'{escape(p["alt_band"]["display"])}</span>'
            )
        rows.append(
            f"        <tr>\n"
            f'          <td>{escape(p["pillar"])} {sign}{p["delta_pct"]}%</td>\n'
            f'          <td>{p["alt_bmi"]}/100</td>\n'
            f'          <td class="{delta_class}">{delta:+d}</td>\n'
            f"          <td>{escape(p['alt_band']['display'])}{shift_badge}</td>\n"
            f"        </tr>\n"
        )

    pct = sens["perturbation_pct"]
    return (
        f"<style>{_SENSITIVITY_STYLE}</style>\n"
        f'<div class="sens-block">\n'
        f'  <div class="section-title">Weight Sensitivity (\u00b1{pct}%)</div>\n'
        f'  <div class="sens-headline">\n'
        f'    <span class="sens-verdict {verdict_class}">{escape(verdict_text)}</span>\n'
        f'    <span class="sens-meta">worst \u0394BMI = {summary["worst_bmi_delta"]:+d} '
        f'\u00b7 worst band shift: {escape(summary["worst_band_shift"])}</span>\n'
        f"  </div>\n"
        f'  <table class="sens-table">\n'
        f"    <thead><tr>"
        f"<th>Perturbation</th><th>Alt BMI</th><th>\u0394BMI</th><th>Alt Band</th>"
        f"</tr></thead>\n"
        f"    <tbody>\n"
        f'{"".join(rows)}'
        f"    </tbody>\n"
        f"  </table>\n"
        f'  <div class="sens-note">Each row applies \u00b1{pct}% to one pillar weight; '
        f"the other three pillars are rescaled proportionally so the total stays 100. "
        f"Perturbations are render-side only \u2014 they do not change the reported BMI, "
        f"the original pillar weights, or the band assignment above.</div>\n"
        f"</div>\n"
    )
