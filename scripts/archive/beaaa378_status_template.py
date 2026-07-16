"""Auto-emit §2/§3/§5 of the CTO Friday status (BEAAA-378 / BEAAA-398).

Reads the persisted baselines under `artifacts/beaaa-387/`, runs:

  - harness.golden_set.calibration_delta.compute_delta()  → §2
  - harness.golden_set.n5_band.run_l1_n5()                → §3 (L1 layer)
  - harness.golden_set.discrimination.compute_discrimination() → §5

Writes three machine-readable artifacts:

  artifacts/beaaa-387/calibration_delta_<DATE>.json
  artifacts/beaaa-387/n5_band_<DATE>.json
  artifacts/beaaa-387/discrimination_<DATE>.json

…and a markdown fragment (`status_sections_<DATE>.md`) that the next Friday
status owner can paste into the §2/§3/§5 slots of
`artifacts/beaaa-378/status_<DATE>.md`. Replaces the prior "pending" rows
with real numbers and a partial-coverage caveat block per BEAAA-419.

Pending fixtures (G3/B2/B3/M3 as of 2026-05-16) emit "N/A — fixture pending
baseline scan, owned by BEAAA-387b/c" rather than blocking the others.

Usage:
    python scripts/beaaa378_status_template.py
    python scripts/beaaa378_status_template.py --date 2026-05-22
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from harness.golden_set.calibration_delta import compute_delta  # noqa: E402
from harness.golden_set.discrimination import compute_discrimination  # noqa: E402
from harness.golden_set.n5_band import run_l1_n5  # noqa: E402

OUT_DIR = REPO / "artifacts" / "beaaa-387"


def _write(p: Path, payload: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _fmt_breach(value: int | float, breach: bool) -> str:
    flag = " ⚠" if breach else ""
    return f"{value}{flag}"


def _section_2_md(delta: dict) -> str:
    rows = []
    rows.append("| Fixture | Slot | Baseline ARE | Current ARE | ΔARE | Pillar Δ (max abs) | Attribution | Notes |")
    rows.append("|---|---|---|---|---|---|---|---|")
    for f in delta["fixtures"]:
        if not f["baselined"]:
            rows.append(
                f"| {f['fixture_id']} | {f['slot']} | — | — | N/A | N/A | pending | "
                f"{f.get('pending_reason', 'pending baseline scan, owned by BEAAA-387b/c')} |"
            )
            continue
        max_pdelta = max(
            (abs(v) for v in f["pillar_deltas"].values()),
            default=0.0,
        )
        breach_marker = " ⚠" if f["are_breach"] or f["pillar_breaches"] else ""
        rows.append(
            f"| {f['fixture_id']} | {f['slot']} | {f['baseline_are']} | "
            f"{f['current_are']} | {f['are_delta']:+d}{breach_marker} | "
            f"{max_pdelta:+.2f} | {f['drift_attributable_to']} | "
            f"baseline={f['baseline_rubric_version']}/{f['baseline_scanner_version']} |"
        )
    summary = delta["summary"]["summary_line"]
    caveat = delta["caveats"]["beaaa353_rubric_contract_drift"]
    return (
        "## 2. Calibration health (golden-set deltas)\n\n"
        f"**L1 deterministic gate:** see §3. **Calibration delta vs persisted baselines:**\n\n"
        + "\n".join(rows) + "\n\n"
        f"**Summary:** {summary}\n\n"
        f"**BEAAA-353 caveat:** {caveat}\n"
    )


def _section_3_md(band: dict) -> str:
    rows = []
    rows.append("| Fixture | Slot | N | ARE min | ARE max | ARE drift | Max pillar drift | Status |")
    rows.append("|---|---|---|---|---|---|---|---|")
    for f in band["fixtures"]:
        if not f["baselined"]:
            rows.append(
                f"| {f['fixture_id']} | {f['slot']} | — | — | — | — | — | "
                f"pending: {f.get('pending_reason', 'baseline scan owned by BEAAA-387b/c')} |"
            )
            continue
        max_pdrift = max(f["pillar_drifts"].values(), default=0.0)
        status = "BREACH" if (f["are_breach"] or f["pillar_breaches"]) else "PASS"
        rows.append(
            f"| {f['fixture_id']} | {f['slot']} | {f['n_replicates']} | "
            f"{f['are_min']} | {f['are_max']} | {f['are_drift']} | "
            f"{max_pdrift:.4f} | {status} |"
        )
    summary = band["summary"]["summary_line"]
    return (
        "## 3. Reproducibility variance band (rolling N=5)\n\n"
        "**L1 (deterministic, no-API):** re-run `score()` against locked "
        "`eval_outputs.json` for every baselined fixture. Drift = 0 by "
        "construction; non-zero = release-blocker code regression.\n\n"
        + "\n".join(rows) + "\n\n"
        f"**Summary:** {summary}\n\n"
        "**L2 (live probe-runner):** owned by `harness/golden_set/repro_gate.py`; "
        "cadence = weekly on cheap fixtures, monthly on full slate (BEAAA-398 §3). "
        "Out-of-band of CI; runs against fresh probe replicates, not locked outputs.\n"
    )


def _section_5_md(disc: dict) -> str:
    rows = []
    rows.append("| Fixture | Slot | Baseline ARE | Current ARE |")
    rows.append("|---|---|---|---|")
    for f in disc["fixtures"]:
        if not f["baselined"]:
            rows.append(f"| {f['fixture_id']} | {f['slot']} | — | N/A — pending |")
        else:
            rows.append(
                f"| {f['fixture_id']} | {f['slot']} | {f['baseline_are']} | {f['current_are']} |"
            )
    spread = disc["spread"]
    dist = disc["distribution"]
    by_slot_lines = "\n".join(
        f"  - {slot}: n={info['n']}, range {info['min']}–{info['max']}, mean {info['mean']}"
        for slot, info in dist["by_slot"].items()
    )
    bucket_lines = ", ".join(
        f"{label}: {count}" for label, count in dist["buckets_current"].items() if count
    )
    caveat = disc["caveats"]["partial_coverage"]
    spread_pass = "PASS" if spread["passes_target"] else "BELOW TARGET"
    collapse_pass = "no collapse" if not dist["collapse_to_mean"] else "COLLAPSE ⚠"
    return (
        "## 5. Discrimination metric\n\n"
        f"**Spread (current rubric):** {spread['current']} pts (target ≥ {spread['target']}) — {spread_pass}\n"
        f"**Spread (baseline ARE values):** {spread['baseline']} pts\n"
        f"**Mean / stdev (current):** {dist['mean_current']} / {dist['stdev_current']}\n"
        f"**Collapse-to-mean (±10):** {collapse_pass}\n\n"
        "**Per-fixture:**\n\n"
        + "\n".join(rows) + "\n\n"
        f"**Active 10-pt buckets (current):** {bucket_lines}\n\n"
        f"**By slot:**\n{by_slot_lines}\n\n"
        f"**Partial-coverage caveat:** {caveat}\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--date", default=datetime.now(timezone.utc).date().isoformat(),
        help="Date stamp (YYYY-MM-DD) used in artifact filenames; defaults to today UTC.",
    )
    parser.add_argument(
        "--n", type=int, default=5,
        help="N for §3 L1 reproducibility band (default 5).",
    )
    args = parser.parse_args()

    delta = compute_delta(today=args.date)
    band = run_l1_n5(n=args.n, today=args.date)
    disc = compute_discrimination(today=args.date)

    delta_path = OUT_DIR / f"calibration_delta_{args.date}.json"
    band_path = OUT_DIR / f"n5_band_{args.date}.json"
    disc_path = OUT_DIR / f"discrimination_{args.date}.json"
    _write(delta_path, delta)
    _write(band_path, band)
    _write(disc_path, disc)

    sections_md = (
        f"<!-- BEAAA-398 auto-emitted §2/§3/§5 — {args.date} UTC -->\n"
        f"<!-- sources: {delta_path.relative_to(REPO)}, "
        f"{band_path.relative_to(REPO)}, {disc_path.relative_to(REPO)} -->\n\n"
        + _section_2_md(delta) + "\n"
        + _section_3_md(band) + "\n"
        + _section_5_md(disc)
    )
    md_path = OUT_DIR / f"status_sections_{args.date}.md"
    md_path.write_text(sections_md, encoding="utf-8")

    print(f"§2 calibration_delta → {delta_path.relative_to(REPO)}")
    print(f"   {delta['summary']['summary_line']}")
    print(f"§3 n5_band            → {band_path.relative_to(REPO)}")
    print(f"   {band['summary']['summary_line']}")
    print(f"§5 discrimination     → {disc_path.relative_to(REPO)}")
    print(f"   {disc['summary']['summary_line']}")
    print(f"status fragment       → {md_path.relative_to(REPO)}")

    delta_breach = not delta["summary"]["passed"]
    band_breach = not band["summary"]["passed"]
    disc_breach = not disc["summary"]["passed"]
    if band_breach:
        print("§3 BREACH — L1 deterministic drift > 0; release-blocker.", file=sys.stderr)
        return 2
    if delta_breach:
        print("§2 BREACH — Supreme-Law version-aligned delta > tolerance; "
              "Underwriter approval required.", file=sys.stderr)
        return 2
    if disc_breach:
        print("§5 ATTENTION — discrimination spread below target or collapse "
              "detected; review partial-coverage caveat.", file=sys.stderr)
        # Phase-1 partial coverage: don't fail-the-status on spread-below-target;
        # the caveat block carries the signal.
    return 0


if __name__ == "__main__":
    sys.exit(main())
