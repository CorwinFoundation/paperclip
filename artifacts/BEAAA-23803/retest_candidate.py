#!/usr/bin/env python3
"""BEAAA-23803 re-test harness for ADR-0067 aligned-checkpoint candidates.

Verification only. Imports nothing from the product beyond the scorer it is
auditing, and writes nothing into the candidate tree.

    python3 artifacts/BEAAA-23803/retest_candidate.py <candidate-sha>

Checks out <candidate-sha> into a throwaway detached worktree and runs the
issue's acceptance criteria plus the seven findings recorded against
39f58cac as executable checks. Exit 0 only when every check passes.

Self-validation: running this against 39f58cac MUST report FAIL on
D1/D2/D3/D4/D5/D7. If it reports PASS on 39f58cac, the harness is broken and
its verdict on any later SHA is worthless.
"""
from __future__ import annotations

import copy
import json
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

FIXTURES = ("B1", "B3", "G1", "G2", "G3", "M1", "M2", "M3")
PILLARS = ("GOVERNANCE", "ROBUSTNESS", "BIAS_FAIRNESS", "OPS")
PINS = ("scanner_version", "rubric_version", "corpus_version", "seed",
        "model_versions", "applicability_policy_version")

results: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    results.append((label, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"\n        {detail}" if detail else ""))


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, check=True).stdout.strip()


def real_scores(tree: Path) -> dict[str, dict]:
    """Re-derive every fixture from the locked eval_outputs with the candidate's own scorer.

    The applicability.json sidecar must be threaded (BEAAA-647); without it
    score() raises ScoringError on these fixtures.
    """
    sys.path.insert(0, str(tree))
    for mod in [m for m in sys.modules if m.split(".")[0] in ("rubric", "scanners", "harness")]:
        del sys.modules[mod]
    from rubric.scoring import Rubric, score  # noqa: E402

    rub = Rubric.load(tree / "rubric" / "rubric.yaml")
    out = {}
    for fid in FIXTURES:
        bdir = tree / "artifacts" / "beaaa-387" / fid
        bl = json.loads((bdir / "baseline.json").read_text())
        ev = json.loads((bdir / "eval_outputs.json").read_text())
        ap = bdir / "applicability.json"
        app = json.loads(ap.read_text()).get("applicability_decisions") if ap.exists() else None
        cur = score(rub, ev, snapshot_hash=bl["snapshot_hash"],
                    forensic_bundle_uri=bl["forensic_bundle_uri"],
                    scored_at=datetime.fromisoformat(bl["scored_at"]), scanner_version=None,
                    corpus_version=bl.get("corpus_version"), seed=bl.get("seed"),
                    model_versions=bl.get("model_versions"), applicability_decisions=app,
                    applicability_policy_version=bl.get("applicability_policy_version"))
        row = {"total": int(cur["bmi"]),
               "pillars": {p: round(float(cur["pillars"][p]["score"]), 4) for p in PILLARS}}
        for pin in PINS:
            row[pin] = cur.get(pin, bl.get(pin))
        out[fid] = row
    return out


