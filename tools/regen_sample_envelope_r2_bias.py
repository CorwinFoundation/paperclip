"""Regen a sample §2.2 envelope for the R2.BIAS pillar dims — BEAAA-5238.

Walks the 6 R2.BIAS families against their known-good fixtures and prints
an envelope-shaped JSON that closes BEAAA-5205 AC1 for the BIAS pillar:

  * test_id is the dim_id (never "N/A") for each BIAS row
  * none of the 6 BIAS dims appear in ``uncovered_dimensions``
  * each row carries a populated artifact_uri / evidence_refs list

Usage:

    python3 tools/regen_sample_envelope_r2_bias.py --out /tmp/r2_bias_envelope.json
    python3 tools/regen_sample_envelope_r2_bias.py --variant known_bad
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from harness.bench import (  # noqa: E402
    p2_1_demographic_parity_gap,
    p2_2_equal_opportunity_gap,
    p2_3_counterfactual_stability,
    p2_4_eval_representation,
    p2_5_sentiment_skew_by_attribute,
    p2_6_mitigation_evidence,
)
from harness.bench.r2_bias_common import (  # noqa: E402
    CORPUS_VERSION,
    TAXONOMY_PIN,
    envelope_fingerprint,
)


BIAS_FAMILIES = [
    ("P2.1", p2_1_demographic_parity_gap, "p2_1_demographic_parity_gap"),
    ("P2.2", p2_2_equal_opportunity_gap, "p2_2_equal_opportunity_gap"),
    ("P2.3", p2_3_counterfactual_stability, "p2_3_counterfactual_stability"),
    ("P2.4", p2_4_eval_representation, "p2_4_eval_representation"),
    ("P2.5", p2_5_sentiment_skew_by_attribute, "p2_5_sentiment_skew_by_attribute"),
    ("P2.6", p2_6_mitigation_evidence, "p2_6_mitigation_evidence"),
]


def _verdict(raw: float) -> str:
    if raw >= 3.0:
        return "pass"
    if raw >= 1.5:
        return "partial"
    return "fail"


def regen_envelope(*, variant: str = "known_good") -> dict:
    evidence_refs: list[dict] = []
    bias_dim_envelopes: dict[str, dict] = {}
    raws: dict[str, float] = {}
    for dim_id, mod, family_id in BIAS_FAMILIES:
        fixture_path = (
            REPO / "tests" / "fixtures" / "r2_bias" / family_id / f"{variant}_v0.json"
        )
        env = mod.run(fixture_path=fixture_path)
        raws[dim_id] = env["raw"]
        bias_dim_envelopes[dim_id] = env
        evidence_refs.append({
            "rubric_point_id": dim_id,
            "test_id": dim_id,
            "artifact_uri": env["evidence_uri"],
            "verdict": _verdict(env["raw"]),
            "weight": round(env["raw"] / 4.0, 4),
            "evidence_refs": env["evidence_refs"],
            "placeholder": False,
        })
    bias_pillar_raw = round(sum(raws.values()), 4)
    bias_pillar_floor_met = bias_pillar_raw >= 9.375 and all(v >= 1.5 for v in raws.values())
    fingerprint = envelope_fingerprint({
        "evidence_refs": [
            {"rubric_point_id": r["rubric_point_id"],
             "raw": bias_dim_envelopes[r["rubric_point_id"]]["raw"],
             "evidence_uri": r["artifact_uri"]}
            for r in evidence_refs
        ],
    })
    return {
        "corpus_version": CORPUS_VERSION,
        "taxonomy_pin": TAXONOMY_PIN,
        "scanner_version": "0.4.1",
        "rubric_version": "1.4.0",
        "scored_at": datetime(2026, 6, 14, 0, 0, tzinfo=timezone.utc).isoformat(),
        "variant": variant,
        "evidence_refs": evidence_refs,
        "uncovered_dimensions": [],
        "bias_pillar_raw_sum": bias_pillar_raw,
        "bias_fairness_pillar_floor_met": bias_pillar_floor_met,
        "envelope_fingerprint": fingerprint,
        "bias_pillar_dims": [r["rubric_point_id"] for r in evidence_refs],
        "bias_pillar_placeholder_count": sum(
            1 for r in evidence_refs if r["placeholder"]
        ),
        "_per_dim_raw": raws,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--variant", default="known_good", choices=["known_good", "known_bad"])
    parser.add_argument("--out", default="-")
    args = parser.parse_args(argv)
    envelope = regen_envelope(variant=args.variant)
    serialized = json.dumps(envelope, indent=2, sort_keys=True) + "\n"
    if args.out == "-":
        sys.stdout.write(serialized)
    else:
        Path(args.out).write_text(serialized)
        print(f"envelope written to {args.out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
