"""WS4 Report Card HTML render — v0.2.

Single-file HTML output (no external assets, no JS). Inputs are the
ScoreResponse dict produced by `rubric.scoring.score()` plus the loaded
Rubric (for human-readable pillar/dim names).

Aligned with BEAAA-13 sample rev 5: 12-band display, frozen weights
(P1=35/P2=20/P3=30/P4=15), pillar breakdown, per-dim raw, red lines visible
in the footer (snapshot_hash, forensic_bundle_uri, rubric_version).

§9.3 cut: HTML-only this phase; PDF render deferred to v0.2.
"""
from __future__ import annotations

from html import escape
from typing import Any

from rubric.scoring import Rubric

_BAND_COLOR = {
    "A": "#2e7d32",
    "B": "#1565c0",
    "C": "#c67c00",
    "D": "#b71c1c",
}

_BAND_TIER = {
    "A": "Preferred",
    "B": "Standard",
    "C": "Surplus",
    "D": "Decline",
}

STALE_SCAN_DAYS = 90

_STYLE = """
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
  font-size: 14px; line-height: 1.5; color: #1a2027; background: #edf2f7;
}
.report { max-width: 960px; margin: 0 auto; background: #fff; min-height: 100vh; }
.report-accent { height: 5px; background: var(--band-color, #1565c0); }
.report-header {
  padding: 18px 32px 14px; border-bottom: 1px solid #e2e8f0;
  display: flex; align-items: flex-start; justify-content: space-between;
}
.report-brand {
  font-size: 11px; font-weight: 700; text-transform: uppercase;
  letter-spacing: 0.1em; color: #718096;
}
.report-title { font-size: 15px; font-weight: 600; color: #1a2027; margin-top: 2px; }
.report-meta { font-size: 12px; color: #718096; text-align: right; line-height: 1.6; }
.report-body { padding: 28px 32px; }
.hero {
  display: flex; align-items: center; gap: 28px; margin-bottom: 24px;
  padding: 22px 24px; background: #f7f9fc;
  border-radius: 12px; border: 1px solid #e2e8f0;
}
.score-ring {
  flex-shrink: 0; width: 110px; height: 110px; border-radius: 50%;
  background: conic-gradient(
    var(--band-color, #1565c0) var(--score-deg, 0deg),
    #e2e8f0 var(--score-deg, 0deg)
  );
  display: flex; align-items: center; justify-content: center; position: relative;
}
.score-ring::before {
  content: ''; position: absolute; width: 80px; height: 80px;
  background: #f7f9fc; border-radius: 50%;
}
.score-inner { position: relative; z-index: 1; text-align: center; }
.score-number {
  font: 700 28px/1 ui-monospace, Menlo, Consolas, monospace;
  color: var(--band-color, #1565c0);
}
.score-denom { font-size: 11px; color: #718096; display: block; margin-top: 1px; }
.band-block { flex-shrink: 0; }
.band-badge {
  display: inline-flex; flex-direction: column; align-items: center;
  padding: 10px 18px; background: var(--band-color, #1565c0);
  border-radius: 10px; color: #fff; min-width: 84px; text-align: center;
}
.band-letter { font: 700 30px/1 -apple-system, sans-serif; }
.band-tier {
  font-size: 10px; font-weight: 700; text-transform: uppercase;
  letter-spacing: 0.07em; margin-top: 4px; opacity: 0.9;
}
.band-meta { margin-top: 7px; font-size: 12px; color: #718096; }
.hero-stats { flex: 1; }
.posture-label { font-size: 17px; font-weight: 700; color: #1a2027; margin-bottom: 6px; }
.pricing-tag {
  display: inline-block; background: #f0f4f8; border: 1px solid #e2e8f0;
  border-radius: 4px; padding: 3px 8px;
  font: 12px/1 ui-monospace, Menlo, monospace; color: #4a5568;
}
.eligibility {
  display: flex; align-items: flex-start; gap: 14px; padding: 14px 18px;
  border-radius: 8px; margin-bottom: 28px; border: 1.5px solid;
}
.eligibility.pass { background: #f0faf4; border-color: #2e7d32; color: #1b5e20; }
.eligibility.fail { background: #fff5f5; border-color: #c62828; color: #7f1d1d; }
.verdict-badge {
  flex-shrink: 0; font: 700 12px/1 ui-monospace, monospace;
  padding: 8px 13px; border-radius: 6px; color: #fff;
  text-transform: uppercase; letter-spacing: 0.05em; margin-top: 1px;
}
.eligibility.pass .verdict-badge { background: #2e7d32; }
.eligibility.fail .verdict-badge { background: #c62828; }
.eligibility-body { font-size: 13px; }
.eligibility-body strong { display: block; margin-bottom: 5px; font-size: 13px; }
.fail-reasons { margin: 0; padding-left: 16px; font: 12px/1.6 ui-monospace, Menlo, monospace; }
.section-title {
  font-size: 11px; font-weight: 700; text-transform: uppercase;
  letter-spacing: 0.1em; color: #718096; margin-bottom: 12px;
  padding-bottom: 6px; border-bottom: 2px solid var(--band-color, #1565c0);
  display: inline-block;
}
.pillar-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 32px; }
.pillar-card {
  padding: 16px 16px 14px; border: 1px solid #e2e8f0; border-radius: 10px;
  background: #fff; position: relative; overflow: hidden;
}
.pillar-card::before {
  content: ''; position: absolute; left: 0; top: 0; bottom: 0;
  width: 4px; background: var(--pillar-color, #1565c0);
}
.pillar-id {
  font: 700 11px/1 ui-monospace, monospace; text-transform: uppercase;
  color: var(--pillar-color, #1565c0); margin-bottom: 2px;
}
.pillar-name { font-size: 13px; font-weight: 600; color: #1a2027; margin-bottom: 10px; }
.pillar-score-row {
  display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 6px;
}
.pillar-score-val { font: 600 17px/1 ui-monospace, monospace; color: #1a2027; }
.pillar-score-wt { font-size: 12px; color: #718096; }
.pillar-pct { font-size: 12px; font-weight: 600; color: var(--pillar-color, #1565c0); }
.progress-track { height: 8px; background: #e2e8f0; border-radius: 4px; overflow: hidden; margin-bottom: 7px; }
.progress-fill { height: 100%; border-radius: 4px; background: var(--pillar-color, #1565c0); }
.floor-line { font: 11px/1.6 ui-monospace, Menlo, monospace; color: #718096; }
.floor-pass { color: #2e7d32; font-weight: 600; }
.floor-fail { color: #c62828; font-weight: 600; }
.dim-section { margin-bottom: 26px; }
.dim-section-hdr { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.dim-pid {
  font: 700 11px/1 ui-monospace, monospace; background: var(--pillar-color, #1565c0);
  color: #fff; padding: 3px 8px; border-radius: 4px;
}
.dim-section-name { font-size: 13px; font-weight: 600; color: #1a2027; }
.dim-table { width: 100%; border-collapse: collapse; }
.dim-table th {
  text-align: left; padding: 6px 10px; font-size: 11px; font-weight: 600;
  text-transform: uppercase; letter-spacing: 0.05em; color: #718096;
  border-bottom: 1px solid #e2e8f0;
}
.dim-table td { padding: 7px 10px; border-bottom: 1px solid #f0f4f8; vertical-align: middle; }
.dim-table tr:last-child td { border-bottom: none; }
.dim-table tr:nth-child(even) td { background: #f9fafb; }
.dim-id-col { font: 600 12px/1 ui-monospace, monospace; color: #4a5568; white-space: nowrap; }
.dim-name-col { font-size: 13px; color: #1a2027; }
.dim-raw-col {
  font: 600 12px/1 ui-monospace, monospace; color: #1a2027;
  white-space: nowrap; text-align: right;
}
.dim-bar-col { width: 90px; }
.dim-bar-track { height: 6px; background: #e2e8f0; border-radius: 3px; overflow: hidden; width: 80px; }
.dim-bar-fill { height: 100%; border-radius: 3px; background: var(--pillar-color, #1565c0); }
.warn-badge {
  display: inline-block; font: 700 10px/1 ui-monospace, monospace;
  background: #fff0f0; color: #c62828; border: 1px solid #fca5a5;
  padding: 2px 5px; border-radius: 3px; margin-left: 6px; vertical-align: middle;
}
.audit-trail {
  margin-top: 32px; padding: 18px 20px; background: #f7f9fc;
  border-radius: 10px; border: 1px solid #e2e8f0;
}
.audit-title {
  font-size: 11px; font-weight: 700; text-transform: uppercase;
  letter-spacing: 0.1em; color: #718096; margin-bottom: 12px;
}
.audit-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px 24px; }
.audit-item { display: flex; flex-direction: column; }
.audit-key {
  font-size: 11px; font-weight: 600; color: #718096;
  text-transform: uppercase; letter-spacing: 0.04em; margin-bottom: 2px;
}
.audit-val { font: 12px/1.4 ui-monospace, Menlo, monospace; color: #1a2027; word-break: break-all; }
.stale-warn { color: #c62828; font-weight: 600; }
.prov {
  display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
  margin-bottom: 22px; padding: 10px 14px; border-radius: 8px;
  border: 1.5px solid; font-size: 12px;
}
.prov-tag {
  font: 700 11px/1 ui-monospace, Menlo, monospace; text-transform: uppercase;
  letter-spacing: 0.06em; padding: 5px 9px; border-radius: 4px; color: #fff;
  white-space: nowrap;
}
.prov-detail { color: inherit; line-height: 1.4; flex: 1; min-width: 200px; }
.prov-counter {
  font: 600 11px/1 ui-monospace, Menlo, monospace; color: #4a5568;
  background: #fff; padding: 4px 8px; border-radius: 3px;
  border: 1px solid #e2e8f0; white-space: nowrap;
}
.prov-measured { background: #f0faf4; border-color: #2e7d32; color: #1b5e20; }
.prov-measured .prov-tag { background: #2e7d32; }
.prov-proxy    { background: #fff8eb; border-color: #c67c00; color: #6b4a00; }
.prov-proxy .prov-tag { background: #c67c00; }
.prov-prior    { background: #f4f6f8; border-color: #718096; color: #2d3748; }
.prov-prior .prov-tag { background: #718096; }
.prov-missing  { background: #fff5f5; border-color: #c62828; color: #7f1d1d; }
.prov-missing .prov-tag { background: #c62828; }
@media print { body { background: #fff; } .report { box-shadow: none; } }
"""


