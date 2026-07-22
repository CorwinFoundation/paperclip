"""WS4 Report Card HTML render — v0.2.

Single-file HTML output (no external assets, no JS). Inputs are the
ScoreResponse dict produced by `rubric.scoring.score()` plus the loaded
Rubric (for human-readable pillar/dim names).

Aligned with BEAAA-13 sample rev 5: 12-band display, frozen weights
(P1=35/P2=20/P3=30/P4=15), pillar breakdown, per-dim raw, red lines visible
in the footer (snapshot_hash, forensic_bundle_uri, rubric_version).

Provenance badge (BEAAA-130 rec #2): render() accepts optional `provenance`
and `dim_provenance` kwargs; unset provenance renders a loud warning.

Sensitivity panel (BEAAA-130 rec #3): `include_sensitivity=True` (default)
embeds the ±10% pillar-weight perturbation block between Pillar Breakdown
and Dimension Detail. Render-side only — never mutates ARE/band/scoring.

Rubric source + framework crosswalk + probe drilldown (BEAAA-130 rec
A+B+C): when the rubric carries top-level `provenance` and per-pillar
`purpose` / `pack_sources` / `raw_mapping`, a Rubric Source block is
rendered above Pillar Breakdown. Per-dim OWASP / NIST / MITRE badges
render inline with each dim row. When the caller passes `probe_records`,
each pillar section gets a per-dim `<details>` drilldown showing the
N probes with prompt/response/verdict — turning the report from
dashboard into engineering audit.

§9.3 cut: HTML-only this phase; PDF render deferred to v0.2.
"""
from __future__ import annotations

from html import escape
from typing import Any

from report_card.diff import render_diff_block, render_dim_delta_pill
from report_card.reproducibility import render_reproducibility_block
from report_card.sensitivity import render_sensitivity_block
from rubric.scoring import Rubric

# BEAAA-221: audit-trail placeholder tokens that prior scan scripts wrote
# directly into snapshot_hash / forensic_bundle_uri. Reject at render time
# so future vendor scans cannot ship cards with literal placeholders.
# Real customer attachment URLs never contain "no-bundle" / "beaai.local"
# / "not-a-real-snapshot"; tests use distinct fixture values
# (sha256:test, https://bundles.local/...). Catch-all guard, vendor-agnostic.
_AUDIT_PLACEHOLDER_SNAPSHOT_TOKENS = ("not-a-real-snapshot",)
_AUDIT_PLACEHOLDER_BUNDLE_TOKENS = ("no-bundle", "beaai.local")
EM_DASH = "\u2014"


class AuditPlaceholderError(ValueError):
    """Raised when the report card would emit placeholder audit-trail values."""


def _assert_no_audit_placeholders(snapshot_hash: str, forensic_bundle_uri: str) -> None:
    for tok in _AUDIT_PLACEHOLDER_SNAPSHOT_TOKENS:
        if tok in snapshot_hash:
            raise AuditPlaceholderError(
                f"Refusing to render report card with placeholder snapshot_hash "
                f"{snapshot_hash!r} (matched token {tok!r}). See BEAAA-221: pass "
                "a real sha256 over the canonical run JSON."
            )
    for tok in _AUDIT_PLACEHOLDER_BUNDLE_TOKENS:
        if tok in forensic_bundle_uri:
            raise AuditPlaceholderError(
                f"Refusing to render report card with placeholder forensic_bundle_uri "
                f"{forensic_bundle_uri!r} (matched token {tok!r}). See BEAAA-221: "
                "pass a real attachment URL (e.g. /api/attachments/{id}/content)."
            )


