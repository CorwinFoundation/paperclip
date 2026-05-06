"""Render a BMI Report Card HTML from a JSON eval_outputs file.

Lets non-engineers (CSO, CMO, CBDO, board) generate a board-quality report
card without touching the API. Useful for pilot LOI binders ([BEAAA-50])
and customer-facing scan cards ([BEAAA-69]).

Usage:
    python3 scripts/render_card.py path/to/eval_outputs.json \\
        --snapshot-hash sha256:my-customer-2026-04-30 \\
        --forensic-bundle https://bundles.beai.local/my-customer \\
        --out path/to/card.html

JSON schema (input):
    { "P1.1": {"raw": 3.0, "evidence_uri": "..."}, ... }
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from report_card.render import evaluate_bind_eligibility, render  # noqa: E402
from rubric.scoring import Rubric, score  # noqa: E402

RUBRIC_PATH = REPO / "rubric" / "rubric.yaml"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("eval_outputs", type=Path, help="JSON file: dim_id -> {raw, evidence_uri}")
    parser.add_argument("--snapshot-hash", required=True, help="snapshot_hash for the bound model")
    parser.add_argument("--forensic-bundle", required=True, help="forensic_bundle_uri for claims spine")
    parser.add_argument("--out", type=Path, required=True, help="HTML output path")
    parser.add_argument("--scan-age-days", type=int, default=0, help="override scan_evidence_age_days")
    parser.add_argument(
        "--provenance",
        default=None,
        help='Scan-level provenance label (e.g. "measured — direct-API customer scan", '
             '"category-proxy:hf-router — Llama-3.3-70B reference model", "prior"). '
             'Omitting renders a loud "PROVENANCE NOT SET" warning on the card.',
    )
    args = parser.parse_args()

    rubric = Rubric.load(RUBRIC_PATH)
    eval_outputs = json.loads(args.eval_outputs.read_text())
    eval_outputs.pop("_comment", None)

    score_response = score(
        rubric,
        eval_outputs,
        snapshot_hash=args.snapshot_hash,
        forensic_bundle_uri=args.forensic_bundle,
        scored_at=datetime.now(timezone.utc),
    )
    if args.scan_age_days:
        score_response["scan_evidence_age_days"] = int(args.scan_age_days)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render(score_response, rubric, provenance=args.provenance))

    verdict = evaluate_bind_eligibility(score_response)
    band = score_response["band"]
    print(f"BMI={score_response['bmi']} band={band['display']} verdict={'BIND' if verdict['eligible'] else 'NO BIND'}")
    if not verdict["eligible"]:
        for r in verdict["reasons"]:
            print(f"  - {r}")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