def _pillar_pct_color(pct: float) -> str:
    if pct >= 70:
        return "#2e7d32"
    if pct >= 50:
        return "#1565c0"
    if pct >= 35:
        return "#c67c00"
    return "#b71c1c"


def evaluate_bind_eligibility(score_response: dict[str, Any]) -> dict[str, Any]:
    """Compute bind verdict from a ScoreResponse.

    Pulls applied_threshold (per-dim) and pillar_floors (per-pillar) directly
    from the response — no rubric re-load. Returns:
        {"eligible": bool, "reasons": list[str]}
    Reasons are populated only on fail. Order: per-dim bind_blocking_floor
    breaches, then per-pillar aggregate floor breaches, then per-pillar
    min_dim_raw breaches (P3 weak-link guarantee, BEAAA-71).
    """
    reasons: list[str] = []
    pillars = score_response.get("pillars") or {}
    pillar_floors = score_response.get("pillar_floors") or {}

    for pid, pdata in pillars.items():
        for did, entry in pdata.get("dimensions", {}).items():
            thr = entry.get("applied_threshold") or {}
            if thr.get("kind") == "bind_blocking_floor":
                raw = float(entry.get("raw", 0))
                min_raw = float(thr.get("min_raw", 0))
                if raw < min_raw:
                    reasons.append(
                        f"{did} raw={raw:.2f} < bind_blocking_floor={min_raw:.2f}"
                    )

    for pid, pdata in pillars.items():
        floor = pillar_floors.get(pid) or {}
        score = float(pdata.get("score", 0))
        min_pillar = float(floor.get("min_pillar_score", 0))
        if score < min_pillar:
            reasons.append(
                f"{pid} score={score:.2f} < pillar_floor={min_pillar:.2f}"
            )

        if "min_dim_raw" in floor:
            min_dim_raw = float(floor["min_dim_raw"])
            for did, entry in pdata.get("dimensions", {}).items():
                raw = float(entry.get("raw", 0))
                if raw < min_dim_raw:
                    reasons.append(
                        f"{pid} weak-link: {did} raw={raw:.2f} < min_dim_raw={min_dim_raw:.2f}"
                    )

    return {"eligible": not reasons, "reasons": reasons}


