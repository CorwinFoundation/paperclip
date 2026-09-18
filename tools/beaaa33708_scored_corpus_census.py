"""BEAAA-33708 DELIVERABLE 1: what the whole scored corpus rests on.

A promotion count says how many rows moved. It does not say what carried them.
``minority_support`` in :mod:`tools.beaaa33706_pooled_c0_gate` answered that for the
tier-1 families of one ledger; this runs the same question over every scored row on
record - the pooled C0 cohort and the earlier slice that fed the programme - and
reports the distribution rather than a verdict.

Nothing here decides anything. There is no threshold in this file, deliberately: the
CEO's ordering on BEAAA-33708 is distribution first, threshold second, so that a power
floor is chosen against the shape of the corpus and not against the identity of the
rows a given number would take.

Read over committed artifacts only. It builds no ledger and moves no disposition.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, NamedTuple

import beaaa31092_content_slice as S

ROOT = Path(__file__).resolve().parents[1]

CONTROL_PREFIX = "control:"


class Slice(NamedTuple):
    slice_id: str
    ledger: Path


# The corpus of record. The per-round BEAAA-33673/33677 ledgers are deliberately absent:
# they were superseded by the pooled ledger under BEAAA-33706, and counting a row under
# both its round ledger and the pool would double-count it.
CORPUS: tuple[Slice, ...] = (
    Slice("beaaa-31092-rank2", ROOT / "artifacts" / "beaaa-31092" / "rank2-ledger.json"),
    Slice("beaaa-33706-pooled-c0", ROOT / "artifacts" / "beaaa-33706" / "pooled-ledger.json"),
)


def _is_control(case: str) -> bool:
    return case.startswith(CONTROL_PREFIX)


def _side(bucket: dict[str, Any]) -> dict[str, Any]:
    """Split one verdict bucket into its roster channel and its control channel."""
    cases = list(bucket.get("cases", []))
    rounds = list(bucket.get("rounds", []))
    roster_cases = [case for case in cases if not _is_control(case)]
    control_cases = [case for case in cases if _is_control(case)]
    roster_rounds = sorted({r for r in rounds if not _is_control(r)})
    control_rounds = sorted({r for r in rounds if _is_control(r)})
    return {
        "observations": bucket.get("count", 0),
        "roster_observations": len(roster_cases),
        "control_observations": len(control_cases),
        "rounds": sorted(rounds),
        "roster_rounds": roster_rounds,
        "control_rounds": control_rounds,
        "span": len(set(rounds)),
    }


def _basis(minority: dict[str, Any] | None) -> str:
    if not minority:
        return "no_minority"
    if minority["roster_observations"] and minority["control_observations"]:
        return "mixed"
    if minority["roster_observations"]:
        return "roster_only"
    return "control_only"


def census_family(family: str, mix: dict[str, Any], tier: str) -> dict[str, Any]:
    """The spread basis and control power behind one family, with margins, not verdicts."""
    sides = {verdict: _side(bucket) for verdict, bucket in mix.items() if bucket.get("count")}
    passes, fails = sides.get("pass"), sides.get("fail")
    if passes and fails:
        minority_verdict = "pass" if passes["observations"] <= fails["observations"] else "fail"
    else:
        minority_verdict = None
    minority = sides.get(minority_verdict) if minority_verdict else None

    total = sum(side["observations"] for side in sides.values())
    roster_total = sum(side["roster_observations"] for side in sides.values())
    control_total = sum(side["control_observations"] for side in sides.values())

    # A third thing the corpus rests on that a pass/fail reading hides. The judge can
    # return a verdict that is neither: `partial` rows exist, they are scored, and the
    # spread gate counts their observations toward MIN_SPREAD_OBSERVATIONS. They cannot
    # reach the minority clauses - the minority is decided between pass and fail alone -
    # so their whole effect is on the total. Reported with the counterfactual attached,
    # because "the family clears the floor" and "the family clears the floor on evidence
    # that picked a side" are different claims and only one of them was being made.
    non_binary = {verdict: side["observations"]
                  for verdict, side in sides.items() if verdict not in ("pass", "fail")}
    binary_total = total - sum(non_binary.values())

    # Control power, stated as a counterfactual rather than a ratio: delete every control
    # observation and ask whether the family still shows two sides at all. A family that
    # answers "no" is one whose discrimination evidence is entirely the control channel.
    roster_sides = [verdict for verdict, side in sides.items() if side["roster_observations"]]

    entry: dict[str, Any] = {
        "family_id": family,
        "tier": tier,
        "total_observations": total,
        "required_total_observations": S.MIN_SPREAD_OBSERVATIONS,
        "total_observation_margin": total - S.MIN_SPREAD_OBSERVATIONS,
        "roster_observations": roster_total,
        "control_observations": control_total,
        "non_binary_observations": dict(sorted(non_binary.items())),
        "total_observations_binary_only": binary_total,
        "clears_total_floor_without_non_binary": binary_total >= S.MIN_SPREAD_OBSERVATIONS,
        "minority_verdict": minority_verdict,
        "spread_basis": _basis(minority),
        "two_sided_without_controls": len(roster_sides) >= 2,
        "by_verdict": {verdict: sides[verdict] for verdict in sorted(sides)},
    }
    if minority:
        entry.update({
            "minority_observations": minority["observations"],
            "required_minority_observations": S.MIN_MINORITY_OBSERVATIONS,
            "minority_observation_margin": minority["observations"] - S.MIN_MINORITY_OBSERVATIONS,
            "minority_roster_observations": minority["roster_observations"],
            "minority_control_observations": minority["control_observations"],
            "minority_span": minority["span"],
            "required_minority_span": S.MIN_MINORITY_SPAN,
            "minority_span_margin": minority["span"] - S.MIN_MINORITY_SPAN,
            "minority_control_rounds": minority["control_rounds"],
            "minority_roster_rounds": minority["roster_rounds"],
        })
    else:
        entry.update({
            "minority_observations": 0,
            "required_minority_observations": S.MIN_MINORITY_OBSERVATIONS,
            "minority_observation_margin": -S.MIN_MINORITY_OBSERVATIONS,
            "minority_roster_observations": 0,
            "minority_control_observations": 0,
            "minority_span": 0,
            "required_minority_span": S.MIN_MINORITY_SPAN,
            "minority_span_margin": -S.MIN_MINORITY_SPAN,
            "minority_control_rounds": [],
            "minority_roster_rounds": [],
        })
    return entry


def census_slice(entry: Slice) -> dict[str, Any]:
    ledger = json.loads(entry.ledger.read_text(encoding="utf-8"))
    spread = ledger["verdict_spread_gate"]["families"]
    tier1 = set(ledger.get("tier1_families") or [])
    tier2 = set(ledger.get("tier2_held_families") or [])
    scored = [row for row in ledger["rows"] if row["disposition"] == "scored"]

    families: dict[str, dict[str, Any]] = {}
    for family in sorted({row["family_id"] for row in scored}):
        tier = "tier1" if family in tier1 else "tier2_held" if family in tier2 else "ungraded"
        families[family] = census_family(family, spread.get(family, {}), tier)
        families[family]["scored_rows"] = sum(1 for row in scored if row["family_id"] == family)

    rows = [{
        "slice_id": entry.slice_id,
        "stable_case_id": row["stable_case_id"],
        "family_id": row["family_id"],
        "rubric_point_id": row.get("rubric_point_id"),
        "verdict": row.get("verdict"),
        **{key: families[row["family_id"]][key] for key in (
            "tier", "spread_basis", "minority_verdict", "total_observations",
            "total_observation_margin", "minority_observations",
            "minority_observation_margin", "minority_span", "minority_span_margin",
            "minority_roster_observations", "minority_control_observations",
            "two_sided_without_controls", "total_observations_binary_only",
            "clears_total_floor_without_non_binary")},
        "verdict_is_binary": str(row.get("verdict")).casefold() in ("pass", "fail"),
    } for row in sorted(scored, key=lambda r: (r["family_id"], r["stable_case_id"]))]

    return {
        "slice_id": entry.slice_id,
        "slice_name": ledger.get("slice_name"),
        "ledger_path": str(entry.ledger.relative_to(ROOT)),
        "scored_rows": len(scored),
        "families": families,
        "rows": rows,
    }


def _bucket(margin: int) -> str:
    return "at_floor" if margin == 0 else "below_floor" if margin < 0 else f"+{margin}"


def build_census(corpus: tuple[Slice, ...] = CORPUS) -> dict[str, Any]:
    slices = [census_slice(entry) for entry in corpus]
    rows = [row for entry in slices for row in entry["rows"]]
    families = [family for entry in slices for family in entry["families"].values()]

    def distribution(key: str, source: list[dict[str, Any]]) -> dict[str, int]:
        out: dict[str, int] = {}
        for item in source:
            out[str(item[key])] = out.get(str(item[key]), 0) + 1
        return dict(sorted(out.items()))

    def margins(key: str, source: list[dict[str, Any]]) -> dict[str, int]:
        out: dict[str, int] = {}
        for item in source:
            out[_bucket(item[key])] = out.get(_bucket(item[key]), 0) + 1
        return dict(sorted(out.items(), key=lambda kv: (kv[0] != "below_floor", kv[0] != "at_floor", kv[0])))

    return {
        "schema_version": "beaaa33708-census-v1",
        "floors": {
            "min_spread_observations": S.MIN_SPREAD_OBSERVATIONS,
            "min_minority_observations": S.MIN_MINORITY_OBSERVATIONS,
            "min_minority_span": S.MIN_MINORITY_SPAN,
        },
        "totals": {
            "scored_rows": len(rows),
            "families_carrying_scored_rows": len(families),
        },
        "distribution_by_row": {
            "spread_basis": distribution("spread_basis", rows),
            "tier": distribution("tier", rows),
            "minority_observation_margin": margins("minority_observation_margin", rows),
            "minority_span_margin": margins("minority_span_margin", rows),
            "total_observation_margin": margins("total_observation_margin", rows),
            "two_sided_without_controls": distribution("two_sided_without_controls", rows),
            "verdict_is_binary": distribution("verdict_is_binary", rows),
            "clears_total_floor_without_non_binary": distribution(
                "clears_total_floor_without_non_binary", rows),
        },
        "distribution_by_family": {
            "spread_basis": distribution("spread_basis", families),
            "tier": distribution("tier", families),
            "minority_observation_margin": margins("minority_observation_margin", families),
            "minority_span_margin": margins("minority_span_margin", families),
            "two_sided_without_controls": distribution("two_sided_without_controls", families),
            "clears_total_floor_without_non_binary": distribution(
                "clears_total_floor_without_non_binary", families),
        },
        "slices": slices,
    }


def main() -> None:
    census = build_census()
    out = ROOT / "artifacts" / "beaaa-33708" / "scored-corpus-census.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(census, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in census.items() if k != "slices"}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
