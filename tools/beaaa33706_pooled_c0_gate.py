"""BEAAA-33706: gate the C0 zero-build cohort as ONE population, every round.

BEAAA-33673 and BEAAA-33677 round 1 measured the same 18 zero-build families
against the same C0 text gateway, with the same judge configuration, the same
pinned inventory and the same manifest builder, and were then gated as two
separate pools. Splitting one measurement population into two pools starves every
family of observations in BOTH directions: a family's pass and fail rows sit in
different pools, so the BEAAA-33537 / 33607 / 33611 clauses see less spread than
was actually measured, and a family can also keep a tier-1 admission that the
full population would not support.

CEO ruling 2026-09-18, four conditions, and where each one lives:

1. PROVE EQUIVALENCE BEFORE POOLING - ``prove_equivalence``. Same gateway, same
   judge configuration digest, same control regime, same harness lineage with the
   explicit ``judge_config_digest`` fix present on both rounds. A family whose
   identity differs across the rounds it appears in does not pool: it is reported
   and left split. If the population-level identity differs at all, this module
   raises and the ruling lapses unused rather than being worked around.
2. THE UNION IS A STANDING RULE - ``C0_ROUNDS`` is a registry, not a pair.
   ``assert_registry_covers_master`` fails closed when a capture manifest exists
   on master that the registry does not name, so a later round cannot be quietly
   left out of the pool. Adding round 2 is one entry here, not a new module.
3. RUN IT SYMMETRICALLY - ``compare_split_to_pooled`` reports promotions and
   demotions as two counts and never a net. Pooling that can only promote is not
   a gate, so the demotion path is computed from the same committed ledgers as the
   promotion path and both land in the same artifact.
4. THE ROSTER_ROUND CHECK STAYS FAIL-CLOSED - the union is DECLARED as a
   ``RosterCohort`` whose per-case rounds are derived from the pooled manifest's
   own evidence. ``_assert_roster_round_matches_manifest`` still refuses an
   undeclared round, a row relabelled into a round it did not run in, and a
   declared round that carries no evidence. Nothing was loosened.

What this module does NOT do: capture anything, author a control, touch the
rubric or the registry, or price a row. It re-reads committed artifacts and runs
the existing gate over their union.

KNOWN BOUNDARY, stated rather than papered over: the ledger is one row per case,
so a case measured in two rounds cannot become two ledger rows. ``_manifest_evidence``
already raises when two evidence rows claim one dedupe digest, and that raise is
the correct behaviour here - a re-measured case needs a ruling on which
observation the ledger scores, which is not this card.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tools import beaaa31092_content_slice as S
from tools import beaaa33673_zero_build_slice as Z

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts"
CAPTURE_SCHEMA_VERSION = "beaaa-33673-capture-manifest/v1"

# The declared identity of the pooled cohort. Every roster observation in a pooled
# build is keyed by its own capture round; this id names the population those
# rounds belong to, and is what the ledger header carries in place of a single
# round label.
C0_COHORT_ID = "c0-zero-build-cohort"


@dataclass(frozen=True)
class C0Round:
    """One measured round of the C0 zero-build population, as committed on master."""

    round_id: str
    issue: str
    cohort: Path
    manifest: Path
    controls: Path
    ledger: Path


# THE REGISTRY. This is the standing rule of condition 2: the gate computes over
# every round in this list at the then-current master, whichever way it moves rows.
# A new round is one entry; nothing below is keyed to 33673 or 33677.
C0_ROUNDS: tuple[C0Round, ...] = (
    C0Round(
        round_id="beaaa-33673-r1",
        issue="BEAAA-33673",
        cohort=ARTIFACTS / "beaaa-33673/cohort-150.json",
        manifest=ARTIFACTS / "beaaa-33673/run-manifest.json",
        controls=ARTIFACTS / "beaaa-33673/controls.json",
        ledger=ARTIFACTS / "beaaa-33673/ledger.json",
    ),
    C0Round(
        round_id="beaaa-33677-r1",
        issue="BEAAA-33677",
        cohort=ARTIFACTS / "beaaa-33677/cohort-200.json",
        manifest=ARTIFACTS / "beaaa-33677/run-manifest.json",
        controls=ARTIFACTS / "beaaa-33677/controls.json",
        ledger=ARTIFACTS / "beaaa-33677/ledger.json",
    ),
    C0Round(
        round_id="beaaa-33708-r1",
        issue="BEAAA-33708",
        cohort=ARTIFACTS / "beaaa-33708/cohort-200.json",
        manifest=ARTIFACTS / "beaaa-33708/run-manifest.json",
        controls=ARTIFACTS / "beaaa-33708/controls.json",
        ledger=ARTIFACTS / "beaaa-33708/ledger.json",
    ),
)

# The manifest fields that have to agree for two rounds to be one measurement
# population. Not a style check: each one is a way the rounds could be measuring
# different things while reporting in the same shape.
POPULATION_IDENTITY_FIELDS = (
    "schema_version",
    "inventory_sha256",
    "target_id",
    "judge_config_digest",
    "judge_model",
)
# The per-row identity a family carries. A family whose rows disagree on any of
# these across rounds is not one population at family level, whatever the manifest
# header says.
FAMILY_IDENTITY_FIELDS = ("target_id", "model_version", "judge_config_digest")


class EquivalenceError(RuntimeError):
    """The rounds are not one measurement population, so the ruling lapses unused."""


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _rel(path: Path) -> str:
    """Repo-relative where that means anything, the plain path otherwise (tests)."""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def assert_registry_covers_master(rounds: tuple[C0Round, ...] = C0_ROUNDS,
                                  artifacts_root: Path = ARTIFACTS) -> list[str]:
    """Every C0 capture manifest on master must be declared in the registry.

    Condition 2 read strictly. A gate that pools "the rounds someone remembered to
    list" is a special case wearing a registry's clothes: the next round lands, the
    list is not updated, and the pooled gate silently reports on a population that
    is no longer the whole one. Discovery is by the manifest's own schema version,
    so a round cannot hide by being stored under a new directory name.
    """
    declared = {path.resolve() for path in (r.manifest for r in rounds)}
    found = []
    for path in sorted(artifacts_root.glob("*/run-manifest.json")):
        try:
            manifest = _load(path)
        except (ValueError, OSError):
            continue
        if manifest.get("schema_version") != CAPTURE_SCHEMA_VERSION:
            continue
        found.append(path.resolve())
    undeclared = sorted(_rel(path) for path in set(found) - declared)
    if undeclared:
        raise EquivalenceError(
            f"{len(undeclared)} C0 capture manifests on master are not declared in C0_ROUNDS, "
            f"so the pooled gate would not be computing over the full cohort: {undeclared}")
    missing = sorted(str(path) for path in declared - set(found))
    if missing:
        raise EquivalenceError(f"registry names manifests that are not present or not C0 captures: {missing}")
    return [_rel(path) for path in sorted(found)]


def _round_identity(manifest: dict[str, Any]) -> dict[str, Any]:
    return {field: manifest.get(field) for field in POPULATION_IDENTITY_FIELDS}


def _family_identity(evidence_rows: list[dict[str, Any]]) -> dict[str, list[str]]:
    identity: dict[str, set[str]] = {field: set() for field in FAMILY_IDENTITY_FIELDS}
    for row in evidence_rows:
        for field in FAMILY_IDENTITY_FIELDS:
            identity[field].add(str(row.get(field)))
    return {field: sorted(values) for field, values in identity.items()}


def prove_equivalence(rounds: tuple[C0Round, ...] = C0_ROUNDS) -> dict[str, Any]:
    """Condition 1: prove the rounds are one measurement population BEFORE pooling.

    Population level is fail-stop: a disagreement on the gateway, the pinned
    inventory, the judge configuration digest or the judge model means the two
    rounds are not measuring the same thing, and this raises rather than pooling
    what it can. Family level is fail-split: a family whose rows disagree across
    the rounds it appears in is reported and left to its per-round gate.
    """
    manifests = {r.round_id: _load(r.manifest) for r in rounds}
    controls = {r.round_id: _load(r.controls) for r in rounds}
    identities = {rid: _round_identity(m) for rid, m in manifests.items()}

    distinct = {json.dumps(value, sort_keys=True) for value in identities.values()}
    population_equivalent = len(distinct) == 1
    if not population_equivalent:
        raise EquivalenceError(
            "the declared C0 rounds are not one measurement population: "
            + json.dumps(identities, indent=1, sort_keys=True))

    # Harness lineage: the explicit judge_config_digest fix (BEAAA-33673) has to be
    # present on BOTH rounds. Its absence is not cosmetic - _family_judge_digest's
    # surrogate returns None and rejects every control, so a round captured before
    # the fix has a dead control channel and its spread is not comparable.
    lineage: dict[str, Any] = {}
    for rid, manifest in manifests.items():
        rows = manifest.get("evidence_rows") or []
        explicit = sum(1 for row in rows if row.get("judge_config_digest") == manifest.get("judge_config_digest"))
        lineage[rid] = {
            "evidence_rows": len(rows),
            "rows_carrying_the_manifest_judge_digest": explicit,
            "explicit_judge_digest_fix_present": explicit == len(rows) and bool(rows),
        }
    missing_fix = sorted(rid for rid, value in lineage.items() if not value["explicit_judge_digest_fix_present"])
    if missing_fix:
        raise EquivalenceError(
            f"these rounds predate the explicit judge_config_digest fix and cannot pool: {missing_fix}")

    # Control regime: same target, same judge configuration, target-measured, and a
    # capture round of its own. Two rounds whose controls share a round label would
    # collapse into one observation key in the span clause.
    control_regime: dict[str, Any] = {}
    control_rounds: dict[str, set[str]] = {}
    for rid, records in controls.items():
        manifest = manifests[rid]
        rounds_seen = {str(record.get("capture_round")) for record in records}
        control_rounds[rid] = rounds_seen
        control_regime[rid] = {
            "controls": len(records),
            "capture_rounds": sorted(rounds_seen),
            # A round that authored no control is vacuously consistent with its own
            # manifest; it simply brings no control observations to the union.
            "same_target": all(r.get("target_id") == manifest["target_id"] for r in records),
            "same_judge_config": all(
                r.get("judge_config_digest") == manifest["judge_config_digest"] for r in records),
            "target_measured": all(r.get("acquisition_basis") == "target-measured" for r in records),
        }
    bad_regime = sorted(rid for rid, value in control_regime.items()
                        if not (value["same_target"] and value["same_judge_config"] and value["target_measured"]))
    if bad_regime:
        raise EquivalenceError(f"these rounds' control regimes do not match their own manifests: {bad_regime}")
    shared = [rid for rid in control_rounds
              for other in control_rounds if other != rid and control_rounds[rid] & control_rounds[other]]
    if shared:
        raise EquivalenceError(
            f"control capture rounds are shared across rounds, so they would not count as distinct "
            f"observations: {sorted(set(shared))}")

    # Family level.
    families_by_round: dict[str, dict[str, dict[str, list[str]]]] = {}
    for rid, manifest in manifests.items():
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in manifest.get("evidence_rows") or []:
            grouped[row["family_id"]].append(row)
        families_by_round[rid] = {family: _family_identity(rows) for family, rows in grouped.items()}

    all_families = sorted({family for value in families_by_round.values() for family in value})
    pooled_families, split_families = [], []
    family_report: dict[str, Any] = {}
    for family in all_families:
        present = {rid: value[family] for rid, value in families_by_round.items() if family in value}
        distinct_family = {json.dumps(value, sort_keys=True) for value in present.values()}
        agrees = len(distinct_family) == 1
        family_report[family] = {
            "rounds": sorted(present),
            "identity_agrees_across_rounds": agrees,
            "identity": present,
        }
        (pooled_families if agrees else split_families).append(family)

    return {
        "population_identity": identities[rounds[0].round_id],
        "population_equivalent": population_equivalent,
        "harness_lineage": lineage,
        "control_regime": control_regime,
        "families_pooled": pooled_families,
        "families_left_split": split_families,
        "family_identity": family_report,
    }


def pool_inputs(rounds: tuple[C0Round, ...] = C0_ROUNDS,
                families_left_split: list[str] | None = None) -> dict[str, Any]:
    """The union of the declared rounds' committed cohorts, manifests and controls.

    A family left split by ``prove_equivalence`` is dropped from the union here, so
    it keeps being gated by its own round rather than being pooled on a population
    identity it does not share.
    """
    split = set(families_left_split or [])
    cohort_ids: list[str] = []
    evidence_rows: list[dict[str, Any]] = []
    controls: list[dict[str, Any]] = []
    manifest_header: dict[str, Any] = {}
    rounds_by_case: dict[str, str] = {}
    for entry in rounds:
        manifest = _load(entry.manifest)
        manifest_header = manifest_header or {field: manifest.get(field) for field in POPULATION_IDENTITY_FIELDS}
        family_of = {row["stable_case_id"]: row["family_id"] for row in manifest.get("evidence_rows") or []}
        for case_id in Z._cohort_ids(entry.cohort):
            if family_of.get(case_id) in split:
                continue
            cohort_ids.append(case_id)
        for row in manifest.get("evidence_rows") or []:
            if row["family_id"] in split:
                continue
            evidence_rows.append(row)
            rounds_by_case[row["stable_case_id"]] = row["capture_round"]
        controls.extend(record for record in _load(entry.controls) if record.get("family_id") not in split)
    manifest = {**manifest_header, "evidence_rows": evidence_rows}
    return {
        "cohort_ids": cohort_ids,
        "manifest": manifest,
        "controls": controls,
        "roster_cohort": Z.RosterCohort(cohort_id=C0_COHORT_ID, rounds_by_case=rounds_by_case),
    }


def build_pooled_ledger(rounds: tuple[C0Round, ...] = C0_ROUNDS,
                        inventory_path: Path = Z.PINNED_INVENTORY_PATH) -> tuple[dict[str, Any], dict[str, Any]]:
    """Prove equivalence, then run the existing gate over the union. In that order."""
    equivalence = prove_equivalence(rounds)
    pooled = pool_inputs(rounds, equivalence["families_left_split"])
    ledger = Z.build_ledger_from_objects(
        pooled["cohort_ids"],
        S._jsonl(inventory_path),
        pooled["manifest"],
        pooled["controls"],
        roster_round=pooled["roster_cohort"],
    )
    return ledger, equivalence


def minority_support(ledger: dict[str, Any]) -> dict[str, Any]:
    """What each tier-1 family's MINORITY verdict actually rests on.

    A promotion count answers "how many rows moved". It does not answer "on what",
    and the two can diverge: a family clears the floor on observation counts, and
    those observations can be thin in ways the clauses do not measure - the same
    target response counted twice under one verdict, or a minority supplied
    entirely by the control channel with no roster row on that side at all.

    Neither is a rule violation. Controls are admitted into the tier decision by
    BEAAA-33537 TASK 1 and the BEAAA-33611 span clause counts control rounds. But a
    reader who is told only "97 promotions" cannot see it, so it is measured here
    and reported next to the promotions rather than left to be discovered later.
    """
    spread = ledger["verdict_spread_gate"]["families"]
    scored = [row for row in ledger["rows"] if row["disposition"] == "scored"]
    support: dict[str, Any] = {}
    for family in ledger["tier1_families"]:
        mix = spread[family]
        verdicts = {verdict: bucket for verdict, bucket in mix.items() if bucket["count"]}
        passes, fails = verdicts.get("pass"), verdicts.get("fail")
        minority_verdict = ("pass" if passes and fails and passes["count"] <= fails["count"]
                            else "fail" if passes and fails else None)
        detail = {}
        for verdict, bucket in sorted(verdicts.items()):
            rows = [row for row in scored
                    if row["family_id"] == family and str(row["verdict"]).casefold() == verdict]
            digests = {S._response_digest(row["recorded_target_response"]) for row in rows}
            rounds = sorted(bucket["rounds"])
            detail[verdict] = {
                "observations": bucket["count"],
                "scored_roster_rows": len(rows),
                "distinct_roster_responses": len(digests),
                "rounds": rounds,
                "roster_rounds": [r for r in rounds if not r.startswith("control:")],
                "control_rounds": [r for r in rounds if r.startswith("control:")],
            }
        minority = detail.get(minority_verdict or "", {})
        support[family] = {
            "minority_verdict": minority_verdict,
            "minority_entirely_control_supplied": bool(minority) and minority["scored_roster_rows"] == 0,
            "minority_at_the_observation_floor": bool(minority)
            and minority["observations"] == S.MIN_MINORITY_OBSERVATIONS,
            "minority_at_the_span_floor": bool(minority)
            and len(minority["rounds"]) == S.MIN_MINORITY_SPAN,
            "by_verdict": detail,
        }
    return support


def _disposition_map(ledger: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {row["stable_case_id"]: row for row in ledger["rows"]}


def compare_split_to_pooled(pooled: dict[str, Any],
                            rounds: tuple[C0Round, ...] = C0_ROUNDS) -> dict[str, Any]:
    """Condition 3: promotions and demotions as two counts, never a net.

    "Before" is the committed per-round ledger on master - the disposition each row
    actually shipped with - not a rebuild of it, so the comparison cannot drift
    from what the programme has on record.
    """
    before: dict[str, dict[str, Any]] = {}
    before_round: dict[str, str] = {}
    for entry in rounds:
        for case_id, row in _disposition_map(_load(entry.ledger)).items():
            before[case_id] = row
            before_round[case_id] = entry.round_id
    after = _disposition_map(pooled)

    promotions, demotions, other_moves, unchanged = [], [], [], 0
    for case_id, row in sorted(after.items()):
        was = before.get(case_id)
        if was is None:
            other_moves.append({"stable_case_id": case_id, "from": None, "to": row["disposition"]})
            continue
        if was["disposition"] == row["disposition"]:
            unchanged += 1
            continue
        move = {
            "stable_case_id": case_id,
            "family_id": row["family_id"],
            "round": before_round[case_id],
            "from": was["disposition"],
            "from_hold_reasons": was.get("hold_reasons") or [],
            "to": row["disposition"],
            "to_hold_reasons": row.get("hold_reasons") or [],
        }
        if was["disposition"] != "scored" and row["disposition"] == "scored":
            promotions.append(move)
        elif was["disposition"] == "scored" and row["disposition"] != "scored":
            demotions.append(move)
        else:
            other_moves.append(move)

    # Every row that is still held, with the literal it is held under. A per-family
    # before/after that reports only the movers would hide the rows the pooled gate
    # still refuses, which is the half of the report that keeps it a gate.
    still_held: dict[str, list[str]] = defaultdict(list)
    for case_id, row in sorted(after.items()):
        if row["disposition"] == "held_reported_not_scored":
            still_held[" + ".join(row["hold_reasons"])].append(case_id)

    families: dict[str, Any] = {}
    for family in sorted({row["family_id"] for row in after.values()}):
        before_rows = [row for case_id, row in before.items()
                       if row["family_id"] == family and case_id in after]
        after_rows = [row for row in after.values() if row["family_id"] == family]
        families[family] = {
            "rows": len(after_rows),
            "scored_before": sum(1 for row in before_rows if row["disposition"] == "scored"),
            "scored_after": sum(1 for row in after_rows if row["disposition"] == "scored"),
            "held_before": sum(1 for row in before_rows if row["disposition"] == "held_reported_not_scored"),
            "held_after": sum(1 for row in after_rows if row["disposition"] == "held_reported_not_scored"),
            "tier1_before": sorted({entry.round_id for entry in rounds
                                    if family in _load(entry.ledger)["tier1_families"]}),
            "tier1_after": family in pooled["tier1_families"],
        }

    # Condition 2's other half, reported rather than asserted: what each round's own
    # yield looks like under the pooled gate, which is the number the stop rule
    # should have been read against.
    per_round_yield = {}
    for entry in rounds:
        case_ids = [case_id for case_id in _disposition_map(_load(entry.ledger)) if case_id in after]
        scored_before = sum(1 for case_id in case_ids if before[case_id]["disposition"] == "scored")
        scored_after = sum(1 for case_id in case_ids if after[case_id]["disposition"] == "scored")
        per_round_yield[entry.round_id] = {
            "rows": len(case_ids),
            "scored_under_its_own_gate": scored_before,
            "scored_under_the_pooled_gate": scored_after,
            "yield_under_its_own_gate": round(scored_before / len(case_ids), 4) if case_ids else None,
            "yield_under_the_pooled_gate": round(scored_after / len(case_ids), 4) if case_ids else None,
        }

    # The control channel moves symmetrically too, and its demotions do not show up
    # as row demotions: a control that was admitted inside its own round can be
    # rejected in the union for duplicating another round's response. Reporting only
    # the row movement would hide that, so the channel is compared record by record.
    control_key = ("family_id", "capture_round", "prompt_sha256", "evidence_ref")

    def _key(record: dict[str, Any]) -> tuple[Any, ...]:
        return tuple(record.get(field) for field in control_key)

    accepted_before, rejected_before = {}, {}
    for entry in rounds:
        channel = _load(entry.ledger).get("verdict_spread_controls") or {}
        accepted_before.update({_key(record): record for record in channel.get("accepted") or []})
        rejected_before.update({_key(record): record for record in channel.get("rejected") or []})
    channel_after = pooled.get("verdict_spread_controls") or {}
    accepted_after = {_key(record): record for record in channel_after.get("accepted") or []}
    rejected_after = {_key(record): record for record in channel_after.get("rejected") or []}
    newly_rejected = [
        {**rejected_after[key], "was": "accepted"}
        for key in sorted(set(rejected_after) - set(rejected_before), key=lambda value: tuple(map(str, value)))
        if key in accepted_before
    ]
    newly_accepted = [
        {**accepted_after[key], "was": "rejected",
         "previous_rejection_reasons": rejected_before[key].get("rejection_reasons")}
        for key in sorted(set(accepted_after) - set(accepted_before), key=lambda value: tuple(map(str, value)))
        if key in rejected_before
    ]
    control_channel = {
        "supplied_before": len(accepted_before) + len(rejected_before),
        "supplied_after": channel_after.get("supplied"),
        "accepted_before": len(accepted_before),
        "accepted_after": len(accepted_after),
        "rejected_before": len(rejected_before),
        "rejected_after": len(rejected_after),
        "rejection_reasons_before": dict(Counter(
            reason for record in rejected_before.values() for reason in record["rejection_reasons"])),
        "rejection_reasons_after": dict(Counter(
            reason for record in rejected_after.values() for reason in record["rejection_reasons"])),
        "accepted_in_its_own_round_but_rejected_in_the_union": newly_rejected,
        "rejected_in_its_own_round_but_accepted_in_the_union": newly_accepted,
    }

    return {
        "rows_compared": len(after),
        "minority_support": minority_support(pooled),
        "control_channel": control_channel,
        "promotions": len(promotions),
        "demotions": len(demotions),
        "other_disposition_moves": len(other_moves),
        "unchanged": unchanged,
        "promoted_rows": promotions,
        "demoted_rows": demotions,
        "other_moved_rows": other_moves,
        "still_held_by_literal": {literal: sorted(cases) for literal, cases in sorted(still_held.items())},
        "still_held_total": sum(len(cases) for cases in still_held.values()),
        "per_family": families,
        "per_round_yield": per_round_yield,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, default=Z.PINNED_INVENTORY_PATH)
    parser.add_argument("--output", type=Path, default=ARTIFACTS / "beaaa-33706/pooled-ledger.json")
    parser.add_argument("--equivalence-output", type=Path,
                        default=ARTIFACTS / "beaaa-33706/pooling-equivalence.json")
    parser.add_argument("--report-output", type=Path,
                        default=ARTIFACTS / "beaaa-33706/pooled-vs-split-report.json")
    args = parser.parse_args()

    covered = assert_registry_covers_master()
    ledger, equivalence = build_pooled_ledger(inventory_path=args.inventory)
    equivalence["c0_capture_manifests_on_master"] = covered
    report = compare_split_to_pooled(ledger)

    for path, payload in ((args.output, ledger),
                          (args.equivalence_output, equivalence),
                          (args.report_output, report)):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8")

    summary = {
        "cohort_id": ledger["roster_capture_round"],
        "rounds_pooled": ledger["roster_capture_rounds"],
        "families_left_split": equivalence["families_left_split"],
        **{key: ledger[key] for key in ("selected", "mapped", "evidenced", "scored", "held", "dropped_duplicate")},
        "tier1_families": ledger["tier1_families"],
        "promotions": report["promotions"],
        "demotions": report["demotions"],
        "other_disposition_moves": report["other_disposition_moves"],
        "still_held_by_literal": {literal: len(cases)
                                  for literal, cases in report["still_held_by_literal"].items()},
        "families_whose_minority_is_entirely_control_supplied": sorted(
            family for family, value in report["minority_support"].items()
            if value["minority_entirely_control_supplied"]),
        "per_round_yield": report["per_round_yield"],
        "controls": {"supplied": ledger["verdict_spread_controls"]["supplied"],
                     "accepted": len(ledger["verdict_spread_controls"]["accepted"]),
                     "rejected": len(ledger["verdict_spread_controls"]["rejected"]),
                     "rejection_reasons": dict(Counter(
                         reason for record in ledger["verdict_spread_controls"]["rejected"]
                         for reason in record["rejection_reasons"]))},
    }
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