def render(
    score_response: dict[str, Any],
    rubric: Rubric,
    *,
    provenance: str | None = None,
    dim_provenance: dict[str, str] | None = None,
) -> str:
    """Render a ScoreResponse to a single self-contained HTML document.

    `provenance` is the scan-level label that drives the badge:
      - "measured" / "measured: <detail>"          → green badge
      - "category-proxy:<host> — <detail>"          → orange badge (UW co-gate)
      - "prior" / "directional priors"              → gray badge
      - None                                       → red "PROVENANCE NOT SET"
        warning badge — surfaces missing labelling rather than silently
        rendering an unlabelled card.

    `dim_provenance` is the per-dim map produced by
    `harness.pipeline.merge_measurements_with_priors` (values like
    "prior" or "measured:<benchmark>"). When supplied, an "M measured /
    N priors" counter renders next to the badge.
    """
    bmi = int(score_response["bmi"])
    band = score_response["band"]
    pillars = score_response["pillars"]
    pillar_floors = score_response.get("pillar_floors") or {}
    age_days = int(score_response.get("scan_evidence_age_days", 0))
    scored_at = escape(score_response["scored_at"])

    band_letter = band["letter"]
    band_color = _BAND_COLOR.get(band_letter, "#444")
    band_tier = escape(_BAND_TIER.get(band_letter, ""))
    eligibility = evaluate_bind_eligibility(score_response)
    provenance_html = _provenance_badge(provenance, dim_provenance)
    score_deg = f"{(bmi / 100) * 360:.1f}deg"

    pillar_cards_html = "".join(
        _pillar_card(pid, pillars[pid], pillar_floors.get(pid) or {}, rubric.pillars[pid])
        for pid in pillars
    )
    pillar_sections_html = "".join(
        _pillar_section(pid, pillars[pid], rubric.pillars[pid])
        for pid in pillars
    )

    age_warning = ""
    if age_days >= STALE_SCAN_DAYS:
        age_warning = (
            f' <span class="stale-warn">[STALE: scan \u2265 {STALE_SCAN_DAYS} days; '
            f"claims trip-wire fires]</span>"
        )

    return (
        f"<!DOCTYPE html>\n"
        f'<html lang="en">\n'
        f"<head>\n"
        f'<meta charset="utf-8" />\n'
        f"<title>BMI Report Card \u2014 {escape(band['display'])} ({bmi}/100)</title>\n"
        f"<style>{_STYLE}</style>\n"
        f"</head>\n"
        f"<body>\n"
        f'<div class="report" style="--band-color:{band_color};">\n'
        f'  <div class="report-accent"></div>\n'
        f'  <div class="report-header">\n'
        f"    <div>\n"
        f'      <div class="report-brand">BEAI Insurance</div>\n'
        f'      <div class="report-title">BEAI Maturity Index \u2014 Report Card</div>\n'
        f"    </div>\n"
        f'    <div class="report-meta">\n'
        f"      Scored: {scored_at}<br>\n"
        f"      Rubric v{escape(score_response['rubric_version'])}\n"
        f"    </div>\n"
        f"  </div>\n"
        f'  <div class="report-body">\n'
        f"\n"
        f'    <div class="hero">\n'
        f'      <div class="score-ring" style="--score-deg:{score_deg};">\n'
        f'        <div class="score-inner">\n'
        f'          <span class="score-number">{bmi}</span>\n'
        f'          <span class="score-denom">/100</span>\n'
        f"        </div>\n"
        f"      </div>\n"
        f'      <div class="band-block">\n'
        f'        <div class="band-badge">\n'
        f'          <span class="band-letter">{escape(band["display"])}</span>\n'
        f'          <span class="band-tier">{band_tier}</span>\n'
        f"        </div>\n"
        f'        <div class="band-meta">\u00d7{band["modifier_factor"]:.2f} pricing'
        f' \u00b7 bands v{escape(str(band["bands_version"]))}</div>\n'
        f"      </div>\n"
        f'      <div class="hero-stats">\n'
        f'        <div class="posture-label">{escape(band["posture"])}</div>\n'
        f'        <span class="pricing-tag">modifier_factor = \u00d7{band["modifier_factor"]:.2f}</span>\n'
        f"      </div>\n"
        f"    </div>\n"
        f"\n"
        f"    {provenance_html}\n"
        f"\n"
        f"    {_eligibility_block(eligibility)}\n"
        f"\n"
        f'    <div class="section-title">Pillar Breakdown</div>\n'
        f'    <div class="pillar-grid">\n'
        f"{pillar_cards_html}"
        f"    </div>\n"
        f"\n"
        f'    <div class="section-title">Dimension Detail</div>\n'
        f"{pillar_sections_html}"
        f'    <div class="audit-trail">\n'
        f'      <div class="audit-title">Audit Trail</div>\n'
        f'      <div class="audit-grid">\n'
        f'        <div class="audit-item">\n'
        f'          <span class="audit-key">Snapshot hash</span>\n'
        f'          <span class="audit-val">{escape(score_response["snapshot_hash"])}</span>\n'
        f"        </div>\n"
        f'        <div class="audit-item">\n'
        f'          <span class="audit-key">Forensic bundle</span>\n'
        f'          <span class="audit-val">{escape(score_response["forensic_bundle_uri"])}</span>\n'
        f"        </div>\n"
        f'        <div class="audit-item">\n'
        f'          <span class="audit-key">Scored at</span>\n'
        f'          <span class="audit-val">{escape(score_response["scored_at"])}</span>\n'
        f"        </div>\n"
        f'        <div class="audit-item">\n'
        f'          <span class="audit-key">Scan evidence age</span>\n'
        f'          <span class="audit-val">{age_days} day(s){age_warning}</span>\n'
        f"        </div>\n"
        f'        <div class="audit-item">\n'
        f'          <span class="audit-key">Rubric version</span>\n'
        f'          <span class="audit-val">{escape(score_response["rubric_version"])}</span>\n'
        f"        </div>\n"
        f'        <div class="audit-item">\n'
        f'          <span class="audit-key">Bands version</span>\n'
        f'          <span class="audit-val">{escape(str(band["bands_version"]))} (frozen 2026-04-30)</span>\n'
        f"        </div>\n"
        f"      </div>\n"
        f"    </div>\n"
        f"\n"
        f"  </div>\n"
        f"</div>\n"
        f"</body>\n"
        f"</html>\n"
    )


