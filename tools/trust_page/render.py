"""Trust Page v1 renderer — data-driven (BEAAA-11926 / WI-3).

Renders a VerificationRecord to a single-file HTML page (no external assets/JS),
replacing the illustrative static prototype (BEAAA-11917) with live fields. All
copy uses the Legal-Coordinator-approved wording from the WI-3 wording review
(BEAAA-11927): prohibited insurance terms are absent, warranty language is
remedy-*eligibility* only, and the stale state is rendered as a feature.

Guards (fail-closed):
  * ``render_html`` raises if the reproducibility envelope is incomplete.
  * ``render_html`` raises if any prohibited term (§3 red lines) appears in output.

The visual is harmonized to the BEAAA-11928 locked design system (navy verification
card, Copper Law, words-first band ladder, freshness lifecycle timeline) WITHOUT
touching the data wiring or the reproducibility envelope (BEAAA-11942, WI-3). The
rendered surface remains INTERNAL until (1) a real partner intake scan on the locked
rubric and (2) outside-counsel sign-off complete; every render carries a non-production
watermark and the brand-backbone metadata block. A render built from a golden
calibration fixture is NOT external production proof (Trust Page spec Red Line 6).
"""

from __future__ import annotations

import html
import json
import re
from datetime import datetime

from .verification_record import VerificationRecord

BRAND_BACKBONE_VERSION = "v1.6"
BRAND_BACKBONE_SHA256 = "a2ffc4f919b0f71a17b9f4506ac4edf11cd37956ff494f00d7a45f0b166ea090"

# §3 red lines (Canon §10) — prohibited on this external-bound surface.
# "claim" is intentionally excluded from the automated guard to avoid false
# positives; it is absent from all approved copy below.
PROHIBITED_TERMS = (
    "insurance", "insured", "policy", "policies", "coverage", "covered",
    "premium", "premiums", "bind", "bound", "binder", "activate coverage",
)

# Legal-approved (BEAAA-11927) fixed copy blocks.
COPY = {
    "means_bullet_2": "The band is a point-in-time verification read that a buyer can use in an approval decision.",
    "not_mean_bullet_1": "This page is not financial protection in force and does not create a warranty remedy.",
    "warranty_disclaimer": (
        "Warranty remedy eligibility is a status, not a warranty in force. A warranty remedy can attach only "
        "once counsel, eligibility, monitoring-continuity, caps, and reserve conditions are all satisfied. "
        "This page does not create, confirm, or activate any warranty."
    ),
    "footer": (
        "BackBond private trust page · v1 (BEAAA-11926 / WI-3). "
        "Copy remains subject to Legal Coordinator and outside-counsel review before partner send."
    ),
    "gallery_current": "Verified and current. Well before next required verification. Eligibility may remain available.",
    "gallery_aging": "Verified; approaching next verification. Countdown shown. Eligibility may remain available.",
    "gallery_stale": "Verification has lapsed — this band is no longer current. Shown, not hidden. Eligibility suspended.",
    "gallery_expired": "Verification expired. Historical band shown greyed with lapse date. Not eligible until re-verified.",
}

# Words-first band ladder (BEAAA-11928 locked design, spec §1 field 3): the surface
# shows the band by WORD + letter, never a raw 0-100 number. The active cell is
# driven by the live band letter (record.band_display); the posture word for the
# active band comes from the score response (record.band_posture).
_BAND_LADDER = (
    ("A", "Preferred"),
    ("B", "Standard"),
    ("C", "Constrained"),
    ("D", "Decline"),
)

# Freshness lifecycle timeline (BEAAA-11928 Designer addition, spec §2): decay reads
# as an INFORMATION lifecycle, never a system error. The active stop is driven by the
# live stale_state. Stop labels/effects reuse canonical glossary vocabulary and match
# the Legal-reviewed (BEAAA-11927) freshness copy; net-new structural labels carry the
# BEAAA-11928 re-confirm flag to Legal.
_TIMELINE = (
    ("current", "Current", "Well before next verification. Eligibility may remain available."),
    ("aging", "Aging", "Approaching next verification. Countdown shown."),
    ("due_soon", "Due soon", "Within the grace window. Action prompt shown."),
    ("stale", "Stale", "Past due. Band no longer current. Eligibility suspended."),
    ("expired", "Expired", "Historical band shown greyed with lapse date. Not eligible until re-verified."),
)

