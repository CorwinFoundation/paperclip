"""WS4 Report Card HTML render — v0.1.

Single-file HTML output (no external assets, no JS). Inputs are the
ScoreResponse dict produced by `rubric.scoring.score()` plus the loaded
Rubric (for human-readable pillar/dim names — those are not in the wire
response so they must come from the rubric the caller bound against).

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
    "A": "#2f7a3f",
    "B": "#4a8fb0",
    "C": "#b58400",
    "D": "#a83232",
}

STALE_SCAN_DAYS = 90

_STYLE = """
:root { --fg: #222; --muted: #666; --rule: #ddd; --bg-alt: #fafafa; }
* { box-sizing: border-box; }
body { font: 14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
       color: var(--fg); margin: 0; padding: 32px; max-width: 920px; }
h1 { font-size: 18px; margin: 0 0 4px 0; letter-spacing: 0.02em; text-transform: uppercase; color: var(--muted); }
h2 { font-size: 15px; margin: 28px 0 10px 0; border-bottom: 1px solid var(--rule); padding-bottom: 4px; }
.hero { display: flex; align-items: center; gap: 24px; margin-bottom: 24px; }
.bmi { font: 700 64px/1 ui-monospace,Menlo,Consolas,monospace; }
.band { font: 600 28px/1.1 -apple-system,sans-serif; padding: 10px 18px;
        border-radius: 6px; color: white; min-width: 84px; text-align: center; }
.band-sub { color: var(--muted); font-size: 13px; margin-top: 4px; }
table { border-collapse: collapse; width: 100%; }
th, td { text-align: left; padding: 6px 10px; border-bottom: 1px solid var(--rule); }
th { font-weight: 600; color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: 0.04em; }
tr.alt td { background: var(--bg-alt); }
.bar { display: inline-block; vertical-align: middle; height: 10px;
       background: linear-gradient(90deg,#3a7,#3a7); border-radius: 3px; margin-left: 8px; }
.bar-track { display: inline-block; vertical-align: middle; height: 10px; width: 120px;
             background: #eee; border-radius: 3px; position: relative; overflow: hidden; }
.bar-fill { display: block; height: 100%; background: #3a7; border-radius: 3px; }
.dim-list { margin: 8px 0 0 0; padding: 0; list-style: none; columns: 2; column-gap: 32px; }
.dim-list li { padding: 3px 0; border-bottom: 1px dotted var(--rule); break-inside: avoid; }
.dim-id { font: 600 12px/1 ui-monospace,monospace; color: var(--muted); margin-right: 6px; }
.dim-raw { float: right; font: 600 13px/1 ui-monospace,monospace; }
.footer { margin-top: 32px; padding-top: 12px; border-top: 1px solid var(--rule);
          font-size: 11px; color: var(--muted); }
.kv { display: grid; grid-template-columns: 180px 1fr; gap: 4px 12px; }
.kv dt { font-weight: 600; }
.kv dd { margin: 0; font: 12px/1.3 ui-monospace,monospace; word-break: break-all; }
.posture { font-size: 13px; color: var(--fg); margin-top: 2px; }
.warn { color: #a83232; font-weight: 600; }
.eligibility { display: flex; align-items: center; gap: 16px; padding: 12px 16px;
               border-radius: 6px; margin: 16px 0 24px 0; }
.eligibility.pass { background: #e6f3ea; border: 1px solid #2f7a3f; }
.eligibility.fail { background: #fbe9e9; border: 1px solid #a83232; }
.eligibility .verdict { font: 700 18px/1 ui-monospace,monospace; padding: 6px 12px;
                        border-radius: 4px; color: white; }
.eligibility.pass .verdict { background: #2f7a3f; }
.eligibility.fail .verdict { background: #a83232; }
.eligibility ul { margin: 0; padding-left: 18px; font-size: 13px; }
.eligibility ul li { padding: 1px 0; }
.floor { color: var(--muted); font: 11px/1 ui-monospace,monospace; }
"""


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


def render(score_response: dict[str, Any], rubric: Rubric) -> str:
    """Render a ScoreResponse to a single self-contained HTML document."""
    bmi = int(score_response["bmi"])
    band = score_response["band"]
    pillars = score_response["pillars"]
    pillar_floors = score_response.get("pillar_floors") or {}
    age_days = int(score_response.get("scan_evidence_age_days", 0))

    band_color = _BAND_COLOR.get(band["letter"], "#444")
    eligibility = evaluate_bind_eligibility(score_response)
    pillar_rows = "".join(
        _pillar_row(pid, pdata, pillar_floors.get(pid) or {})
        for pid, pdata in pillars.items()
    )
    pillar_sections = "".join(
        _pillar_section(pid, pillars[pid], rubric.pillars[pid]) for pid in pillars
    )

    age_warning = ""
    if age_days >= STALE_SCAN_DAYS:
        age_warning = (
            f' <span class="warn">[STALE: scan ≥ {STALE_SCAN_DAYS} days; '
            f'claims trip-wire fires]</span>'
        )

    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8" />
<title>BMI Report Card — {escape(band['display'])} ({bmi}/100)</title>
<style>{_STYLE}</style>
</head><body>
<h1>BEAI Maturity Index — Report Card v0.1</h1>
<div class="hero">
  <div class="bmi">{bmi}<span style="font-size:24px;color:var(--muted);">/100</span></div>
  <div>
    <div class="band" style="background:{band_color};">{escape(band['display'])}</div>
    <div class="band-sub">×{band['modifier_factor']:.2f} pricing factor &middot; bands v{escape(str(band['bands_version']))}</div>
  </div>
  <div class="posture">{escape(band['posture'])}</div>
</div>

{_eligibility_block(eligibility)}

<h2>Pillar breakdown</h2>
<table>
<thead><tr><th>Pillar</th><th>Score / Weight</th><th>Floor</th><th>Percentage</th><th>Trend</th></tr></thead>
<tbody>{pillar_rows}</tbody>
</table>

{pillar_sections}

<div class="footer">
<dl class="kv">
  <dt>Snapshot hash</dt><dd>{escape(score_response['snapshot_hash'])}</dd>
  <dt>Forensic bundle</dt><dd>{escape(score_response['forensic_bundle_uri'])}</dd>
  <dt>Scored at</dt><dd>{escape(score_response['scored_at'])}</dd>
  <dt>Scan evidence age</dt><dd>{age_days} day(s){age_warning}</dd>
  <dt>Rubric version</dt><dd>{escape(score_response['rubric_version'])}</dd>
  <dt>Bands version</dt><dd>{escape(str(band['bands_version']))} (frozen 2026-04-30)</dd>
</dl>
</div>
</body></html>
"""


def _eligibility_block(eligibility: dict[str, Any]) -> str:
    if eligibility["eligible"]:
        return (
            '<div class="eligibility pass">'
            '<div class="verdict">BIND</div>'
            '<div>All bind-blocking floors and pillar floors cleared. '
            'Eligible for primary cover at the indicated band.</div>'
            '</div>'
        )
    items = "".join(f"<li>{escape(r)}</li>" for r in eligibility["reasons"])
    return (
        '<div class="eligibility fail">'
        '<div class="verdict">NO BIND</div>'
        '<div><strong>Bind-eligibility failed. Endorsement-only or decline.</strong>'
        f'<ul>{items}</ul>'
        '</div>'
        '</div>'
    )


def _pillar_row(pid: str, pdata: dict[str, Any], floor: dict[str, Any]) -> str:
    score = float(pdata["score"])
    weight = int(pdata["weight"])
    pct = (score / weight) * 100 if weight else 0
    fill_pct = max(0, min(100, pct))
    cls = "alt" if pid in {"P2", "P4"} else ""
    floor_cell = "—"
    if floor:
        min_pillar = float(floor.get("min_pillar_score", 0))
        if min_pillar > 0:
            mark = '<span class="warn">✗</span>' if score < min_pillar else "✓"
            floor_cell = f'<span class="floor">≥ {min_pillar:.2f}</span> {mark}'
        if "min_dim_raw" in floor:
            min_dim_raw = float(floor["min_dim_raw"])
            floor_cell += f'<br><span class="floor">dim ≥ {min_dim_raw:.2f}</span>'
    return (
        f'<tr class="{cls}">'
        f'<td><strong>{escape(pid)}</strong></td>'
        f'<td>{score:.2f} / {weight}</td>'
        f'<td>{floor_cell}</td>'
        f'<td>{pct:.1f}%</td>'
        f'<td><span class="bar-track"><span class="bar-fill" style="width:{fill_pct:.1f}%"></span></span></td>'
        f'</tr>'
    )


def _pillar_section(pid: str, pdata: dict[str, Any], rubric_pillar: dict[str, Any]) -> str:
    name = rubric_pillar["name"]
    dim_items = []
    for did in rubric_pillar["dim_ids"]:
        entry = pdata["dimensions"].get(did, {})
        raw = float(entry.get("raw", 0))
        warn = ""
        thr = entry.get("applied_threshold") or {}
        if thr.get("kind") == "bind_blocking_floor" and raw < float(thr.get("min_raw", 0)):
            warn = ' <span class="warn">[BIND BLOCKED]</span>'
        dim_items.append(
            f'<li><span class="dim-id">{escape(did)}</span>'
            f'<span class="dim-raw">{raw:.1f}/4</span>{warn}</li>'
        )
    return (
        f'<h2>{escape(pid)} — {escape(name)}</h2>'
        f'<ul class="dim-list">{"".join(dim_items)}</ul>'
    )