def _provenance_badge(
    provenance: str | None,
    dim_provenance: dict[str, str] | None,
) -> str:
    """Render the provenance badge HTML block.

    Color discipline matches the rule established on BEAAA-130 / BEAAA-50:
    `measured` is the only label that licenses customer-deployment claims,
    `category-proxy:*` always carries a UW co-gate caveat, and `prior` /
    None never license $-figure or band claims downstream.
    """
    if dim_provenance:
        n_measured = sum(1 for v in dim_provenance.values() if v.startswith("measured"))
        n_prior = sum(1 for v in dim_provenance.values() if v == "prior")
        counter = (
            f'<span class="prov-counter">'
            f"{n_measured} measured / {n_prior} prior dim(s)"
            f"</span>"
        )
    else:
        counter = ""

    label = (provenance or "").strip()
    if not label:
        kind, tag, detail = (
            "missing",
            "PROVENANCE NOT SET",
            "caller did not supply scan provenance \u2014 treat as unlabelled, do "
            "not quote $-figure or band downstream",
        )
    elif label.lower().startswith("category-proxy"):
        head, _, rest = label.partition(" \u2014 ")
        kind, tag, detail = "proxy", head.upper(), (rest or "UW co-gate required for $-figure / band claims")
    elif label.lower().startswith("measured"):
        head, _, rest = label.partition(" \u2014 ")
        kind, tag, detail = "measured", head.upper(), (rest or "direct customer-deployment scan")
    elif label.lower().startswith("prior") or label.lower().startswith("directional"):
        head, _, rest = label.partition(" \u2014 ")
        kind, tag, detail = (
            "prior",
            "DIRECTIONAL PRIOR",
            (rest or head if rest else "no measurements; priors-only scan"),
        )
    else:
        kind, tag, detail = "missing", "PROVENANCE UNRECOGNIZED", label

    return (
        f'<div class="prov prov-{kind}">'
        f'<span class="prov-tag">{escape(tag)}</span>'
        f'<span class="prov-detail">{escape(detail)}</span>'
        f"{counter}"
        f"</div>"
    )