# stale_state -> (pill css class, human phrase, statebar line)
_STALE_UI = {
    "current": ("ok", "Verified — current", "This band reflects a point-in-time verification on a locked rubric. It is current."),
    "aging": ("aging", "Aging — before next verification", "This band is current; the next required verification is approaching."),
    "due_soon": ("aging", "Re-verification due soon", "This band is current; re-verification is due soon."),
    "stale": ("stale", "Stale — verification lapsed", "Verification has lapsed. This band is no longer current."),
    "expired": ("lapsed", "Expired — verification lapsed", "Verification expired. The historical band is shown with its lapse date."),
}

_CONTINUITY_UI = {
    "healthy": ("ok", "Healthy"),
    "grace": ("aging", "Grace — heartbeat aging"),
    "gap": ("stale", "Gap — sequence discontinuity"),
    "stale_ineligible": ("lapsed", "Stale — evidence not flowing"),
}

_WARRANTY_UI = {
    "eligible": ("ok", "Eligible"),
    "pending": ("aging", "Pending"),
    "not_eligible": ("lapsed", "Not eligible"),
    "suspended": ("stale", "Suspended (verification lapsed)"),
}


def _e(s: object) -> str:
    return html.escape(str(s))


def _fmt_ts(iso: str) -> str:
    dt = datetime.fromisoformat(iso)
    return dt.strftime("%Y-%m-%d %H:%M UTC")


def _fmt_date(iso: str) -> str:
    return datetime.fromisoformat(iso).strftime("%Y-%m-%d")


def _assert_no_prohibited(doc: str) -> None:
    low = doc.lower()
    hits = sorted({t for t in PROHIBITED_TERMS if re.search(rf"\b{re.escape(t)}\b", low)})
    if hits:
        raise ValueError(f"prohibited red-line term(s) in rendered output: {hits}")


