"""BEAAA-33677: emit a round's required report straight from its ledger.

The card fixes what a round comment must carry: mapped / evidenced / scored /
held / dropped-duplicate, the hold-reason literal on EVERY held row, the
per-family scored counts, and the realized control count against its
pre-registered budget. Hand-tallying those from a 200-row ledger is how a number
drifts from the artifact it claims to summarise, so they are read off the ledger
here and nowhere else.

The budget is an INPUT, not something inferred from the result. Pass the number
that was pre-registered before the round ran; the report prints realized against
it and never the other way round.
"""
import argparse, json
from collections import Counter, defaultdict
from pathlib import Path


def build(ledger: dict, control_budget: int) -> dict:
    rows = ledger["rows"]
    scored_by_family = Counter(r["family_id"] for r in rows if r["disposition"] == "scored")
    held_rows = [r for r in rows if r["disposition"] == "held_reported_not_scored"]
    # Every held row's literal, not a summary of them: a row held for two reasons
    # is reported under the exact pair it carries.
    held_by_literal = defaultdict(list)
    for row in held_rows:
        held_by_literal[" + ".join(row["hold_reasons"])].append(row["stable_case_id"])
    dropped = [r["stable_case_id"] for r in rows if r["disposition"] == "dropped_duplicate"]
    controls = ledger.get("verdict_spread_controls") or {}

    def count(value):
        if value is None:
            return 0
        return value if isinstance(value, int) else len(value)

    realized = count(controls.get("supplied"))
    unaccounted = [r["stable_case_id"] for r in rows
                   if r["disposition"] not in
                   ("scored", "held_reported_not_scored", "dropped_duplicate")]
    return {
        "roster_capture_round": ledger.get("roster_capture_round"),
        "selected": ledger["selected"],
        "mapped": ledger["mapped"],
        "evidenced": ledger["evidenced"],
        "scored": ledger["scored"],
        "held": ledger["held"],
        "dropped_duplicate": ledger["dropped_duplicate"],
        "dispositions_sum_to_selected":
            ledger["scored"] + ledger["held"] + ledger["dropped_duplicate"] == ledger["selected"],
        "rows_with_no_recognised_disposition": unaccounted,
        "scored_by_family": dict(sorted(scored_by_family.items())),
        "held_by_reason_literal": {k: sorted(v) for k, v in sorted(held_by_literal.items())},
        "held_rows_total_check": sum(len(v) for v in held_by_literal.values()) == ledger["held"],
        "dropped_duplicate_ids": sorted(dropped),
        "controls": {
            "pre_registered_budget": control_budget,
            "realized_supplied": realized,
            "accepted": count(controls.get("accepted")),
            "rejected": count(controls.get("rejected")),
            "short_of_budget": control_budget - realized,
        },
        "tier1_families": ledger.get("tier1_families"),
        "tier2_held_families": ledger.get("tier2_held_families"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--control-budget", type=int, required=True)
    args = parser.parse_args()
    report = build(json.loads(args.ledger.read_text(encoding="utf-8")), args.control_budget)
    print(json.dumps(report, indent=1, sort_keys=True))
    # A report that does not add up is not a report.
    return 0 if (report["dispositions_sum_to_selected"]
                 and report["held_rows_total_check"]
                 and not report["rows_with_no_recognised_disposition"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
