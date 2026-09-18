"""BEAAA-33677: re-check the control admission result WITHOUT the admitting tool.

BEAAA-33673 came back with zero control rejections and that turned out to be a
defect, not a clean sheet: every control had been rejected earlier for a
double-digested judge configuration. The lesson is not "distrust the number", it
is "a zero is only worth what an independent recount says it is". So this script
re-derives each admission condition from the raw artifacts - the controls file,
the roster capture log, the ledger, the pinned inventory - and never calls
``_control_rejections`` or ``_admit_controls``.

Two of the checks are deliberately STRONGER than the ones they audit:

* Response independence is tested under casefold + whitespace collapse. The
  product compares raw sha256. A normalisation strictly more aggressive than the
  product's finding no collisions implies the product's finds none either, so a
  clean result here is a real bound and not a restatement.
* Oracle-digest agreement is reported alongside how many DISTINCT oracle digests
  the cohort actually carries. A per-case check that every case in a family
  shares one digest would pass vacuously; the distinctness count is what says
  whether the check can fail at all.

Exit status is 1 if the independent recount disagrees with the ledger's own
control report, or if any check turns out to be vacuous.
"""
import hashlib, json, re, sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import beaaa31092_content_slice as S

ALLOWED = {"pass", "fail", "partial"}
REQUIRED = ("family_id", "verdict", "evidence_ref", "recorded_target_response",
            "target_id", "status", "acquisition_basis", "oracle_digest")


def _count(value):
    """The control report writes some members as lists and some as plain counts."""
    if value is None:
        return 0
    return value if isinstance(value, int) else len(value)


def hard(text):
    """Strictly more aggressive than the product's raw sha256."""
    collapsed = re.sub(r"\s+", " ", (text or "")).strip().casefold()
    return hashlib.sha256(collapsed.encode("utf-8")).hexdigest()