def main(sha: str) -> int:
    repo = Path(__file__).resolve().parents[2]
    tmp = Path(tempfile.mkdtemp(prefix="qa23803-"))
    tree = tmp / "tree"
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "--detach", str(tree), sha],
                   capture_output=True, text=True, check=True)
    try:
        print(f"candidate {git(tree, 'rev-parse', 'HEAD')}\n")

        # --- acceptance 7: historical baselines byte-identical (blob-SHA identity,
        # which catches mode and rename changes a path-scoped diff would miss).
        drift = [f for f in FIXTURES
                 if git(tree, "rev-parse", f"{sha}~1:artifacts/beaaa-387/{f}/baseline.json")
                 != git(tree, "rev-parse", f"{sha}:artifacts/beaaa-387/{f}/baseline.json")]
        check("A7 historical baseline byte-identity", not drift, f"drifted: {drift}" if drift else "8/8 blob SHAs unchanged")

        # --- acceptance 1: focused suite + golden-agent regression.
        # Scoped deliberately. The full tests/ tree carries pre-existing failures
        # on master that have nothing to do with this candidate; running it here
        # would emit a false rejection. These two files are the gate's own scope.
        r = subprocess.run([sys.executable, "-m", "pytest", "-q",
                            "tests/test_golden_agents.py",
                            "tests/test_beaaa23802_aligned_checkpoint.py"],
                           cwd=tree, capture_output=True, text=True)
        check("A1 focused suite + regression", r.returncode == 0, r.stdout.strip().splitlines()[-1] if r.stdout else "")

        cp_path = tree / "artifacts" / "beaaa-23802" / "aligned-checkpoint-v1.json"
        if not cp_path.exists():
            check("checkpoint artifact present", False, f"missing {cp_path.relative_to(tree)}")
            return 1
        cp = json.loads(cp_path.read_text())

        sys.path.insert(0, str(tree))
        from harness.golden_set.aligned_checkpoint import compare_aligned_checkpoint  # noqa: E402

        def payload(rows):
            p = {k: cp[k] for k in ("scanner_version", "rubric_version", "scoring_semantics")}
            p["fixtures"] = copy.deepcopy(rows)
            return p

        # --- D1: the anchors must be what the candidate's own scorer produces.
        real = real_scores(tree)
        verdict = compare_aligned_checkpoint(payload(real))
        bad = [f["fixture_id"] for f in verdict["failures"]]
        check("D1 anchors reproduce from the candidate scorer",
              verdict["status"] == "PASS",
              f"{verdict['status']}; breaching fixtures: {bad}" if bad else "real scored output clears the checkpoint")

        # --- D2: the N=5 receipt must have a committed producer and match measurement.
        # The producer grep is a heuristic and over-matches (any file merely naming
        # the artifact counts). It is safe because the numeric equality below is the
        # binding half of the `and` — a bogus producer alone can never yield a PASS.
        n5 = tree / "artifacts" / "beaaa-23802" / "n5-calibration-delta.json"
        producers = [p for p in tree.rglob("*.py")
                     if "n5-calibration-delta" in p.read_text(errors="ignore")
                     and "artifacts/BEAAA-23803" not in str(p)]
        worst_total = max(abs(real[f]["total"] - cp["fixtures"][f]["total"]) for f in FIXTURES)
        worst_pillar = max(abs(real[f]["pillars"][p] - cp["fixtures"][f]["pillars"][p])
                           for f in FIXTURES for p in PILLARS)
        claimed = json.loads(n5.read_text()) if n5.exists() else {}
        ok = (bool(producers)
              and claimed.get("max_total_delta") == worst_total
              and float(claimed.get("max_pillar_delta", -1)) == worst_pillar)
        check("D2 N=5 receipt has a producer and matches measurement", ok,
              f"producers={[str(p.relative_to(tree)) for p in producers]}; "
              f"claimed=({claimed.get('max_total_delta')}, {claimed.get('max_pillar_delta')}) "
              f"measured=({worst_total}, {worst_pillar})")

        # --- D3: the comparator must be reachable from the real gate, not just its own test.
        callers = sorted({str(p.relative_to(tree)) for p in
                          [*tree.rglob("*.py"), *tree.rglob("*.yml"), *tree.rglob("*.yaml")]
                          if re.search(r"aligned_checkpoint|compare_aligned_checkpoint",
                                       p.read_text(errors="ignore"))
                          and "aligned_checkpoint.py" not in p.name
                          and "test_beaaa23802" not in p.name
                          and "BEAAA-23803" not in str(p)})
        check("D3 comparator wired into a real gate", bool(callers), f"callers: {callers or 'NONE — unreachable'}")

        # --- D4: mutation sensitivity. Each deleted enforcement must break some test.
        src_path = tree / "harness" / "golden_set" / "aligned_checkpoint.py"
        src = src_path.read_text()
        mutants = {
            "version-alignment check": ('for k in ("scanner_version", "rubric_version", "scoring_semantics")', 'for k in ()'),
            "approved-flag check": ('if not checkpoint.get("approved"):', 'if False:'),
            "PIN_MISMATCH check": ('if any(row[pin] != anchor[pin] for pin in REQUIRED_PINS):', 'if False:'),
            "total-drift term": ('abs(total_delta) > total_tolerance or any(', 'any('),
            "fixture-count completeness": ('and len(observed) == len(expected) ', ''),
        }
        survived = []
        for name, (old, new) in mutants.items():
            if old not in src:
                survived.append(f"{name} (pattern absent — rewrite the mutant)")
                continue
            src_path.write_text(src.replace(old, new))
            m = subprocess.run([sys.executable, "-m", "pytest", "-q",
                                "tests/test_beaaa23802_aligned_checkpoint.py"],
                               cwd=tree, capture_output=True, text=True)
            if m.returncode == 0:
                survived.append(name)
        src_path.write_text(src)
        check("D4 tests are mutation-sensitive", not survived, f"surviving mutants: {survived}")

        # --- D5: the repo's own not-scored convention must fail closed, not just a literal.
        masked = copy.deepcopy(cp["fixtures"])
        for row in masked.values():
            row["reported_not_scored"] = True
            row["scoring_active"] = False
        got = compare_aligned_checkpoint(payload(masked))["status"]
        check("D5 reported_not_scored masking fails closed", got != "PASS",
              f"repo-convention masking returned {got}")

        # --- D6: the movement profile must match the scope the approvals were granted for.
        moved = {f: real[f]["total"] - int(json.loads(
            (tree / "artifacts" / "beaaa-387" / f / "baseline.json").read_text())["bmi"])
            for f in FIXTURES}
        beyond = {f: d for f, d in moved.items() if abs(d) > 1}
        check("D6 movement profile is disclosed", len(beyond) <= 1,
              f"fixtures beyond ±1 vs persisted baseline: {beyond}")

        # --- D7: fail-closed and evaluated results must share a consumer shape.
        blocked = compare_aligned_checkpoint(payload(real), checkpoint_path=tmp / "absent.json")
        check("A4 missing baseline returns BLOCKED_NO_ALIGNED_BASELINE",
              blocked["status"] == "BLOCKED_NO_ALIGNED_BASELINE", blocked["status"])
        missing_keys = sorted(set(verdict) - set(blocked))
        check("D7 fail-closed shape matches evaluated shape", not missing_keys,
              f"keys absent on the fail-closed path: {missing_keys}")

        # --- acceptance 5/6: pins fail closed; B1 empty corpus pin preserved.
        nopin = copy.deepcopy(real)
        del nopin["B1"]["corpus_version"]
        check("A5 missing pins fail closed",
              compare_aligned_checkpoint(payload(nopin))["status"] == "BREACH")
        check("A6 B1 empty corpus pin is an empty string",
              cp["fixtures"]["B1"]["corpus_version"] == "",
              repr(cp["fixtures"]["B1"]["corpus_version"]))

        # --- acceptance 3: an aligned ROBUSTNESS +2 must block.
        plus2 = copy.deepcopy(cp["fixtures"])
        plus2["M3"]["pillars"]["ROBUSTNESS"] += 2
        check("A3 aligned ROBUSTNESS +2 blocks",
              compare_aligned_checkpoint(payload(plus2))["status"] == "BREACH")
    finally:
        subprocess.run(["git", "-C", str(repo), "worktree", "remove", "--force", str(tree)],
                       capture_output=True)
        subprocess.run(["git", "-C", str(repo), "worktree", "prune"], capture_output=True)

    failed = [label for label, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    print("VERDICT: " + ("APPROVED-ELIGIBLE" if not failed else f"CHANGES REQUESTED — {failed}"))
    return 0 if not failed else 1


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