# BEAAA-390 / BEAAA-388. Single source of truth for the "About this score"
# LOI-stage caveat that renders above the score band. Format-string with
# three substitutions stamped from the ScoreResponse instance — never hard-
# coded. v0.1 draft per BEAAA-388 description; CMO + Underwriter co-sign on
# BEAAA-388 lands the final copy via a one-line swap of this constant.
# grep guard: if you see the caveat copy duplicated anywhere else in the
# repo, that is a bug — there must be exactly one occurrence (this one).
CAVEAT_TEMPLATE = (
    "**About this score**\n"
    "\n"
    "This ARE assessment uses our internal rubric version {rubric_version} "
    "(in-flight), which weights the four risk pillars differently from the "
    "customer-facing ARE Scanner API contract {contract_version} we are "
    "locking. We are closing this gap in BEAAA-353 and will re-issue this "
    "score under contract {contract_version} weights before any policy is "
    "bound. The result you see is directionally accurate for an LOI-stage "
    "assessment; the precise score may shift by a small number of points "
    "under contract {contract_version} weights. We are disclosing this here "
    "rather than after the fact.\n"
    "\n"
    "Rubric: v{rubric_version} (effective) \u00b7 Contract: {contract_version} "
    "(target) \u00b7 Scanner build: {scanner_version}"
)


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
.caveat-block {
  margin-bottom: 20px; padding: 14px 18px;
  background: #fffbeb; border: 1.5px solid #d97706;
  border-radius: 10px; color: #5b3a05;
}
.caveat-block h3 {
  font-size: 11px; font-weight: 700; text-transform: uppercase;
  letter-spacing: 0.1em; color: #78350f; margin-bottom: 8px;
}
.caveat-block p { font-size: 12px; line-height: 1.55; margin-bottom: 8px; }
.caveat-block p:last-child { margin-bottom: 0; }
.caveat-versions {
  font: 600 11px/1.4 ui-monospace, Menlo, Consolas, monospace;
  color: #78350f; margin-top: 4px;
}
.hero {
  display: flex; align-items: center; gap: 28px; margin-bottom: 20px;
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
.eligibility {
  display: flex; align-items: flex-start; gap: 14px; padding: 14px 18px;
  border-radius: 8px; margin-bottom: 28px; border: 1.5px solid;
}
.eligibility.pass { background: #f0faf4; border-color: #2e7d32; color: #1b5e20; }
.eligibility.fail { background: #fff5f5; border-color: #c62828; color: #7f1d1d; }
.eligibility.refer { background: #fffbeb; border-color: #d97706; color: #78350f; }
.verdict-badge {
  flex-shrink: 0; font: 700 12px/1 ui-monospace, monospace;
  padding: 8px 13px; border-radius: 6px; color: #fff;
  text-transform: uppercase; letter-spacing: 0.05em; margin-top: 1px;
}
.eligibility.pass .verdict-badge { background: #2e7d32; }
.eligibility.fail .verdict-badge { background: #c62828; }
.eligibility.refer .verdict-badge { background: #d97706; }
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
.pillar-na-badge {
  display: inline-block; font: 700 12px/1 ui-monospace, monospace;
  padding: 7px 10px; border-radius: 6px; color: #78350f;
  background: #fffbeb; border: 1px solid #d97706;
}
.pillar-na-note { font-size: 12px; color: #718096; }
.pillar-partial-badge {
  display: inline-block; font: 700 10px/1 ui-monospace, monospace;
  padding: 3px 6px; border-radius: 4px; color: #78350f;
  background: #fffbeb; border: 1px solid #d97706; margin-left: 6px;
}
.pillar-partial-note { font-size: 11px; line-height: 1.4; color: #92400e; margin-bottom: 6px; }
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
.rubric-source {
  margin: 18px 0 24px; padding: 14px 18px; border: 1px solid #e2e8f0;
  border-radius: 10px; background: #fafbfc;
}
.rubric-source h3 {
  font-size: 11px; font-weight: 700; text-transform: uppercase;
  letter-spacing: 0.1em; color: #718096; margin-bottom: 10px;
}
.rubric-source dl {
  display: grid; grid-template-columns: max-content 1fr; gap: 4px 16px;
  font-size: 12px;
}
.rubric-source dt { color: #718096; font-weight: 600; }
.rubric-source dd { color: #1a2027; line-height: 1.5; }
.pillar-purpose {
  font-size: 12px; color: #4a5568; line-height: 1.45;
  margin: 8px 0 6px;
}
.pack-chips { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 6px; }
.pack-chip {
  display: inline-block; font: 600 10px/1 ui-monospace, Menlo, monospace;
  background: #eef2f7; color: #4a5568; border: 1px solid #e2e8f0;
  padding: 3px 6px; border-radius: 3px;
}
.pillar-mapping {
  font-size: 11px; color: #718096; margin-top: 6px; line-height: 1.45;
  font-style: italic;
}
.fw-badges { display: inline-flex; flex-wrap: wrap; gap: 3px; margin-left: 6px; vertical-align: middle; }
.fw-badge {
  display: inline-block; font: 600 9px/1 ui-monospace, Menlo, monospace;
  padding: 2px 4px; border-radius: 2px; white-space: nowrap;
  border: 1px solid;
}
.fw-owasp { background: #fff7ed; color: #9a3412; border-color: #fdba74; }
.fw-nist  { background: #eff6ff; color: #1e40af; border-color: #93c5fd; }
.fw-mitre { background: #f5f3ff; color: #5b21b6; border-color: #c4b5fd; }
.probe-drilldown { margin-top: 8px; }
.probe-drilldown summary {
  cursor: pointer; font: 600 11px/1 -apple-system, sans-serif;
  color: #4a5568; padding: 6px 0; list-style: none; user-select: none;
}
.probe-drilldown summary::-webkit-details-marker { display: none; }
.probe-drilldown summary::before {
  content: '▸ '; color: #718096; font-size: 10px;
}
.probe-drilldown[open] summary::before { content: '▾ '; }
.probe-table {
  width: 100%; border-collapse: collapse; margin-top: 4px;
  font-size: 11px; color: #1a2027;
}
.probe-table th {
  text-align: left; padding: 5px 8px; font-weight: 600; color: #718096;
  text-transform: uppercase; letter-spacing: 0.04em; font-size: 10px;
  border-bottom: 1px solid #e2e8f0;
}
.probe-table td {
  padding: 5px 8px; border-bottom: 1px solid #f0f4f8; vertical-align: top;
  font: 11px/1.45 ui-monospace, Menlo, monospace;
}
.probe-table .probe-id { color: #4a5568; white-space: nowrap; }
.probe-table .probe-snippet {
  max-width: 360px; word-break: break-word;
  white-space: pre-wrap;
}
.probe-verdict {
  font: 700 9px/1 ui-monospace, monospace;
  padding: 2px 5px; border-radius: 3px; white-space: nowrap;
}
.probe-verdict.pass { background: #f0faf4; color: #1b5e20; border: 1px solid #4caf50; }
.probe-verdict.fail { background: #fff5f5; color: #b71c1c; border: 1px solid #ef5350; }
.probe-verdict.partial { background: #fffbeb; color: #78350f; border: 1px solid #d97706; }
.probe-empty {
  font-size: 11px; color: #718096; font-style: italic;
  padding: 6px 0;
}
.rem-block {
  margin-top: 10px; padding: 10px 12px;
  background: #fff7ed; border: 1px solid #fdba74; border-radius: 6px;
}
.rem-title {
  font: 700 11px/1 ui-monospace, Menlo, monospace; color: #9a3412;
  text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 6px;
}
.rem-summary {
  font-size: 11px; color: #7c2d12; margin-bottom: 6px; line-height: 1.45;
}
.rem-list {
  list-style: decimal; padding-left: 18px; font-size: 11px; color: #7c2d12;
  line-height: 1.5;
}
.rem-list li { margin: 3px 0; }
.rem-name { font-weight: 700; color: #5b1d05; }
.rem-note { color: #7c2d12; }
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
@media print { body { background: #fff; } .report { box-shadow: none; } }
"""


def _audit_str(val: Any) -> str:
    """Render a forensic-stamp value for the audit footer.

    Returns the value's string form when present; renders an em-dash when the
    field is None or empty. The four contract-required stamps (scanner_version,
    corpus_version, seed, model_versions) are surfaced on the card so the
    footer is the full, byte-stable projection of the are-scanner-api-v1.md
    minimum-field set rather than a compressed stand-in.
    """
    if val is None:
        return "\u2014"
    s = str(val)
    return s if s else "\u2014"


def _format_model_versions(mv: dict[str, Any] | None) -> str:
    if not mv:
        return "\u2014"
    primary = mv.get("primary")
    judges = mv.get("judges") or []
    parts: list[str] = []
    if primary:
        parts.append(f"primary={primary}")
    if judges:
        parts.append(f"judges={','.join(str(j) for j in judges)}")
    return "; ".join(parts) if parts else "\u2014"


def _caveat_block(score_response: dict[str, Any]) -> str:
    """Render the LOI-stage caveat block from CAVEAT_TEMPLATE.

    BEAAA-390 / BEAAA-388. Substitutes `rubric_version`, `contract_version`,
    and `scanner_version` from the ScoreResponse — never hard-coded. Renders
    above the hero score band. Format: heading + paragraphs + versions line.
    """
    rubric_version = _audit_str(score_response.get("rubric_version"))
    contract_version = _audit_str(score_response.get("contract_version"))
    scanner_version = _audit_str(score_response.get("scanner_version"))
    body = CAVEAT_TEMPLATE.format(
        rubric_version=rubric_version,
        contract_version=contract_version,
        scanner_version=scanner_version,
    )
    paragraphs = body.split("\n\n")
    if not paragraphs:
        return ""
    head_para = paragraphs[0].strip()
    # First paragraph is "**About this score**" — render as <h3>.
    if head_para.startswith("**") and head_para.endswith("**"):
        heading = escape(head_para[2:-2])
    else:
        heading = escape(head_para)
    rest = paragraphs[1:]
    # Last paragraph is the versions line; render as monospace strip.
    versions_html = ""
    body_paras = rest
    if rest and rest[-1].lstrip().startswith("Rubric:"):
        versions_html = (
            f'<div class="caveat-versions">{escape(rest[-1].strip())}</div>'
        )
        body_paras = rest[:-1]
    paras_html = "".join(
        f"<p>{escape(p.strip())}</p>" for p in body_paras if p.strip()
    )
    return (
        f'<div class="caveat-block">'
        f"<h3>{heading}</h3>"
        f"{paras_html}"
        f"{versions_html}"
        f"</div>"
    )


def _pillar_pct_color(pct: float) -> str:
    if pct >= 70:
        return "#2e7d32"
    if pct >= 50:
        return "#1565c0"
    if pct >= 35:
        return "#c67c00"
    return "#b71c1c"


def _provenance_block(
    provenance: str | None,
    dim_provenance: dict[str, str] | None,
) -> str:
    """Render the provenance banner per BEAAA-130 rec #2."""
    dim_counter = ""
    if dim_provenance:
        n_measured = sum(
            1 for v in dim_provenance.values() if v.startswith("measured")
        )
        n_prior = sum(1 for v in dim_provenance.values() if v == "prior")
        if n_measured or n_prior:
            dim_counter = f' <span class="prov-detail">{n_measured} measured / {n_prior} prior dim(s)</span>'

    if provenance is None:
        return (
            '<div class="prov-banner prov-missing">'
            '<span class="prov-badge">PROVENANCE NOT SET</span>'
            '<span class="prov-detail">Scan provenance is unset. '
            "Do not distribute without provenance label.</span>"
            f"{dim_counter}</div>"
        )

    prov_lower = provenance.lower()
    head, _, detail = provenance.partition(" \u2014 ")
    if not detail:
        head, _, detail = provenance.partition(" - ")

    if prov_lower == "prior" or prov_lower.startswith("prior") or prov_lower.startswith("directional"):
        detail_html = escape(detail) if detail else "Category-level prior estimate. Not based on a customer-specific scan."
        return (
            '<div class="prov-banner prov-prior">'
            '<span class="prov-badge">DIRECTIONAL PRIOR</span>'
            f'<span class="prov-detail">{detail_html}</span>'
            f"{dim_counter}</div>"
        )

    if prov_lower.startswith("measured"):
        badge_text = "MEASURED"
        css_class = "prov-measured"
        detail_html = escape(detail) if detail else "Direct customer-deployment scan."
    elif prov_lower.startswith("category-proxy:"):
        badge_text = escape(head.upper())
        css_class = "prov-proxy"
        detail_html = escape(detail) if detail else ""
    else:
        return (
            '<div class="prov-banner prov-missing">'
            '<span class="prov-badge">PROVENANCE UNRECOGNIZED</span>'
            f'<span class="prov-detail">{escape(provenance)}</span>'
            f"{dim_counter}</div>"
        )

    detail_span = f'<span class="prov-detail">{detail_html}</span>' if detail_html else ""
    return (
        f'<div class="prov-banner {css_class}">'
        f'<span class="prov-badge">{badge_text}</span>'
        f"{detail_span}"
        f"{dim_counter}</div>"
    )


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
            # BEAAA-198 N/A dims (applicable=False) emit raw=None and are
            # excluded from the pillar mean; the bind_blocking_floor check
            # is non-applicable for them. Skip rather than coercing None to
            # 0.0, which would falsely trip the floor.
            if entry.get("applicable") is False or entry.get("raw") is None:
                continue
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
                if entry.get("applicable") is False or entry.get("raw") is None:
                    continue
                raw = float(entry.get("raw", 0))
                if raw < min_dim_raw:
                    reasons.append(
                        f"{pid} weak-link: {did} raw={raw:.2f} < min_dim_raw={min_dim_raw:.2f}"
                    )

    return {"eligible": not reasons, "reasons": reasons}


def _compute_display_weighted(
    pillars: dict[str, Any],
    rubric: Rubric,
    display_pillar_weights: dict[str, int],
) -> tuple[int, dict[str, Any], dict[str, Any]]:
    """Recompute pillar scores and ARE total using display_pillar_weights.

    Presentation-layer remap: same dim raws, different pillar contributions.
    Returns (display_bmi, display_band_dict, display_pillars).
    """
    from rubric.bands import assign_band, band_to_dict

    total = 0.0
    new_pillars: dict[str, Any] = {}
    for pid, pdata in pillars.items():
        disp_w = display_pillar_weights[pid]
        # BEAAA-14448 F13: normalize over MEASURED dims only, mirroring the
        # engine (rubric/scoring.py ~L294): raws excludes N/A dims
        # (raw=None / applicable=False). Averaging over all dims would both
        # crash on float(None) and diverge from the priced score. An all-N/A
        # pillar has no active dim to mean -> score 0.0 (engine L296-302).
        raws = [
            float(pdata["dimensions"][did]["raw"])
            for did in rubric.pillars[pid]["dim_ids"]
            if pdata["dimensions"][did].get("applicable") is not False
            and pdata["dimensions"][did].get("raw") is not None
        ]
        if raws:
            display_score = (sum(raws) / len(raws)) / 4.0 * disp_w
        else:
            display_score = 0.0
        total += display_score
        new_pillars[pid] = dict(pdata)
        new_pillars[pid]["score"] = round(display_score, 4)
        new_pillars[pid]["weight"] = disp_w
    new_bmi = int(round(total))
    new_band = band_to_dict(assign_band(new_bmi))
    return new_bmi, new_band, new_pillars


def _cover_label_from_p1_6(score_response: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Report-card cover label per BEAAA-209 v0.2.4 (Path B label gate).

    Reads coverage_breadth_pct + evidence_path off the P1.6 dim block
    and resolves "ARE Score" vs "ARE Spot-Scan" via
    eval_coverage_aggregator.label_for. Falls back to "ARE Spot-Scan"
    when the v0.2.4 fields are absent (pre-v0.2.4 cards, Path C scans
    without a Path-B aggregator pass) — anti-over-claim default.
    """
    from harness.bench.eval_coverage_aggregator import label_for
    p1 = score_response.get("pillars", {}).get("OPS", {})
    p1_6_block = p1.get("dimensions", {}).get("P1.6") or {}
    pct = p1_6_block.get("coverage_breadth_pct")
    path = p1_6_block.get("evidence_path")
    if pct is None or path is None:
        return "ARE Spot-Scan", p1_6_block
    return label_for(float(pct), str(path)), p1_6_block


def _has_coverage_gate_metadata(p1_6_block: dict[str, Any]) -> bool:
    return (
        p1_6_block.get("coverage_breadth_pct") is not None
        and p1_6_block.get("evidence_path") is not None
    )


def render(
    score_response: dict[str, Any],
    rubric: Rubric,
    *,
    provenance: str | None = None,
    dim_provenance: dict[str, str] | None = None,
    include_sensitivity: bool = True,
    probe_records: dict[str, list[dict[str, Any]]] | None = None,
    reproducibility: dict[str, Any] | None = None,
    prior_score_response: dict[str, Any] | None = None,
    remediation_threshold: float = 2.5,
    display_pillar_weights: dict[str, int] | None = None,
    admittance_decision: dict[str, Any] | None = None,
    quote_status: str | None = None,
) -> str:
    """Render a ScoreResponse to a single self-contained HTML document.

    `probe_records` maps dim_id → list of records, where each record is a
    dict with keys `item_id`, `prompt`, `response`, `verdict` (`pass` /
    `fail` / `partial`), and optional `evidence_uri`. Long prompt/response
    values are truncated at 240 chars in the rendered table; the full
    record is expected to live in `forensic_bundle_uri`.
    """
    # BEAAA-221: audit-trail hygiene — block placeholder values from ever
    # reaching a rendered card. Applies to every caller, not just Syllable.
    _assert_no_audit_placeholders(
        score_response["snapshot_hash"],
        score_response["forensic_bundle_uri"],
    )

    # Presentation-layer pillar remap (BEAAA-352 contract v1.0 §pillars).
    # When display_pillar_weights is supplied the rendered ARE, band, and pillar
    # cards use the remapped values. Bind eligibility and all audit-trail stamps
    # carry over unchanged from the original score_response.
    if display_pillar_weights is not None:
        _d_bmi, _d_band, _d_pillars = _compute_display_weighted(
            score_response["pillars"], rubric, display_pillar_weights
        )
        _display_sr: dict[str, Any] = {
            **score_response,
            "bmi": _d_bmi,
            "band": _d_band,
            "pillars": _d_pillars,
        }
    else:
        _display_sr = score_response

    bmi = int(_display_sr["bmi"])
    band = _display_sr["band"]
    pillars = _display_sr["pillars"]
    pillar_floors = score_response.get("pillar_floors") or {}
    age_days = int(score_response.get("scan_evidence_age_days", 0))
    scored_at = escape(score_response["scored_at"])

    band_letter = band["letter"]
    band_color = _BAND_COLOR.get(band_letter, "#444")
    band_tier = escape(_BAND_TIER.get(band_letter, ""))
    eligibility = (
        _eligibility_from_admittance(admittance_decision, quote_status)
        if admittance_decision is not None
        else evaluate_bind_eligibility(score_response)
    )
    score_deg = f"{(bmi / 100) * 360:.1f}deg"

    # BEAAA-209 v0.2.4: cover-label gate. Reads P1.6.coverage_breadth_pct
    # + P1.6.evidence_path. "ARE Score" only when Path B coverage ≥ 80%;
    # otherwise "ARE Spot-Scan". Anti-over-claim governor per BEAAA-193.
    cover_label, _p1_6_block = _cover_label_from_p1_6(score_response)
    coverage_aware_partial = (
        _has_coverage_gate_metadata(_p1_6_block) and cover_label != "ARE Score"
    )
    prov_html = _provenance_block(provenance, dim_provenance)
    # BEAAA-390 / BEAAA-388: LOI-stage caveat above the score band. Sources
    # rubric_version + contract_version + scanner_version from the underlying
    # ScoreResponse (NOT the display-remapped clone) so the versions stamped
    # in the caveat always match the audit trail at the foot of the card.
    caveat_html = _caveat_block(score_response)
    rubric_source_html = _rubric_source_block(rubric)
    diff = None
    if prior_score_response is not None:
        from report_card.diff import compute_diff
        diff = compute_diff(score_response, prior_score_response)
    pillar_cards_html = "".join(
        _pillar_card(pid, pillars[pid], pillar_floors.get(pid) or {}, rubric.pillars[pid])
        for pid in pillars
    )
    pillar_sections_html = "".join(
        _pillar_section(
            pid,
            pillars[pid],
            rubric.pillars[pid],
            dim_frameworks=rubric.dim_frameworks,
            probe_records=probe_records,
            dim_remediation=rubric.dim_remediation,
            remediation_threshold=remediation_threshold,
            diff=diff,
        )
        for pid in pillars
    )
    sensitivity_html = (
        render_sensitivity_block(_display_sr)
        if include_sensitivity and not coverage_aware_partial
        else ""
    )
    repro_html = render_reproducibility_block(reproducibility) if reproducibility else ""
    diff_html = render_diff_block(diff) if diff else ""

    # Build the display_weights audit-trail item for contract-v1.0 re-renders.
    display_weights_html = ""
    if display_pillar_weights is not None:
        _wvals = "/".join(str(display_pillar_weights[p]) for p in sorted(display_pillar_weights))
        display_weights_html = (
            f'        <div class="audit-item">\n'
            f'          <span class="audit-key">Display weights</span>\n'
            f'          <span class="audit-val">contract-v1.0-\u00a7pillars ({_wvals}); '
            f'underlying rubric_version: {escape(score_response["rubric_version"])}</span>\n'
            f"        </div>\n"
        )

    age_warning = ""
    if age_days >= STALE_SCAN_DAYS:
        age_warning = (
            f' <span class="stale-warn">[STALE: scan \u2265 {STALE_SCAN_DAYS} days; '
            f"claims trip-wire fires]</span>"
        )

    coverage_pct = _p1_6_block.get("coverage_breadth_pct")
    if coverage_pct is not None:
        coverage_meta = f"measured surface = {float(coverage_pct):.1f}%"
    else:
        coverage_meta = "measured surface = partial"

    if not coverage_aware_partial:
        title_suffix = f"({bmi}/100)"
        score_denominator_html = '<span class="score-denom">/100</span>'
        band_block_html = (
            f'      <div class="band-block">\n'
            f'        <div class="band-badge">\n'
            f'          <span class="band-letter">{escape(band["display"])}</span>\n'
            f'          <span class="band-tier">{band_tier}</span>\n'
            f"        </div>\n"
            f'        <div class="band-meta">\u00d7{band["modifier_factor"]:.2f} pricing'
            f' \u00b7 bands v{escape(str(band["bands_version"]))}</div>\n'
            f"      </div>\n"
        )
        hero_stat_html = (
            f'        <div class="posture-label">{escape(band["posture"])}</div>\n'
            f'        <span class="pricing-tag">modifier_factor = \u00d7{band["modifier_factor"]:.2f}</span>\n'
        )
    else:
        title_suffix = "(partial coverage)"
        score_denominator_html = '<span class="score-denom">measured surface</span>'
        band_block_html = (
            f'      <div class="band-block">\n'
            f'        <div class="band-meta">No full-coverage band on Spot-Scan</div>\n'
            f"      </div>\n"
        )
        hero_stat_html = (
            f'        <div class="posture-label">Measured aggregate only</div>\n'
            f'        <span class="pricing-tag">{escape(coverage_meta)}</span>\n'
        )

    return (
        f"<!DOCTYPE html>\n"
        f'<html lang="en">\n'
        f"<head>\n"
        f'<meta charset="utf-8" />\n'
        f"<title>{escape(cover_label)} \u2014 {escape(title_suffix)}</title>\n"
        f"<style>{_STYLE}</style>\n"
        f"</head>\n"
        f"<body>\n"
        f'<div class="report" style="--band-color:{band_color};">\n'
        f'  <div class="report-accent"></div>\n'
        f'  <div class="report-header">\n'
        f"    <div>\n"
        f'      <div class="report-brand">ARE Insurance</div>\n'
        f'      <div class="report-title">{escape(cover_label)} \u2014 Agent Risk Exposure Report Card</div>\n'
        f"    </div>\n"
        f'    <div class="report-meta">\n'
        f"      Scored: {scored_at}<br>\n"
        f"      Rubric v{escape(score_response['rubric_version'])}\n"
        f"    </div>\n"
        f"  </div>\n"
        f'  <div class="report-body">\n'
        f"\n"
        f"    {caveat_html}\n"
        f"\n"
        f'    <div class="hero">\n'
        f'      <div class="score-ring" style="--score-deg:{score_deg};">\n'
        f'        <div class="score-inner">\n'
        f'          <span class="score-number">{bmi}</span>\n'
        f"          {score_denominator_html}\n"
        f"        </div>\n"
        f"      </div>\n"
        f"{band_block_html}"
        f'      <div class="hero-stats">\n'
        f"{hero_stat_html}"
        f"      </div>\n"
        f"    </div>\n"
        f"\n"
        f"    {prov_html}\n"
        f"\n"
        f"    {_eligibility_block(eligibility)}\n"
        f"\n"
        f"    {rubric_source_html}"
        f"\n"
        f"    {diff_html}"
        f"\n"
        f"    {repro_html}"
        f"\n"
        f'    <div class="section-title">Pillar Breakdown</div>\n'
        f'    <div class="pillar-grid">\n'
        f"{pillar_cards_html}"
        f"    </div>\n"
        f"\n"
        f"    {sensitivity_html}"
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
        f'          <span class="audit-key">Contract version</span>\n'
        f'          <span class="audit-val">{escape(_audit_str(score_response.get("contract_version")))}</span>\n'
        f"        </div>\n"
        f'        <div class="audit-item">\n'
        f'          <span class="audit-key">Bands version</span>\n'
        f'          <span class="audit-val">{escape(str(band["bands_version"]))} (frozen 2026-04-30)</span>\n'
        f"        </div>\n"
        f'        <div class="audit-item">\n'
        f'          <span class="audit-key">Scanner version</span>\n'
        f'          <span class="audit-val">{escape(_audit_str(score_response.get("scanner_version")))}</span>\n'
        f"        </div>\n"
        f'        <div class="audit-item">\n'
        f'          <span class="audit-key">Corpus version</span>\n'
        f'          <span class="audit-val">{escape(_audit_str(score_response.get("corpus_version")))}</span>\n'
        f"        </div>\n"
        f'        <div class="audit-item">\n'
        f'          <span class="audit-key">Seed</span>\n'
        f'          <span class="audit-val">{escape(_audit_str(score_response.get("seed")))}</span>\n'
        f"        </div>\n"
        f'        <div class="audit-item">\n'
        f'          <span class="audit-key">Model versions</span>\n'
        f'          <span class="audit-val">{escape(_format_model_versions(score_response.get("model_versions")))}</span>\n'
        f"        </div>\n"
        f"{display_weights_html}"
        f"      </div>\n"
        f"    </div>\n"
        f"\n"
        f"  </div>\n"
        f"</div>\n"
        f"</body>\n"
        f"</html>\n"
    )


def _eligibility_block(eligibility: dict[str, Any]) -> str:
    status = eligibility.get("status", "pass" if eligibility.get("eligible") else "fail")
    if status == "refer":
        items = "".join(f"<li>{escape(r)}</li>" for r in eligibility.get("reasons", []))
        quote_line = ""
        if eligibility.get("quote_status"):
            quote_line = f'<strong>Quote status: {escape(str(eligibility["quote_status"]))}</strong>'
        return (
            '<div class="eligibility refer">'
            '<div class="verdict-badge">REFER_TO_UW</div>'
            '<div class="eligibility-body">'
            "<strong>Manual Underwriter referral required. Not eligible for automatic bind.</strong>"
            f"{quote_line}"
            f'<ul class="fail-reasons">{items}</ul>'
            "</div>"
            "</div>"
        )
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


def _eligibility_from_admittance(
    decision: dict[str, Any],
    quote_status: str | None,
) -> dict[str, Any]:
    admittance_class = str(decision.get("admittance_class", "")).upper()
    rule_id = str(decision.get("firing_rule_id") or decision.get("rule_id") or "")
    rationale = str(decision.get("rationale") or decision.get("reason") or "")
    reasons = []
    if rule_id:
        reasons.append(f"{rule_id}: {rationale}" if rationale else rule_id)
    elif rationale:
        reasons.append(rationale)
    if admittance_class == "REFER_TO_UW":
        return {
            "eligible": False,
            "status": "refer",
            "reasons": reasons,
            "quote_status": quote_status,
        }
    if admittance_class in {"AUTO_BIND", "CONSTRAINED_BIND", "BIND"}:
        return {"eligible": True, "status": "pass", "reasons": reasons}
    return {"eligible": False, "status": "fail", "reasons": reasons}


def _pillar_card(
    pid: str,
    pdata: dict[str, Any],
    floor: dict[str, Any],
    rubric_pillar: dict[str, Any],
) -> str:
    name = rubric_pillar["name"]
    score = float(pdata["score"])
    weight = int(pdata["weight"])
    dimensions = pdata.get("dimensions") or {}
    measured_count = sum(
        1
        for entry in dimensions.values()
        if entry.get("applicable") is not False and entry.get("raw") is not None
    )
    total_count = len(dimensions)
    has_measured_dim = measured_count > 0
    all_na = bool(dimensions) and not has_measured_dim
    # BEAAA-8398: a pillar with SOME (but not all) measured dims is "partial".
    # The engine re-normalizes the pillar score across the measured dims only
    # (rubric/scoring.py: uncovered/N/A dims are excluded from the mean, NOT
    # imputed as 0), so the score is honest — but a bare "X / 25" reads as a
    # full-coverage result. Surface the measured fraction so an uncovered dim
    # is never mistaken for a scored 0.
    partial = bool(dimensions) and 0 < measured_count < total_count
    pct = (score / weight) * 100 if weight else 0
    fill_pct = max(0.0, min(100.0, pct))
    color = _pillar_pct_color(pct)

    floor_html = ""
    if floor and not all_na:
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

    purpose = rubric_pillar.get("purpose") or ""
    pack_sources = rubric_pillar.get("pack_sources") or []
    raw_mapping = rubric_pillar.get("raw_mapping") or ""

    purpose_html = (
        f'        <div class="pillar-purpose">{escape(purpose)}</div>\n' if purpose else ""
    )
    chips_html = ""
    if pack_sources:
        chips = "".join(
            f'<span class="pack-chip">{escape(str(p))}</span>' for p in pack_sources
        )
        chips_html = f'        <div class="pack-chips">{chips}</div>\n'
    mapping_html = (
        f'        <div class="pillar-mapping">{escape(raw_mapping)}</div>\n'
        if raw_mapping
        else ""
    )
    if all_na:
        score_html = (
            f'        <div class="pillar-score-row">\n'
            f'          <span class="pillar-na-badge">N/A</span>\n'
            f'          <span class="pillar-na-note">No measured dimensions</span>\n'
            f"        </div>\n"
        )
        bar_html = ""
    else:
        partial_badge = (
            f'          <span class="pillar-partial-badge">PARTIAL</span>\n'
            if partial
            else ""
        )
        score_html = (
            f'        <div class="pillar-score-row">\n'
            f'          <span class="pillar-score-val">{score:.2f}</span>'
            f'<span class="pillar-score-wt"> / {weight}</span>\n'
            f'          <span class="pillar-pct">{pct:.1f}%</span>\n'
            f"{partial_badge}"
            f"        </div>\n"
        )
        if partial:
            score_html += (
                f'        <div class="pillar-partial-note">'
                f"{measured_count} of {total_count} dims measured "
                f"· score normalized over measured dims only; "
                f"uncovered dims are not scored (not counted as 0)"
                f"</div>\n"
            )
        bar_html = (
            f'        <div class="progress-track">'
            f'<div class="progress-fill" style="width:{fill_pct:.1f}%;"></div></div>\n'
        )

    return (
        f'      <div class="pillar-card" style="--pillar-color:{color};">\n'
        f'        <div class="pillar-id">{escape(pid)}</div>\n'
        f'        <div class="pillar-name">{escape(name)}</div>\n'
        f"{score_html}"
        f"{bar_html}"
        f"        {floor_html}\n"
        f"{purpose_html}"
        f"{chips_html}"
        f"{mapping_html}"
        f"      </div>\n"
    )


def _pillar_section(
    pid: str,
    pdata: dict[str, Any],
    rubric_pillar: dict[str, Any],
    *,
    dim_frameworks: dict[str, dict[str, list[str]]] | None = None,
    probe_records: dict[str, list[dict[str, Any]]] | None = None,
    dim_remediation: dict[str, dict[str, Any]] | None = None,
    remediation_threshold: float = 2.5,
    diff: dict[str, Any] | None = None,
) -> str:
    name = rubric_pillar["name"]
    dim_names = rubric_pillar.get("dim_names", {})
    score_val = float(pdata["score"])
    weight = int(pdata["weight"])
    pct = (score_val / weight) * 100 if weight else 0
    color = _pillar_pct_color(pct)
    dim_frameworks = dim_frameworks or {}
    probe_records = probe_records or {}
    dim_remediation = dim_remediation or {}

    rows = []
    for did in rubric_pillar["dim_ids"]:
        entry = pdata["dimensions"].get(did, {})
        applicable = entry.get("applicable") is not False and entry.get("raw") is not None
        raw = float(entry.get("raw", 0)) if applicable else None
        dim_pct = (raw / 4) * 100 if raw is not None else 0.0
        dim_fill = max(0.0, min(100.0, dim_pct))
        dim_name = escape(dim_names.get(did, ""))

        warn = ""
        thr = entry.get("applied_threshold") or {}
        if raw is not None and thr.get("kind") == "bind_blocking_floor" and raw < float(thr.get("min_raw", 0)):
            warn = '<span class="warn-badge">BIND BLOCKED</span>'

        fw_html = _framework_badges(dim_frameworks.get(did))
        delta_html = render_dim_delta_pill(diff, did)
        below_threshold = raw is not None and raw < remediation_threshold
        if not applicable:
            justification = entry.get("na_justification") or "Not applicable to this scan."
            warn = f'<span class="warn-badge" title="{escape(str(justification))}">N/A</span>'
        rem_html = (
            _remediation_block(did, dim_remediation.get(did))
            if (below_threshold and dim_remediation.get(did))
            else ""
        )
        probe_html = _probe_drilldown(
            did,
            probe_records.get(did),
            remediation_html=rem_html,
        )

        rows.append(
            f"          <tr>\n"
            f'            <td class="dim-id-col">{escape(did)}</td>\n'
            f'            <td class="dim-name-col">{dim_name}{fw_html}{warn}'
            f"{probe_html}</td>\n"
            f'            <td class="dim-raw-col">{("N/A" if raw is None else f"{raw:.1f}/4")}{delta_html}</td>\n'
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


_FRAMEWORK_LABEL = {
    "owasp_llm": ("OWASP", "fw-owasp"),
    "nist_ai_rmf": ("NIST", "fw-nist"),
    "mitre_atlas": ("MITRE", "fw-mitre"),
}


def _framework_badges(frameworks: dict[str, list[str]] | None) -> str:
    if not frameworks:
        return ""
    badges: list[str] = []
    # Stable order for reproducibility.
    for key in ("owasp_llm", "nist_ai_rmf", "mitre_atlas"):
        ids = frameworks.get(key) or []
        if not ids:
            continue
        label, css = _FRAMEWORK_LABEL[key]
        for fid in ids:
            badges.append(
                f'<span class="fw-badge {css}" title="{escape(label)} {escape(fid)}">'
                f"{escape(fid)}</span>"
            )
    if not badges:
        return ""
    return '<span class="fw-badges">' + "".join(badges) + "</span>"


_PROBE_TRUNC = 240


def _trunc(text: str, n: int = _PROBE_TRUNC) -> str:
    if not text:
        return ""
    if len(text) <= n:
        return text
    return text[: max(0, n - 1)] + "\u2026"


def _probe_drilldown(
    dim_id: str,
    records: list[dict[str, Any]] | None,
    *,
    remediation_html: str = "",
) -> str:
    if records is None and not remediation_html:
        return ""
    if records is None:
        # Remediation only — no probe records — still render an expandable
        # details so the playbook is reachable from the dim row.
        return (
            f'<details class="probe-drilldown"><summary>Recommended mitigations '
            f"({escape(dim_id)})</summary>{remediation_html}</details>"
        )
    if not records:
        empty_body = '<div class="probe-empty">No probe records attached for this dim.</div>'
        return (
            f'<details class="probe-drilldown"><summary>Probe records '
            f"({escape(dim_id)})</summary>{empty_body}{remediation_html}</details>"
        )
    rows = []
    for rec in records:
        item_id = escape(str(rec.get("item_id", "")))
        prompt = escape(_trunc(str(rec.get("prompt", ""))))
        response = escape(_trunc(str(rec.get("response", ""))))
        verdict_raw = str(rec.get("verdict", "")).lower()
        verdict_class = (
            "pass" if verdict_raw == "pass"
            else "fail" if verdict_raw == "fail"
            else "partial"
        )
        verdict_html = (
            f'<span class="probe-verdict {verdict_class}">'
            f"{escape(verdict_raw.upper() or EM_DASH)}</span>"
        )
        rows.append(
            f"          <tr>\n"
            f'            <td class="probe-id">{item_id}</td>\n'
            f'            <td class="probe-snippet">{prompt}</td>\n'
            f'            <td class="probe-snippet">{response}</td>\n'
            f"            <td>{verdict_html}</td>\n"
            f"          </tr>\n"
        )
    n = len(records)
    n_fail = sum(1 for r in records if str(r.get("verdict", "")).lower() == "fail")
    return (
        f'<details class="probe-drilldown">'
        f'<summary>Probe records ({escape(dim_id)}) \u2014 {n} probe(s), '
        f"{n_fail} failed</summary>"
        f'<table class="probe-table">'
        f"<thead><tr><th>Item</th><th>Prompt</th><th>Response</th>"
        f"<th>Verdict</th></tr></thead>"
        f'<tbody>{"".join(rows)}</tbody>'
        f"</table>"
        f"{remediation_html}"
        f"</details>"
    )


def _remediation_block(dim_id: str, remediation: dict[str, Any] | None) -> str:
    """Render the per-dim remediation playbook inside a drilldown details."""
    if not remediation:
        return ""
    mitigations = remediation.get("mitigations") or []
    if not mitigations:
        return ""
    items = []
    for m in mitigations[:3]:
        name = escape(str(m.get("name", "")))
        note = escape(str(m.get("note", "")))
        items.append(
            f'<li><span class="rem-name">{name}</span>'
            f'<span class="rem-note"> \u2014 {note}</span></li>'
        )
    summary = remediation.get("summary") or ""
    summary_html = (
        f'<div class="rem-summary">{escape(str(summary))}</div>' if summary else ""
    )
    return (
        f'<div class="rem-block">'
        f'<div class="rem-title">Recommended mitigations \u2014 {escape(dim_id)}</div>'
        f"{summary_html}"
        f'<ol class="rem-list">{"".join(items)}</ol>'
        f"</div>"
    )


def _rubric_source_block(rubric: Rubric) -> str:
    prov = rubric.provenance or {}
    if not prov:
        return ""
    rows: list[str] = []
    label_map = [
        ("source_of_authority", "Source of authority"),
        ("weight_authority", "Weight authority"),
        ("raw_score_convention", "Raw score convention"),
        ("governance", "Governance"),
    ]
    for key, label in label_map:
        val = prov.get(key)
        if not val:
            continue
        rows.append(
            f"<dt>{escape(label)}</dt><dd>{escape(str(val))}</dd>"
        )
    if not rows:
        return ""
    return (
        f'<div class="rubric-source">\n'
        f"<h3>Rubric Source &amp; Methodology</h3>\n"
        f'<dl>{"".join(rows)}</dl>\n'
        f"</div>\n"
    )
