"""Trust Page v1 renderer — data-driven (BEAAA-11926 / WI-3).

Renders a VerificationRecord to a single-file HTML page (no external assets/JS),
replacing the illustrative static prototype (BEAAA-11917) with live fields. All
copy uses the Legal-Coordinator-approved wording from the WI-3 wording review
(BEAAA-11927): prohibited insurance terms are absent, warranty language is
remedy-*eligibility* only, and the stale state is rendered as a feature.

Guards (fail-closed):
  * ``render_html`` raises if the reproducibility envelope is incomplete.
  * ``render_html`` raises if any prohibited term (§3 red lines) appears in output.

The rendered surface remains INTERNAL until Designer UX (BEAAA-11928) and outside
counsel review complete; every render carries a non-production watermark and the
brand-backbone metadata block. A render built from a golden calibration fixture is
NOT external production proof (Trust Page spec Red Line 6).
"""

from __future__ import annotations

import html
import json
import re
from datetime import datetime

from .verification_record import VerificationRecord

BRAND_BACKBONE_VERSION = "v1.5"
BRAND_BACKBONE_SHA256 = "b1ae308e0f150ff5d0a6bdf05071ee98540e296bb3a86a4626b03041a266838f"

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


def render_html(record: VerificationRecord, *, watermark: str) -> str:
    """Render a VerificationRecord to warranty-safe single-file HTML."""
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

    css = """
:root{--ink:#12161c;--muted:#5b6675;--line:#e2e6ec;--bg:#f6f8fb;--card:#fff;--brand:#1b3a6b;
--ok:#1f7a4d;--ok-bg:#e8f5ee;--aging:#8a6d1f;--aging-bg:#fbf3d9;--stale:#9a5a12;--stale-bg:#fbeada;--lapsed:#5b6675;--lapsed-bg:#eef1f5;}
*{box-sizing:border-box}body{margin:0;font:15px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:var(--ink);background:var(--bg)}
.watermark{background:#3a2a00;color:#ffd873;text-align:center;font-size:12.5px;padding:7px 12px;letter-spacing:.02em}
.wrap{max-width:760px;margin:0 auto;padding:24px 18px 60px}
.brandline{display:flex;align-items:center;gap:10px;margin:14px 0 6px}.dot{width:22px;height:22px;border-radius:6px;background:var(--brand)}
.brandline b{font-size:17px}.sub{color:var(--muted);font-size:13px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:22px;margin:16px 0;box-shadow:0 1px 2px rgba(20,30,50,.04)}
.rowtop{display:flex;justify-content:space-between;align-items:flex-start;gap:16px;flex-wrap:wrap}h1{font-size:20px;margin:0 0 2px}
.vendor{color:var(--muted);font-size:14px}.band{text-align:center;min-width:110px}.band .letter{font-size:40px;font-weight:700;line-height:1;color:var(--brand)}
.band .lbl{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.08em;margin-top:4px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:14px 22px;margin-top:18px}.f{border-top:1px solid var(--line);padding-top:10px}
.f .k{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted)}.f .v{font-size:15px;margin-top:2px}
.pill{display:inline-block;padding:3px 10px;border-radius:999px;font-size:12.5px;font-weight:600}
.pill.ok{background:var(--ok-bg);color:var(--ok)}.pill.aging{background:var(--aging-bg);color:var(--aging)}
.pill.stale{background:var(--stale-bg);color:var(--stale)}.pill.lapsed{background:var(--lapsed-bg);color:var(--lapsed)}
.statebar{margin-top:16px;padding:12px 14px;border-radius:10px;font-size:14px}
.statebar.ok{background:var(--ok-bg);color:var(--ok)}.statebar.aging{background:var(--aging-bg);color:var(--aging)}
.statebar.stale{background:var(--stale-bg);color:var(--stale)}.statebar.lapsed{background:var(--lapsed-bg);color:var(--lapsed)}.statebar b{font-weight:700}
h2.sec{font-size:13px;text-transform:uppercase;letter-spacing:.07em;color:var(--muted);margin:30px 0 8px}
.means{display:grid;grid-template-columns:1fr 1fr;gap:14px}.means .box{border:1px solid var(--line);border-radius:12px;padding:14px 16px;background:#fff}
.means .box h3{margin:0 0 6px;font-size:14px}.means ul{margin:0;padding-left:18px;color:#33404f}.means li{margin:4px 0}
.disc{font-size:12.5px;color:var(--muted);border-left:3px solid var(--line);padding:6px 0 6px 12px;margin-top:14px}
.prov{font-size:11.5px;color:var(--muted);font-family:ui-monospace,Menlo,Consolas,monospace;margin-top:6px;word-break:break-word}
footer{color:var(--muted);font-size:12px;margin-top:34px;border-top:1px solid var(--line);padding-top:12px}
@media(max-width:560px){.grid,.means{grid-template-columns:1fr}}
"""

    doc = f"""<!DOCTYPE html>
<!--
  BackBond Trust Page — v1 (BEAAA-11926 / WI-3 dynamic wiring)
  persona_audience: internal. Rendered surface is external-facing and gated by
  Designer (60670b68) UX review [BEAAA-11928] + outside-counsel review before any partner send.
  brand_backbone_version: {BRAND_BACKBONE_VERSION}  sha256: {BRAND_BACKBONE_SHA256}  verified: true
  Fields are LIVE-WIRED: band+envelope <- rubric.scoring.score() on locked rubric (WI-1);
  monitoring continuity <- continuity_gate (WI-2); warranty eligibility <- WI-6 §2 rule.
  Reproducibility envelope (Supreme Law):
{envelope_comment}
-->
<html lang="en"><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>BackBond Private Trust Page</title><style>{css}</style></head><body>
<div class="watermark">{_e(watermark)}</div>
<div class="wrap">
  <div class="brandline"><span class="dot"></span><b>BackBond</b><span class="sub">· Private Trust Page</span></div>
  <div class="sub">Prepared for a single named recipient. Confidential to the vendor and the named enterprise reviewer.</div>

  <div class="card">
    <div class="rowtop">
      <div><h1>{_e(record.agent_name)}</h1><div class="vendor">Vendor: {_e(record.vendor_name)}</div></div>
      <div class="band"><div class="letter">{_e(record.band_display)}</div><div class="lbl">BackBond Score band</div></div>
    </div>

    <div class="statebar {st_cls}"><b>{_e(st_phrase)}.</b> {_e(st_line)}</div>

    <div class="grid">
      <div class="f"><div class="k">Verified as of</div><div class="v">{_e(_fmt_ts(record.verified_as_of))}</div></div>
      <div class="f"><div class="k">Next required verification</div><div class="v">{_e(_fmt_date(record.next_required_verification))}</div></div>
      <div class="f"><div class="k">Verification freshness</div><div class="v"><span class="pill {st_cls}">{_e(st_phrase)}</span></div></div>
      <div class="f"><div class="k">Monitoring continuity</div><div class="v"><span class="pill {cont_cls}">{_e(cont_lbl)}</span></div></div>
      <div class="f"><div class="k">Warranty remedy eligibility</div><div class="v"><span class="pill {warr_cls}">{_e(warr_lbl)}</span></div></div>
      <div class="f"><div class="k">Scanner / corpus / rubric</div><div class="v prov">{rubric_line}</div></div>
    </div>

    <div class="disc"><b>{_e(COPY['warranty_disclaimer'])}</b></div>
  </div>

  <h2 class="sec">What this means / What this does not mean</h2>
  <div class="means">
    <div class="box"><h3>What this means</h3><ul>
      <li>This agent was verified against BackBond's scanner on a frozen rubric, at the timestamp shown.</li>
      <li>{_e(COPY['means_bullet_2'])}</li>
      <li>Monitoring continuity shows whether runtime evidence is still flowing since the last verification.</li>
    </ul></div>
    <div class="box"><h3>What this does not mean</h3><ul>
      <li>{_e(COPY['not_mean_bullet_1'])}</li>
      <li>A band is not a guarantee of future agent behavior.</li>
      <li>Eligibility is not the same as an attached remedy; see the disclaimer above.</li>
    </ul></div>
  </div>

  <h2 class="sec">Stale state is a feature — how the record reads over time</h2>
  <div class="means">
    <div class="box"><h3>Current</h3><div class="sub">{_e(COPY['gallery_current'])}</div></div>
    <div class="box"><h3>Aging / due soon</h3><div class="sub">{_e(COPY['gallery_aging'])}</div></div>
    <div class="box"><h3>Stale</h3><div class="sub">{_e(COPY['gallery_stale'])}</div></div>
    <div class="box"><h3>Expired / lapsed</h3><div class="sub">{_e(COPY['gallery_expired'])}</div></div>
  </div>
  <div class="disc">Decay is expected asset behavior, rendered as information (amber/grey), never as a system error. The verified-as-of and next-required timestamps are always visible so a buyer can judge freshness directly.</div>

  <div class="disc prov"><b>Reproducibility envelope:</b> {rubric_line} · warranty source: {_e(record.warranty.get('source',''))}</div>

  <footer>{_e(COPY['footer'])}</footer>
</div></body></html>"""

    _assert_no_prohibited(doc)
    return doc
