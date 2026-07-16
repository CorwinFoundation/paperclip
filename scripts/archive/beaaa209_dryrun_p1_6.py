"""BEAAA-209 acceptance — dry-run the Path B P1.6 aggregator on the
three required surfaces (Atticus, LawConnect, Voiceflow v0.2-cut).

Output: artifacts/beaaa-209/dryrun/<surface>.json  per surface, plus a
summary index. Each artifact captures the P1.6 cell shape that will land
on `ScoreResponse.pillars.P1.dimensions["P1.6"]` after Phase 2 wiring
plus the resolved report-card cover-label.

Models are synthetic-but-plausible inputs reflecting what Tier A scans of
each surface measure today (per scan_lawconnect_atticus.py provenance
notes, voiceflow_demo prior, and BEAAA-203 P2.x coverage). Path B
aggregator's job is the math, not the surface model — so this dry-run is
about contract conformance + label-gate behavior across the three
surfaces, not measurement validity.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from harness import p1_emit  # noqa: E402
from harness.bench import eval_coverage_aggregator as cov  # noqa: E402
from report_card.render import _cover_label_from_p1_6  # noqa: E402
from rubric.scoring import Rubric, score  # noqa: E402

OUT_DIR = REPO / "artifacts" / "beaaa-209" / "dryrun"


def _make_eo(measured: list[str], decline: list[str]) -> dict:
    """Build an eval_outputs dict: `measured` dims get raw=2.5,
    `decline` dims get applicable=False (decline-to-attest), and any
    other in-scope dim is omitted (classified missing)."""
    eo: dict = {}
    for d in measured:
        eo[d] = {"raw": 2.5, "evidence_uri": f"https://path-b.dryrun/{d}"}
    for d in decline:
        eo[d] = {"raw": None, "applicable": False,
                 "na_justification": "architectural-na-on-this-surface",
                 "na_justification_key": "tier_a_na_v1",
                 "evidence_uri": ""}
    return eo


# Surface models — reflect Tier A scan reality, not full v1.0 coverage.

SURFACES = {
    "atticus": {
        "vendor_label": "LawConnect Atticus (questions-stage)",
        "provenance": "measured:atticus-questions-stage (BEAAA-182)",
        # Per scan_lawconnect_atticus.py: questions-stage probe only;
        # P3.2 (indirect injection) and P3.3 (secret exfil) N/A
        # (no tool use, no exposed system prompt).
        "measured": [
            "P1.1", "P1.2", "P1.3",
            "P2.1", "P2.2", "P2.3", "P2.4", "P2.5",
            "P3.1",
        ],
        "decline": ["P3.2", "P3.3"],
    },
    "lawconnect": {
        "vendor_label": "LawConnect (ecosystem-wide)",
        "provenance": "measured:lawconnect-expanded (BEAAA-182 expanded)",
        "measured": [
            "P1.1", "P1.2", "P1.3", "P1.4", "P1.5",
            "P2.1", "P2.2", "P2.3", "P2.4", "P2.5",
            "P3.1", "P3.2",
        ],
        "decline": ["P3.3"],
    },
    "voiceflow_v02_cut": {
        "vendor_label": "Voiceflow Dialog Manager demo (v0.2-cut)",
        "provenance": "measured:voiceflow-v0.2-cut",
        # voiceflow_demo prior is governance-undisclosed; Tier A
        # probes get a small measured subset. Demonstrates the
        # aggregator still emits a P1.6 cell on a v0.1-only surface
        # (backward-compat requirement from BEAAA-209 acceptance).
        "measured": [
            "P1.1", "P1.2",
            "P2.1", "P2.3", "P2.5",
            "P3.1",
        ],
        "decline": [],
    },
}


def main() -> int:
    rubric = Rubric.load(REPO / "rubric" / "rubric.yaml")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    summary: dict = {
        "issue": "BEAAA-209",
        "phase": "Phase 2 dry-run",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "rubric_version": rubric.version,
        "aggregator_algorithm_version": cov.ALGORITHM_VERSION,
        "surfaces": {},
    }
    for surface_key, spec in SURFACES.items():
        eo = _make_eo(spec["measured"], spec["decline"])
        eo = p1_emit.populate_p1_6_cell(
            eo, rubric=rubric,
            evidence_uri=f"s3://dryrun/beaaa-209/{surface_key}/p1_6",
        )
        # Run the scoring engine end-to-end to confirm the v0.2.4 fields
        # propagate through onto the dim block. For the dry-run, both
        # explicit declines AND aggregator-missing dims are passed as
        # Option C N/A decisions so the scoring engine accepts the
        # partial input (the aggregator's classification of "missing"
        # already counted them as uncovered; this just keeps `score`
        # from raising on missing dim scores). Aggregator-relevant dims
        # (the ones in `measured`) and P1.6 itself stay in eval_outputs.
        missing_set = [
            d for d in rubric.active_dim_ids
            if d not in spec["measured"]
            and d not in spec["decline"]
            and d != "P1.6"
        ]
        applicability = {}
        for d in spec["decline"]:
            applicability[d] = {
                "fires": False,
                "na_justification": "architectural-na-on-this-surface",
                "na_justification_key": "tier_a_na_v1",
            }
        for d in missing_set:
            applicability[d] = {
                "fires": False,
                "na_justification": "not-exercised-in-this-tier-a-scan",
                "na_justification_key": "tier_a_not_exercised_v1",
            }
        sr = score(
            rubric, eo,
            snapshot_hash=f"sha256:dryrun-{surface_key}",
            forensic_bundle_uri=f"s3://dryrun/beaaa-209/{surface_key}/bundle",
            applicability_decisions=applicability,
            applicability_policy_version="dryrun-1",
        )
        p1_6_block = sr["pillars"]["P1"]["dimensions"]["P1.6"]
        cover_label, _ = _cover_label_from_p1_6(sr)
        record = {
            "surface": surface_key,
            "vendor_label": spec["vendor_label"],
            "provenance": spec["provenance"],
            "input": {
                "measured_dim_ids": spec["measured"],
                "decline_dim_ids": spec["decline"],
                "missing_dim_ids": missing_set,
            },
            "p1_6_cell": p1_6_block,
            "cover_label": cover_label,
            "are_score": sr["bmi"],
            "band": sr["band"]["display"],
        }
        (OUT_DIR / f"{surface_key}.json").write_text(
            json.dumps(record, indent=2, sort_keys=True) + "\n"
        )
        summary["surfaces"][surface_key] = {
            "coverage_breadth_pct": p1_6_block["coverage_breadth_pct"],
            "p1_6_raw": p1_6_block["raw"],
            "evidence_path": p1_6_block["evidence_path"],
            "cover_label": cover_label,
            "covered_count": len(p1_6_block["covered_dim_ids"]),
        }
        print(
            f"{surface_key:24s}  "
            f"pct={p1_6_block['coverage_breadth_pct']:5.1f}  "
            f"raw={p1_6_block['raw']:.1f}  "
            f"covered={len(p1_6_block['covered_dim_ids']):2d}  "
            f"label={cover_label}"
        )
    (OUT_DIR / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n"
    )
    print(f"\nartifacts → {OUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
