"""BEAAA-34042: pool the round-2 cac capture into the rank-2 ledger and re-apply the gate.

CEO ruling 2026-09-18, route 2. The 14 ``consequential_action_confirmation`` rows
held on ``verdict_spread_below_stability_floor`` were short exactly one clause -
``minority_rounds`` 1 against a required 2 - so what they needed was a second
capture round, not a lower floor and not a bigger sample inside one round.

Additive only. Round-1 observations are kept exactly as captured and are never
re-labelled; round 2 is appended beside them. The floor itself is untouched:
``MIN_SPREAD_OBSERVATIONS``, ``MIN_MINORITY_OBSERVATIONS`` and ``MIN_MINORITY_SPAN``
are read from the slice, not redefined here. Adding a round raises the absolute
observation bar rather than lowering it.

No pin moves. The pinned run manifest is not re-bound: ``retier_ledger`` re-applies
the tier decision to rows already on the ledger, so a second round enters as rows
and never as a competing manifest sha.

Each row observes under the round DERIVED from its own ``evidence_ref``
(``_capture_round_for``). Nothing in this module states a round for a row.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tools.beaaa31092_content_slice import BELOW_STABILITY_FLOOR, _capture_round_for, retier_ledger

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "artifacts/beaaa-31092/rank2-ledger.json"
ROUND2_LOG = ROOT / "artifacts/beaaa-34042/round2-capture-log.jsonl"
FAMILY = "consequential_action_confirmation"

# Carried from the round-1 row for the same case, because they are properties of
# the CASE and not of the capture: re-deriving them here would let a second round
# quietly disagree with the first about what it measured.
CASE_IDENTITY_FIELDS = ("dedupe_sha256", "semantic_behavior_fingerprint", "mapping_rationale")


def round2_rows(ledger: dict[str, Any], log_path: Path = ROUND2_LOG) -> list[dict[str, Any]]:
    """Round-2 captures as ledger rows, keyed to the round-1 row of the same case."""
    by_case = {row["stable_case_id"]: row for row in ledger["rows"] if row["family_id"] == FAMILY}
    rows = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if record.get("status") != "complete" or not record.get("verdict"):
            continue  # a row that errored stays errored; it is not an observation
        first = by_case[record["stable_case_id"]]
        rows.append({
            **{field: first[field] for field in CASE_IDENTITY_FIELDS},
            "stable_case_id": record["stable_case_id"],
            "family_id": FAMILY,
            "verdict": record["verdict"],
            "evidence_ref": record["evidence_ref"],
            "recorded_target_response": record["recorded_target_response"],
            "rubric_point_id": record["rubric_point_id"],
            "model_version": record["model_version"],
            # Entered exactly as the round-1 rows sit today: held by the gate's own
            # literal, so ``_restore_gate_demotions`` lifts both rounds through the
            # same path and this module never asserts a row is scorable.
            "disposition": "held_reported_not_scored",
            "hold_reasons": [BELOW_STABILITY_FLOOR],
        })
    return rows


def build_pooled_ledger(ledger_path: Path = LEDGER, log_path: Path = ROUND2_LOG) -> dict[str, Any]:
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    before = {"scored": ledger["scored"], "held": ledger["held"], "rows": len(ledger["rows"])}
    added = round2_rows(ledger, log_path)
    ledger["rows"] = sorted(ledger["rows"] + added,
                            key=lambda row: (row["stable_case_id"], row.get("evidence_ref") or ""))
    retier_ledger(ledger, manifest_path=ROOT / "artifacts/beaaa-31092/rank2-run-manifest.json")
    # Reported through the same derivation the gate itself uses. Re-implementing the
    # parse here is how a report starts disagreeing with the thing it reports on.
    ledger["pooled_capture_rounds"] = sorted({
        _capture_round_for(row, None) for row in ledger["rows"]
        if row["family_id"] == FAMILY and row.get("evidence_ref")})
    return {"ledger": ledger, "before": before, "round2_rows": len(added),
            "after": {"scored": ledger["scored"], "held": ledger["held"], "rows": len(ledger["rows"])}}


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--stamp-sha", default=None)
    parser.add_argument("--stamp-at", default=None)
    args = parser.parse_args()
    result = build_pooled_ledger()
    if args.output:
        if not (args.stamp_sha and args.stamp_at):
            parser.error("--output requires --stamp-sha and --stamp-at")
        result["ledger"]["provenance_stamp"] = {
            "built_at": args.stamp_at,
            "built_at_sha": args.stamp_sha,
            "built_by_issue": "BEAAA-34042",
            "notice": (
                "Derived, not captured: the committed round-1 rank-2 ledger with the "
                "BEAAA-34042 round-2 capture log appended, re-tiered. This is a SEPARATE "
                "artifact on purpose. The pinned rank2-ledger.json still reproduces from "
                "the pinned inventory + run manifest alone, which is what the BEAAA-33964 "
                "drift check asserts; a pooled ledger cannot, because round 2 is not in the "
                "pinned manifest and no pin was moved to put it there. Rebuild with "
                "python -m tools.beaaa34042_pooled_rank2_gate."),
            "stamped_by_issue": "BEAAA-34042",
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result["ledger"], indent=2, sort_keys=True) + "\n",
                               encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "ledger"}, indent=1, sort_keys=True))
    print("tier1:", json.dumps(result["ledger"]["tier1_families"]))
    print("rounds:", json.dumps(result["ledger"]["pooled_capture_rounds"]))