def main(controls_path, ledger_path, manifest_path):
    controls = json.loads(Path(controls_path).read_text(encoding="utf-8"))
    ledger = json.loads(Path(ledger_path).read_text(encoding="utf-8"))
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    inventory = {r["stable_case_id"]: r for r in S._jsonl(S.PINNED_INVENTORY_PATH)}
    # Bindability, re-derived rather than imported: a control may bind to a row the
    # ledger would score, which on an already-demoted ledger includes a row held
    # ONLY for want of spread (BEAAA-33596). A row held for any fail-closed reason
    # stays unbindable. SPREAD_HOLD_REASONS is read as data, not as a predicate.
    spread_only = set(S.SPREAD_HOLD_REASONS)
    bindable = {}
    for r in ledger["rows"]:
        if r["disposition"] == "scored":
            bindable[r["stable_case_id"]] = r
        elif (r["disposition"] == "held_reported_not_scored" and r["hold_reasons"]
              and all(reason in spread_only for reason in r["hold_reasons"])):
            bindable[r["stable_case_id"]] = r
    scored = bindable

    # The roster a control must differ from is every row of its family that the run
    # MEASURED, read off the manifest's evidence rows - the raw artifact, before any
    # ledger disposition is applied. Keying this on ``disposition == "scored"``, as
    # this script first did, makes the check blind to every held row's response:
    # BEAAA-33677 round 1 rejected 7 controls bound into families where nothing
    # scored, and this recount admitted all 7. That was a gap in the auditor, not a
    # finding against the product. Reading the manifest keeps the population
    # independent of the ledger AND at least as wide as the one the product uses.
    roster_hard = defaultdict(lambda: defaultdict(set))   # family -> hard digest -> verdicts
    for row in manifest["evidence_rows"]:
        roster_hard[row["family_id"]][hard(row.get("recorded_target_response"))].add(
            str(row.get("verdict")).casefold())

    judge_digests = {c.get("judge_config_digest") for c in controls}
    manifest_digest = manifest.get("judge_config_digest")
    if judge_digests and judge_digests != {manifest_digest}:
        print("NOTE: control judge digests do not match the manifest's", file=sys.stderr)

    admitted_hard = defaultdict(dict)                     # family -> hard digest -> verdict
    rejected, admitted = {}, []
    for c in controls:
        fam, case = c.get("family_id"), c.get("bound_case_id")
        why = [f"missing_{f}" for f in REQUIRED if c.get(f) in (None, "")]
        if c.get("status") != "complete":
            why.append("nonterminal_evidence")
        if c.get("acquisition_basis") != "target-measured":
            why.append("provenance_mismatch")
        if int(c.get("target_calls") or 0) < 1:
            why.append("unsupported_target_capability")
        if int(c.get("n") or 0) < 1:
            why.append("unsupported_observation_count")
        verdict = str(c.get("verdict", "")).casefold()
        if verdict not in ALLOWED:
            why.append("unsupported_verdict")
        if case not in inventory:
            why.append("bound_case_not_in_family")
        elif case not in scored:
            why.append("bound_case_not_scored")
        elif c.get("oracle_digest") != S._digest(
                {f: inventory[case].get(f) for f in S.ORACLE_IDENTITY_FIELDS}):
            why.append("oracle_digest_mismatch")
        if len(judge_digests) != 1:
            why.append("family_has_no_single_judge_configuration")
        digest = hard(c.get("recorded_target_response"))
        if digest in roster_hard[fam]:
            why.append("roster_response_collision")
        if digest in admitted_hard[fam]:
            why.append("control_response_collision")
        if why:
            rejected[c.get("evidence_ref") or case] = sorted(set(why))
        else:
            admitted.append(c)
            admitted_hard[fam][digest] = verdict

    # Non-vacuity: a check that cannot fail is not a check.
    oracle_digests = {S._digest({f: inventory[k].get(f) for f in S.ORACLE_IDENTITY_FIELDS})
                      for k in scored}
    # Counted per ROW, not per digest key: collapsing to keys first would hide
    # exactly the within-family response sharing that makes the collision check
    # worth running, and would report a live check as an idle one.
    per_family_rows = Counter()
    for row in ledger["rows"]:
        if row["stable_case_id"] in bindable:
            per_family_rows[(row["family_id"], hard(row["recorded_target_response"]))] += 1
    report = {
        "controls_read": len(controls),
        "independently_admitted": len(admitted),
        "independently_rejected": len(rejected),
        "rejection_reasons": rejected,
        "ledger_accepted": _count((ledger.get("verdict_spread_controls") or {}).get("accepted")),
        "ledger_rejected": _count((ledger.get("verdict_spread_controls") or {}).get("rejected")),
        "ledger_supplied": _count((ledger.get("verdict_spread_controls") or {}).get("supplied")),
        "judge_config_digests_seen": sorted(d for d in judge_digests if d),
        "manifest_judge_config_digest": manifest_digest,
        "nonvacuity": {
            "distinct_oracle_digests_over_scored_rows": len(oracle_digests),
            "bindable_rows": len(bindable),
            "distinct_family_response_pairs_over_bindable_rows": len(per_family_rows),
            "bindable_rows_sharing_a_response_within_their_family":
                sum(count for count in per_family_rows.values() if count > 1),
        },
    }
    print(json.dumps(report, indent=1, sort_keys=True))

    problems = []
    if len(oracle_digests) < 2:
        problems.append("oracle-digest check is vacuous: the cohort carries one oracle digest")
    if per_family_rows and max(per_family_rows.values()) == 1:
        problems.append("response-collision check is idle: no two bindable rows in any "
                        "family share a response, so a control cannot collide with one")
    if report["ledger_accepted"] != len(admitted):
        problems.append(
            f"recount disagrees: ledger accepted {report['ledger_accepted']}, "
            f"independent recount admitted {len(admitted)}")
    if report["ledger_supplied"] != len(controls):
        problems.append(
            f"ledger saw {report['ledger_supplied']} controls, the controls file holds {len(controls)}")
    for problem in problems:
        print("DISAGREE:", problem, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(*sys.argv[1:4]))
