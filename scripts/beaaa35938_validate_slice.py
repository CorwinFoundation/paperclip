"""BEAAA-35938 VALIDATE: refuse the hallucination_legal slice unless the evidence is real.

The BEAAA-21728 guardrail is that nothing is scored that is unmapped or that
lacks ``recorded_target_response`` + verdict from an actual target run. This
script is the enforcement of that sentence, run before the ledger is allowed to
reach the registry. Every check below FAILS the slice rather than warning.

  V1 provenance      every scored row's dedupe digest is in the BEAAA-18720
                     catalogue, its provenance path is the family YAML at the
                     pinned source revision, and its stable_case_id resolves to a
                     probe that actually exists in that YAML today.
  V2 responses       every scored row has a non-empty recorded target response on
                     all five seeds. A row scored off a missing, empty or
                     whitespace response is the exact failure this gate exists for.
  V3 placeholders    no response or evidence span matches a placeholder pattern
                     (TODO/TBD/lorem/FIXME/XXX/sample response/<...>).
  V4 not-a-fixture   the responses are not the fixture corpus. Every scored row's
                     response is checked against tests/fixtures and harness/fixtures
                     content; a scored row whose response appears verbatim in a
                     committed fixture is fixtures-as-production and fails.
  V5 verdicts        every scored row carries an ensemble verdict on every seed,
                     identical across seeds, from one of the two pinned graders.
  V6 tier            the ledger declares dev tier, zero weight, production
                     activation off. A ledger that asks for production scoring
                     fails here regardless of its rows.
  V7 budget          the capture did not stop early, and the AUTHORIZED CAP is
                     checked against the account pool, not against the capture
                     script's own meter. The meter prices a price table against
                     the token counts the API reports and zeroes on resume, so it
                     cannot enforce a real-money cap; the readings in
                     artifacts/beaaa-35938/pool-readings.jsonl can, and their
                     absence fails the slice rather than falling back to the meter.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
CATALOGUE = ROOT / "artifacts" / "beaaa-18720" / "unified-probe-inventory.jsonl"
YAML_RELPATH = "rubric/probe_mappings/hallucination_legal.yaml"
PINNED_SOURCE_REVISION = "6e78dae48131f590cdd59fada023089c53a45236"
PINNED_GRADERS = {
    "harness.bench.hallucination_legal.judge_f2_case",
    f"{YAML_RELPATH}#judge.rubric",
}
PLACEHOLDER = re.compile(
    r"\b(todo|tbd|fixme|xxx+|lorem ipsum|placeholder|sample response|your response here)\b|<[A-Z_]{3,}>",
    re.IGNORECASE,
)
# CEO re-ruling on BEAAA-35938 at 2026-09-25T21:07Z, raising the 20:20Z $2.00 cap
# after the $0.38 estimate it rested on was shown to be wrong.
AUTHORIZED_CAP_USD = 3.00
POOL_READINGS = ROOT / "artifacts" / "beaaa-35938" / "pool-readings.jsonl"


def fail(errs: list[str], cond: bool, msg: str) -> None:
    if not cond:
        errs.append(msg)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--capture", default="artifacts/beaaa-35938/hl_slice_capture.json")
    ap.add_argument("--ledger", default="artifacts/beaaa-35938/hl-dev-activation-ledger.json")
    ap.add_argument("--out", default="artifacts/beaaa-35938/validation-report.json")
    args = ap.parse_args()

    cap = json.loads((ROOT / args.capture).read_text())
    led = json.loads((ROOT / args.ledger).read_text())
    catalogue = {
        json.loads(line)["dedupe_sha256"]: json.loads(line)
        for line in CATALOGUE.read_text().splitlines()
        if line.strip()
    }
    doc = yaml.safe_load((ROOT / YAML_RELPATH).read_text())
    probe_ids = {
        p["id"] for fam in (doc.get("families") or {}).values() for p in fam.get("probes") or []
    }
    cap_rows = {r["stable_case_id"]: r for r in cap["rows"]}
    scored = [r for r in led["rows"] if r["disposition"] == "scored"]

    errs: list[str] = []

    # V6 / V7 first: cheap, and they invalidate everything else.
    fail(errs, led["activation_tier"] == "founder_dev_mode", "V6 activation_tier is not founder_dev_mode")
    fail(errs, led["production_activation"] is False, "V6 ledger requests production activation")
    fail(errs, float(led["priced_score_weight"]) == 0.0, "V6 priced_score_weight is not zero")
    fail(errs, not cap.get("stopped_reason"), f"V7 capture stopped early: {cap.get('stopped_reason')}")
    metered = float(cap["budget"]["estimated_usd_from_tokens"])
    fail(errs, cap["budget"]["calls"] > 0, "V7 capture recorded zero calls, so no target was run")
    # The cap is real money, so it is read off the account, and a missing reading
    # is a failure rather than a fallback to the meter.
    readings = [
        json.loads(line)
        for line in (POOL_READINGS.read_text().splitlines() if POOL_READINGS.exists() else [])
        if line.strip()
    ]
    fail(errs, bool(readings), f"V7 no account-pool reading at {POOL_READINGS.relative_to(ROOT)}")
    pool_drawn = max((float(r["drawn_since_baseline_usd"]) for r in readings), default=float("nan"))
    if readings:
        fail(
            errs,
            pool_drawn <= AUTHORIZED_CAP_USD,
            f"V7 account-pool draw ${pool_drawn:.4f} exceeds the authorized ${AUTHORIZED_CAP_USD:.2f}",
        )

    fixture_blobs: list[str] = []
    for base in (ROOT / "tests" / "fixtures", ROOT / "harness" / "fixtures"):
        if base.exists():
            for f in base.rglob("*"):
                if f.is_file() and f.suffix in {".json", ".jsonl", ".yaml", ".yml", ".txt"}:
                    try:
                        fixture_blobs.append(f.read_text(encoding="utf-8", errors="ignore"))
                    except OSError:
                        pass
    fixture_corpus = "\n".join(fixture_blobs)

    for row in scored:
        sid = row["stable_case_id"]
        digest = row["dedupe_sha256"]
        tag = f"{sid}/{digest[:8]}"

        # V1
        entry = catalogue.get(digest)
        fail(errs, entry is not None, f"V1 {tag} digest not in the BEAAA-18720 catalogue")
        if entry is not None:
            fail(errs, entry["provenance"]["path"] == YAML_RELPATH, f"V1 {tag} provenance path is {entry['provenance']['path']}")
            fail(errs, entry["provenance"]["source_revision"] == PINNED_SOURCE_REVISION, f"V1 {tag} source_revision drifted")
            fail(errs, entry["stable_case_id"] == sid, f"V1 {tag} stable_case_id mismatch")
        fail(errs, sid in probe_ids, f"V1 {tag} has no probe with that id in {YAML_RELPATH}")

        cr = cap_rows.get(sid)
        fail(errs, cr is not None, f"V2 {tag} has no capture row")
        if cr is None:
            continue

        # V2 / V3 / V4 / V5
        fail(errs, cr["complete"] and cr["n_seeds"] == cr["seeds_expected"], f"V2 {tag} incomplete seed coverage")
        labels = []
        for seed_rec in cr["per_seed"]:
            resp = seed_rec.get("response")
            fail(errs, isinstance(resp, str) and resp.strip() != "", f"V2 {tag} seed {seed_rec.get('seed')} has no recorded target response")
            if isinstance(resp, str) and resp.strip():
                fail(errs, not PLACEHOLDER.search(resp), f"V3 {tag} seed {seed_rec.get('seed')} response looks like a placeholder")
                fail(errs, resp.strip() not in fixture_corpus, f"V4 {tag} seed {seed_rec.get('seed')} response appears verbatim in a committed fixture")
            v = seed_rec.get("verdict")
            fail(errs, isinstance(v, dict) and v.get("label"), f"V5 {tag} seed {seed_rec.get('seed')} has no verdict")
            if isinstance(v, dict):
                labels.append(v.get("label"))
                fail(errs, v.get("grader") in PINNED_GRADERS, f"V5 {tag} graded by unpinned {v.get('grader')!r}")
                fail(errs, int(v.get("ensemble_n") or 0) == int(doc["judge"]["ensemble_n"]), f"V5 {tag} ensemble_n is not the pinned {doc['judge']['ensemble_n']}")
                span = str(v.get("evidence_span") or "")
                fail(errs, not PLACEHOLDER.search(span), f"V3 {tag} evidence span looks like a placeholder")
        fail(errs, len(set(labels)) == 1, f"V5 {tag} verdict not identical across seeds: {sorted(set(labels))}")
        fail(errs, row["seed_stable"] is True, f"V5 {tag} ledger says not seed_stable but was scored")
        fail(errs, bool(row.get("rubric_point_id")), f"V1 {tag} scored without a rubric point")

    report = {
        "issue": "BEAAA-35938",
        "n_scored_checked": len(scored),
        "n_held": led["n_held"],
        "metered_spend_usd_not_authoritative": metered,
        "pool_drawn_usd": pool_drawn,
        "authorized_cap_usd": AUTHORIZED_CAP_USD,
        "fixture_files_scanned": len(fixture_blobs),
        "checks": ["V1 provenance", "V2 responses", "V3 placeholders", "V4 not-a-fixture", "V5 verdicts", "V6 tier", "V7 budget"],
        "failures": errs,
        "result": "PASS" if not errs else "FAIL",
    }
    dest = ROOT / args.out
    dest.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "failures"}, indent=2, sort_keys=True))
    if errs:
        print(f"\n{len(errs)} FAILURES:", file=sys.stderr)
        for e in errs[:40]:
            print(f"  - {e}", file=sys.stderr)
        return 1
    print("\nVALIDATE: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
