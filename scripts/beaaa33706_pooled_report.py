"""BEAAA-33706: render the pooled-vs-split report as markdown, off the artifact.

Every figure printed here is read from ``pooled-ledger.json`` and
``pooled-vs-split-report.json``. Nothing is tallied by hand - that is how a
reported number drifts from the artifact it claims to summarise - and promotions
and demotions are printed as two counts with no net anywhere.
"""
import argparse, json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def render(ledger: dict, report: dict, equivalence: dict) -> str:
    out = []
    out.append(f"Cohort `{ledger['roster_capture_round']}` pooling rounds "
               f"{', '.join('`%s`' % r for r in ledger['roster_capture_rounds'])}.")
    out.append("")
    out.append(f"selected {ledger['selected']} / mapped {ledger['mapped']} / evidenced {ledger['evidenced']} / "
               f"scored {ledger['scored']} / held {ledger['held']} / dropped-duplicate {ledger['dropped_duplicate']}")
    out.append("")
    out.append(f"**Promotions {report['promotions']}. Demotions {report['demotions']}. "
               f"Other disposition moves {report['other_disposition_moves']}.** "
               f"({report['unchanged']} rows unchanged of {report['rows_compared']} compared.)")
    out.append("")
    out.append("| family | rows | scored before | scored after | held before | held after | tier-1 before | tier-1 after |")
    out.append("|---|---|---|---|---|---|---|---|")
    for family, value in sorted(report["per_family"].items()):
        before = ", ".join(value["tier1_before"]) or "no"
        out.append(f"| `{family}` | {value['rows']} | {value['scored_before']} | {value['scored_after']} | "
                   f"{value['held_before']} | {value['held_after']} | {before} | "
                   f"{'yes' if value['tier1_after'] else 'no'} |")
    out.append("")
    out.append("Held-reason literal on every row that stays held:")
    out.append("")
    for literal, cases in sorted(report["still_held_by_literal"].items()):
        out.append(f"- `{literal}` — {len(cases)} rows")
    out.append(f"- total held {report['still_held_total']} (ledger held {ledger['held']})")
    out.append("")
    out.append("What each tier-1 family's minority verdict rests on "
               "(a promotion count cannot show this, so it is reported beside it):")
    out.append("")
    out.append("| family | minority | minority observations | scored roster rows that side | "
               "distinct roster responses | rounds | at a floor exactly |")
    out.append("|---|---|---|---|---|---|---|")
    for family, value in sorted(report["minority_support"].items()):
        verdict = value["minority_verdict"]
        side = value["by_verdict"].get(verdict or "", {})
        flags = [name for name, key in (("observations", "minority_at_the_observation_floor"),
                                        ("span", "minority_at_the_span_floor"))
                 if value[key]]
        if value["minority_entirely_control_supplied"]:
            flags.append("**control-supplied only**")
        out.append(f"| `{family}` | {verdict} | {side.get('observations')} | "
                   f"{side.get('scored_roster_rows')} | {side.get('distinct_roster_responses')} | "
                   f"{len(side.get('rounds') or [])} | {', '.join(flags) or '-'} |")
    out.append("")
    channel = report["control_channel"]
    out.append(f"Control channel: supplied {channel['supplied_before']} → {channel['supplied_after']}, "
               f"accepted {channel['accepted_before']} → {channel['accepted_after']}, "
               f"rejected {channel['rejected_before']} → {channel['rejected_after']}; "
               f"admitted in its own round but rejected in the union: "
               f"{len(channel['accepted_in_its_own_round_but_rejected_in_the_union'])}.")
    out.append("")
    out.append("| round | rows | scored under its own gate | scored under the pooled gate | yield own | yield pooled |")
    out.append("|---|---|---|---|---|---|")
    for round_id, value in sorted(report["per_round_yield"].items()):
        out.append(f"| `{round_id}` | {value['rows']} | {value['scored_under_its_own_gate']} | "
                   f"{value['scored_under_the_pooled_gate']} | "
                   f"{value['yield_under_its_own_gate']:.1%} | {value['yield_under_the_pooled_gate']:.1%} |")
    out.append("")
    out.append(f"Equivalence: population equivalent {equivalence['population_equivalent']}, "
               f"families pooled {len(equivalence['families_pooled'])}, "
               f"families left split {equivalence['families_left_split'] or 'none'}, "
               f"C0 capture manifests on master {len(equivalence['c0_capture_manifests_on_master'])}.")
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ledger", type=Path, default=ROOT / "artifacts/beaaa-33706/pooled-ledger.json")
    parser.add_argument("--report", type=Path, default=ROOT / "artifacts/beaaa-33706/pooled-vs-split-report.json")
    parser.add_argument("--equivalence", type=Path,
                        default=ROOT / "artifacts/beaaa-33706/pooling-equivalence.json")
    args = parser.parse_args()
    load = lambda path: json.loads(path.read_text(encoding="utf-8"))
    print(render(load(args.ledger), load(args.report), load(args.equivalence)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