def render_html(
    record: VerificationRecord, *, watermark: str, provenance_mode: str = "live"
) -> str:
    """Render a VerificationRecord to warranty-safe single-file HTML."""
    if provenance_mode not in {"live", "frozen_sample"}:
        raise ValueError(f"unsupported provenance_mode: {provenance_mode}")
    env = record.envelope
    missing = [k for k in ("scanner_version", "corpus_version", "rubric_version",
                           "rubric_locked_through", "seed", "bands_version") if env.get(k) is None]
    if missing:
        raise ValueError(f"cannot render: envelope missing {missing}")

    st_cls, st_phrase, st_line = _STALE_UI[record.stale_state]
    cont_cls, cont_lbl = _CONTINUITY_UI.get(record.monitoring_continuity, ("lapsed", record.monitoring_continuity))
    warr_cls, warr_lbl = _WARRANTY_UI.get(record.warranty_effect, ("aging", record.warranty_effect))

    rubric_line = (
        f"scanner v{_e(env['scanner_version'])} · "
        f"corpus {_e(env['corpus_version'] or '(none)')} · "
        f"rubric {_e(env['rubric_version'])} locked through {_e(env['rubric_locked_through'])} · "
        f"seed {_e(env['seed'])} · bands {_e(env['bands_version'])}"
    )
    envelope_comment = json.dumps({**env, "generated_at": record.generated_at,
                                   "warranty_source": record.warranty.get("source")}, indent=2, sort_keys=True)

    # Words-first band ladder: active cell driven by the live band letter.
    active_letter = (record.band_display or "").strip()[:1].upper()
    ladder_cells = "".join(
        f'<div class="cell{" active" if lt == active_letter else ""}">'
        f'<div class="nm">{_e(nm)}</div><div class="lt">{_e(lt)}</div></div>'
        for lt, nm in _BAND_LADDER
    )
    band_word = record.band_posture or next((nm for lt, nm in _BAND_LADDER if lt == active_letter), "")

    # Freshness lifecycle timeline: active stop driven by the live stale_state.
    active_idx = next((i for i, (s, _n, _e2) in enumerate(_TIMELINE) if s == record.stale_state), 0)
    stage_name = _TIMELINE[active_idx][1]
    prog_pct = round((active_idx + 0.5) / len(_TIMELINE) * 100, 1)
    stops_html = ""
    for i, (_s, nm, ef) in enumerate(_TIMELINE):
        cls = "past" if i < active_idx else ("here" if i == active_idx else "")
        youare = '<span class="youare">This record</span>' if i == active_idx else ""
        stops_html += (
            f'<div class="stop {cls}"><div class="dot"></div>'
            f'<div class="nm">{_e(nm)}</div><div class="ef">{_e(ef)}</div>{youare}</div>'
        )

    # BEAAA-11928 LOCKED design system (design-system/tokens/backbond-tokens.css
    # grammar): navy verification card, Copper Law (copper only on active band +
    # wordmark rule), words-first band ladder, freshness lifecycle timeline, and the
    # Archivo/Inter/IBM Plex Mono type stack. Freshness-state color is namespaced
    # (--fresh-*) and kept SEPARATE from band color so "bands are tonal, never
    # traffic-lights" holds. Fonts are named with system fallbacks (no @font-face
    # external assets) to preserve the single-file, no-external-asset guarantee.
    css = """
:root{--ink:#0B1A38;--body:#36425C;--slate:#535E77;--paper:#FFFFFF;--tint:#F5F7FB;
--tint-line:#DBE1EE;--rule:#E3E7EE;--on-navy:#FFFFFF;--on-navy-muted:#8A9BC0;--navy-line:#26375C;
--bond-on-dark:#D58E5C;--bond-on-light:#BD6A34;
--fresh-ok:#1f6d45;--fresh-ok-bg:#E9F3ED;--fresh-aging:#7c6412;--fresh-aging-bg:#F6EFD6;
--fresh-stale:#8a541a;--fresh-stale-bg:#F4E7D6;--fresh-exp:#5b6675;--fresh-exp-bg:#ECEFF3;
--f-display:"Archivo",system-ui,-apple-system,Segoe UI,sans-serif;
--f-body:"Inter",system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
--f-data:"IBM Plex Mono",ui-monospace,Menlo,Consolas,monospace;--radius:12px;--pill:6px;}
*{box-sizing:border-box}
body{margin:0;font-family:var(--f-body);font-size:16px;line-height:1.55;color:var(--ink);background:var(--tint);-webkit-font-smoothing:antialiased}
.watermark{background:#3a2a00;color:#ffd873;text-align:center;font-family:var(--f-data);font-size:12px;letter-spacing:.02em;padding:9px 14px;line-height:1.4}
.wrap{max-width:820px;margin:0 auto;padding:0 22px 64px}
.mast{display:flex;justify-content:space-between;align-items:center;padding:26px 0 18px;border-bottom:1px solid var(--rule)}
.wordmark{display:flex;flex-direction:column;gap:5px}
.wordmark .name{font-family:var(--f-display);font-weight:800;font-size:23px;letter-spacing:-.01em;color:var(--ink)}
.wordmark .rule{width:52px;height:2px;background:var(--bond-on-light);border-radius:2px}
.mast .kicker{font-family:var(--f-data);font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:var(--slate);text-align:right}
.conf{font-size:13px;color:var(--slate);margin:14px 0 0}
.eyebrow{font-family:var(--f-body);font-weight:700;font-size:11.5px;letter-spacing:.12em;text-transform:uppercase;color:var(--slate)}
.eyebrow .num{color:var(--bond-on-light);font-family:var(--f-data);font-weight:600;margin-right:7px}
.record{background:var(--ink);color:var(--on-navy);border-radius:var(--radius);padding:28px 30px 26px;margin:22px 0 0;box-shadow:0 2px 10px rgba(11,26,56,.12)}
.rec-top{display:flex;justify-content:space-between;align-items:flex-start;gap:22px;flex-wrap:wrap}
.rec-top .agent{font-family:var(--f-display);font-weight:800;font-size:26px;letter-spacing:-.01em;line-height:1.05}
.rec-top .vendor{color:var(--on-navy-muted);font-size:13.5px;margin-top:5px}
.bandblock{text-align:right}
.bandblock .lbl{font-family:var(--f-body);font-weight:700;font-size:10px;letter-spacing:.12em;text-transform:uppercase;color:var(--on-navy-muted)}
.bandchip{display:inline-block;margin-top:7px;background:var(--bond-on-dark);color:var(--ink);font-family:var(--f-display);font-weight:800;font-size:19px;padding:6px 15px;border-radius:var(--pill)}
.bandchip .ltr{font-family:var(--f-data);font-weight:600;font-size:15px;opacity:.75}
.ladder{display:grid;grid-template-columns:repeat(4,1fr);gap:1px;margin-top:20px;background:var(--navy-line);border:1px solid var(--navy-line);border-radius:var(--pill);overflow:hidden}
.cell{background:var(--ink);padding:11px 8px;text-align:center}
.cell .nm{font-weight:700;font-size:12.5px;color:var(--on-navy-muted)}
.cell .lt{font-family:var(--f-data);font-size:10px;color:var(--on-navy-muted);opacity:.8;margin-top:3px}
.cell.active{background:var(--bond-on-dark)}.cell.active .nm{color:var(--ink)}.cell.active .lt{color:var(--ink);opacity:.85}
.statebar{display:flex;gap:10px;align-items:flex-start;margin-top:20px;padding:13px 15px;border-radius:10px;font-size:14px}
.statebar .tick{flex:0 0 auto;width:9px;height:9px;border-radius:50%;margin-top:6px}.statebar b{font-weight:700}
.statebar.ok{background:var(--fresh-ok-bg);color:#173d29}.statebar.ok .tick{background:var(--fresh-ok)}
.statebar.aging{background:var(--fresh-aging-bg);color:#4a3d10}.statebar.aging .tick{background:var(--fresh-aging)}
.statebar.stale{background:var(--fresh-stale-bg);color:#4d3010}.statebar.stale .tick{background:var(--fresh-stale)}
.statebar.lapsed{background:var(--fresh-exp-bg);color:#3a4250}.statebar.lapsed .tick{background:var(--fresh-exp)}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:15px 26px;margin-top:22px}
.f{border-top:1px solid var(--navy-line);padding-top:11px}
.f .k{font-family:var(--f-body);font-weight:600;font-size:10.5px;letter-spacing:.06em;text-transform:uppercase;color:var(--on-navy-muted)}
.f .v{font-size:14.5px;margin-top:4px;color:var(--on-navy)}.f .v.mono{font-family:var(--f-data);font-size:12.5px;color:var(--on-navy-muted);line-height:1.5}
.pill{display:inline-block;padding:3px 11px;border-radius:999px;font-size:12px;font-weight:600;font-family:var(--f-body)}
.pill.ok{background:var(--fresh-ok-bg);color:var(--fresh-ok)}.pill.aging{background:var(--fresh-aging-bg);color:var(--fresh-aging)}
.pill.stale{background:var(--fresh-stale-bg);color:var(--fresh-stale)}.pill.lapsed{background:var(--fresh-exp-bg);color:var(--fresh-exp)}
.disc{font-size:12.5px;line-height:1.55;color:var(--on-navy-muted);margin-top:20px;border-left:2px solid var(--navy-line);padding-left:13px}
.disc b{color:#cdd6ea;font-weight:700}
h2.sec{font-family:var(--f-body);font-weight:700;font-size:12px;letter-spacing:.06em;text-transform:uppercase;color:var(--slate);margin:34px 0 12px}
.timeline-card{background:var(--paper);border:1px solid var(--tint-line);border-radius:var(--radius);padding:22px 24px 20px}
.tl-intro{font-size:13.5px;color:var(--body);margin:0 0 18px}
.track{position:relative;height:2px;background:var(--rule);margin:34px 8px 0}.track .prog{position:absolute;left:0;top:0;height:2px;background:var(--fresh-aging)}
.stops{display:grid;grid-template-columns:repeat(5,1fr);gap:0;margin-top:-42px}
.stop{text-align:center;position:relative}
.stop .dot{width:13px;height:13px;border-radius:50%;background:var(--paper);border:2px solid var(--rule);margin:0 auto;position:relative;top:22px}
.stop.past .dot{border-color:var(--fresh-aging);background:var(--fresh-aging)}
.stop.here .dot{width:17px;height:17px;top:20px;border-color:var(--fresh-aging);background:var(--paper);box-shadow:0 0 0 4px var(--fresh-aging-bg)}
.stop .nm{font-weight:700;font-size:12px;margin-top:34px;color:var(--body)}.stop.here .nm{color:var(--fresh-aging)}
.stop .ef{font-size:10.5px;line-height:1.35;color:var(--slate);margin-top:4px;padding:0 4px}
.youare{display:inline-block;font-family:var(--f-data);font-size:9px;letter-spacing:.12em;text-transform:uppercase;color:var(--fresh-aging);background:var(--fresh-aging-bg);padding:2px 7px;border-radius:999px;margin-top:6px}
.tl-note{font-size:12px;color:var(--slate);margin:20px 0 0;border-left:2px solid var(--tint-line);padding-left:12px;line-height:1.55}
.means{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.box{border:1px solid var(--tint-line);border-radius:var(--radius);padding:18px 20px;background:var(--paper)}
.box h3{margin:0 0 4px;font-size:14.5px;display:flex;align-items:center;gap:9px}
.box .mk{font-family:var(--f-data);font-weight:600;font-size:15px;line-height:1}.box.pos .mk{color:var(--fresh-ok)}.box.neg .mk{color:var(--slate)}
.box ul{margin:10px 0 0;padding-left:18px;color:var(--body);font-size:13.5px;line-height:1.5}.box li{margin:6px 0}.box b{color:var(--ink)}
.gallery{display:grid;grid-template-columns:repeat(2,1fr);gap:12px}
.mini{border:1px solid var(--tint-line);border-radius:10px;padding:14px 16px;background:var(--paper)}
.mini .ds{font-size:12.5px;color:var(--body);margin-top:9px;line-height:1.5}
.decay-note{font-size:12.5px;color:var(--slate);border-left:2px solid var(--tint-line);padding-left:13px;margin-top:16px;line-height:1.55}
footer{color:var(--slate);font-size:11.5px;line-height:1.5;margin-top:36px;border-top:1px solid var(--rule);padding-top:14px}
.meta{font-family:var(--f-data);font-size:10.5px;color:var(--slate);margin-top:8px}
@media(max-width:600px){.grid,.means,.gallery{grid-template-columns:1fr}.stop .ef{display:none}}
"""

    provenance_lines = (
        "Fields are LIVE-WIRED: band+envelope <- rubric.scoring.score() on locked rubric (WI-1);\n"
        "  monitoring continuity <- continuity_gate (WI-2); warranty eligibility <- WI-6 §2 rule."
        if provenance_mode == "live"
        else
        "Fields are rendered from the committed frozen historical record; they are not rescored by the current rubric.\n"
        "  Regenerate with build_sample --frozen-record to preserve the recorded scanner, corpus, rubric, and band."
    )

    doc = f"""<!DOCTYPE html>
<!--
  BackBond Trust Page — v1 (BEAAA-11926 wiring / BEAAA-11942 locked-visual harmonization, WI-3)
  persona_audience: internal. Rendered surface is external-facing and gated by
  Designer (60670b68) locked design [BEAAA-11928, applied here] + outside-counsel review before any partner send.
  brand_backbone_version: {BRAND_BACKBONE_VERSION}
  brand_backbone_sha256: {BRAND_BACKBONE_SHA256}
  brand_backbone_verified: true
  Visual harmonized to the BEAAA-11928 locked design system (navy card, Copper Law, words-first
  band ladder, freshness lifecycle timeline). Data wiring and reproducibility envelope UNCHANGED.
  {provenance_lines}
  Reproducibility envelope (Supreme Law):
{envelope_comment}
-->
<html lang="en"><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>BackBond Private Trust Page</title><style>{css}</style></head><body>
<div class="watermark">{_e(watermark)}</div>
<div class="wrap">

  <div class="mast">
    <div class="wordmark"><span class="name">BackBond</span><span class="rule"></span></div>
    <div class="kicker">Private Trust Page</div>
  </div>
  <div class="conf">Prepared for a single named recipient. Confidential to the vendor and the named enterprise reviewer.</div>

  <div class="eyebrow" style="margin-top:26px;"><span class="num">01</span>The verification record</div>
  <div class="record">
    <div class="rec-top">
      <div><div class="agent">{_e(record.agent_name)}</div><div class="vendor">Vendor: {_e(record.vendor_name)}</div></div>
      <div class="bandblock"><div class="lbl">BackBond Score band</div>
        <div class="bandchip">{_e(band_word)} <span class="ltr">{_e(record.band_display)}</span></div></div>
    </div>

    <div class="ladder">{ladder_cells}</div>

    <div class="statebar {st_cls}"><span class="tick"></span><div><b>{_e(st_phrase)}.</b> {_e(st_line)}</div></div>

    <div class="grid">
      <div class="f"><div class="k">Verified as of</div><div class="v mono">{_e(_fmt_ts(record.verified_as_of))}</div></div>
      <div class="f"><div class="k">Next required verification</div><div class="v mono">{_e(_fmt_date(record.next_required_verification))}</div></div>
      <div class="f"><div class="k">Verification freshness</div><div class="v"><span class="pill {st_cls}">{_e(st_phrase)}</span></div></div>
      <div class="f"><div class="k">Monitoring continuity</div><div class="v"><span class="pill {cont_cls}">{_e(cont_lbl)}</span></div></div>
      <div class="f"><div class="k">Warranty remedy eligibility</div><div class="v"><span class="pill {warr_cls}">{_e(warr_lbl)}</span></div></div>
      <div class="f"><div class="k">Scanner / corpus / rubric</div><div class="v mono">{rubric_line}</div></div>
    </div>

    <div class="disc"><b>{_e(COPY['warranty_disclaimer'])}</b></div>
  </div>

  <div class="eyebrow" style="margin-top:34px;"><span class="num">02</span>How this record reads over time</div>
  <h2 class="sec" style="margin-top:12px;">Verification freshness is a lifecycle - decay is expected, and always shown</h2>
  <div class="timeline-card">
    <p class="tl-intro">A verification record ages on a fixed clock. Each stage is shown in plain view so a buyer can judge freshness directly. This record sits at <b>{_e(stage_name)}</b>.</p>
    <div class="track"><div class="prog" style="width:{prog_pct}%"></div></div>
    <div class="stops">{stops_html}</div>
    <p class="tl-note">Decay is expected asset behavior, rendered as information (amber / grey), never as a system error. The verified-as-of and next-required timestamps are always visible so a buyer can judge freshness directly.</p>
  </div>

  <div class="eyebrow" style="margin-top:34px;"><span class="num">03</span>How to read this record</div>
  <h2 class="sec" style="margin-top:12px;">What this means / What this does not mean</h2>
  <div class="means">
    <div class="box pos"><h3><span class="mk">+</span> What this means</h3><ul>
      <li>This agent was verified against BackBond's scanner on a frozen rubric, at the timestamp shown.</li>
      <li>{_e(COPY['means_bullet_2'])}</li>
      <li>Monitoring continuity shows whether runtime evidence is still flowing since the last verification.</li>
    </ul></div>
    <div class="box neg"><h3><span class="mk">&minus;</span> What this does not mean</h3><ul>
      <li>{_e(COPY['not_mean_bullet_1'])}</li>
      <li>A band is not a guarantee of future agent behavior.</li>
      <li>Eligibility is not the same as an attached remedy; see the disclaimer above.</li>
    </ul></div>
  </div>

  <h2 class="sec">Stale state is a feature - how the record renders in each state</h2>
  <div class="gallery">
    <div class="mini"><span class="pill ok">Current</span><div class="ds">{_e(COPY['gallery_current'])}</div></div>
    <div class="mini"><span class="pill aging">Aging / due soon</span><div class="ds">{_e(COPY['gallery_aging'])}</div></div>
    <div class="mini"><span class="pill stale">Stale</span><div class="ds">{_e(COPY['gallery_stale'])}</div></div>
    <div class="mini"><span class="pill lapsed">Expired / lapsed</span><div class="ds">{_e(COPY['gallery_expired'])}</div></div>
  </div>
  <div class="decay-note">Decay is expected asset behavior, rendered as information (amber / grey), never as a system error. The verified-as-of and next-required timestamps are always visible so a buyer can judge freshness directly.</div>

  <footer>{_e(COPY['footer'])}
    <div class="meta">brand-backbone {BRAND_BACKBONE_VERSION} · sha {BRAND_BACKBONE_SHA256[:8]} · verified true · {rubric_line} · warranty source: {_e(record.warranty.get('source',''))}</div>
  </footer>
</div></body></html>"""

    _assert_no_prohibited(doc)
    return doc
