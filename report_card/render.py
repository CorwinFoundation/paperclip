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
"""


def render(score_response: dict[str, Any], rubric: Rubric) -> str:
    """Render a ScoreResponse to a single self-contained HTML document."""
    bmi = int(score_response["bmi"])
    band = score_response["band"]
    pillars = score_response["pillars"]

    band_color = _BAND_COLOR.get(band["letter"], "#444")
    pillar_rows = "".join(_pillar_row(pid, pdata) for pid, pdata in pillars.items())
    pillar_sections = "".join(
        _pillar_section(pid, pillars[pid], rubric.pillars[pid]) for pid in pillars
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

<h2>Pillar breakdown</h2>
<table>
<thead><tr><th>Pillar</th><th>Score / Weight</th><th>Percentage</th><th>Trend</th></tr></thead>
<tbody>{pillar_rows}</tbody>
</table>

{pillar_sections}

<div class="footer">
<dl class="kv">
  <dt>Snapshot hash</dt><dd>{escape(score_response['snapshot_hash'])}</dd>
  <dt>Forensic bundle</dt><dd>{escape(score_response['forensic_bundle_uri'])}</dd>
  <dt>Scored at</dt><dd>{escape(score_response['scored_at'])}</dd>
  <dt>Rubric version</dt><dd>{escape(score_response['rubric_version'])}</dd>
  <dt>Bands version</dt><dd>{escape(str(band['bands_version']))} (frozen 2026-04-30)</dd>
</dl>
</div>
</body></html>
"""


def _pillar_row(pid: str, pdata: dict[str, Any]) -> str:
    score = float(pdata["score"])
    weight = int(pdata["weight"])
    pct = (score / weight) * 100 if weight else 0
    fill_pct = max(0, min(100, pct))
    cls = "alt" if pid in {"P2", "P4"} else ""
    return (
        f'<tr class="{cls}">'
        f'<td><strong>{escape(pid)}</strong></td>'
        f'<td>{score:.2f} / {weight}</td>'
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