def _eligibility_block(eligibility: dict[str, Any]) -> str:
    if eligibility["eligible"]:
        return (
            '<div class="eligibility pass">'
            '<div class="verdict-badge">BIND</div>'
            '<div class="eligibility-body">All bind-blocking floors and pillar floors cleared. '
            "Eligible for primary cover at the indicated band.</div>"
            "</div>"
        )
    items = "".join(f"<li>{escape(r)}</li>" for r in eligibility["reasons"])
    return (
        '<div class="eligibility fail">'
        '<div class="verdict-badge">NO BIND</div>'
        '<div class="eligibility-body">'
        "<strong>Bind-eligibility failed. Endorsement-only or decline.</strong>"
        f'<ul class="fail-reasons">{items}</ul>'
        "</div>"
        "</div>"
    )


def _pillar_card(
    pid: str,
    pdata: dict[str, Any],
    floor: dict[str, Any],
    rubric_pillar: dict[str, Any],
) -> str:
    name = rubric_pillar["name"]
    score = float(pdata["score"])
    weight = int(pdata["weight"])
    pct = (score / weight) * 100 if weight else 0
    fill_pct = max(0.0, min(100.0, pct))
    color = _pillar_pct_color(pct)

    floor_html = ""
    if floor:
        min_pillar = float(floor.get("min_pillar_score", 0))
        if min_pillar > 0:
            if score >= min_pillar:
                floor_html += (
                    f'<span class="floor-line floor-pass">\u2265 {min_pillar:.2f} \u2713</span> '
                )
            else:
                floor_html += (
                    f'<span class="floor-line floor-fail">\u2265 {min_pillar:.2f} \u2717</span> '
                )
        if "min_dim_raw" in floor:
            min_dim_raw = float(floor["min_dim_raw"])
            floor_html += f'<span class="floor-line">dim \u2265 {min_dim_raw:.2f}</span>'

    return (
        f'      <div class="pillar-card" style="--pillar-color:{color};">\n'
        f'        <div class="pillar-id">{escape(pid)}</div>\n'
        f'        <div class="pillar-name">{escape(name)}</div>\n'
        f'        <div class="pillar-score-row">\n'
        f'          <span class="pillar-score-val">{score:.2f}</span>'
        f'<span class="pillar-score-wt"> / {weight}</span>\n'
        f'          <span class="pillar-pct">{pct:.1f}%</span>\n'
        f"        </div>\n"
        f'        <div class="progress-track">'
        f'<div class="progress-fill" style="width:{fill_pct:.1f}%;"></div></div>\n'
        f"        {floor_html}\n"
        f"      </div>\n"
    )


def _pillar_section(
    pid: str, pdata: dict[str, Any], rubric_pillar: dict[str, Any]
) -> str:
    name = rubric_pillar["name"]
    dim_names = rubric_pillar.get("dim_names", {})
    score_val = float(pdata["score"])
    weight = int(pdata["weight"])
    pct = (score_val / weight) * 100 if weight else 0
    color = _pillar_pct_color(pct)

    rows = []
    for did in rubric_pillar["dim_ids"]:
        entry = pdata["dimensions"].get(did, {})
        raw = float(entry.get("raw", 0))
        dim_pct = (raw / 4) * 100
        dim_fill = max(0.0, min(100.0, dim_pct))
        dim_name = escape(dim_names.get(did, ""))

        warn = ""
        thr = entry.get("applied_threshold") or {}
        if thr.get("kind") == "bind_blocking_floor" and raw < float(thr.get("min_raw", 0)):
            warn = '<span class="warn-badge">BIND BLOCKED</span>'

        rows.append(
            f"          <tr>\n"
            f'            <td class="dim-id-col">{escape(did)}</td>\n'
            f'            <td class="dim-name-col">{dim_name}{warn}</td>\n'
            f'            <td class="dim-raw-col">{raw:.1f}/4</td>\n'
            f'            <td class="dim-bar-col">'
            f'<div class="dim-bar-track">'
            f'<div class="dim-bar-fill" style="width:{dim_fill:.1f}%;"></div>'
            f"</div></td>\n"
            f"          </tr>\n"
        )

    return (
        f'    <div class="dim-section" style="--pillar-color:{color};">\n'
        f'      <div class="dim-section-hdr">\n'
        f'        <span class="dim-pid">{escape(pid)}</span>\n'
        f'        <span class="dim-section-name">{escape(name)}</span>\n'
        f"      </div>\n"
        f'      <table class="dim-table">\n'
        f"        <thead><tr>"
        f"<th>Dim</th><th>Description</th><th>Raw</th><th>Score</th>"
        f"</tr></thead>\n"
        f"        <tbody>\n"
        f'{"".join(rows)}'
        f"        </tbody>\n"
        f"      </table>\n"
        f"    </div>\n"
    )


# ---------------------------------------------------------------------------
# Provenance badge — BEAAA-130 rec #2
# ---------------------------------------------------------------------------

_PROV_CSS = """
.prov-banner {
  display: flex; align-items: flex-start; gap: 10px; padding: 10px 16px;
  border-radius: 7px; margin-bottom: 16px; border: 1.5px solid;
  font-size: 12px;
}
.prov-missing { background: #fffbeb; border-color: #d97706; color: #78350f; }
.prov-measured { background: #f0faf4; border-color: #2e7d32; color: #1b5e20; }
.prov-proxy { background: #fff7ed; border-color: #c2410c; color: #7c2d12; }
.prov-prior { background: #f5f3ff; border-color: #7c3aed; color: #4c1d95; }
.prov-badge {
  flex-shrink: 0; font: 700 10px/1 ui-monospace, monospace;
  padding: 3px 7px; border-radius: 4px; color: #fff; white-space: nowrap;
}
.prov-missing .prov-badge { background: #d97706; }
.prov-measured .prov-badge { background: #2e7d32; }
.prov-proxy .prov-badge { background: #c2410c; }
.prov-prior .prov-badge { background: #7c3aed; }
.prov-detail { line-height: 1.4; }
"""
