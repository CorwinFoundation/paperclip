#!/usr/bin/env python3
"""BEAAA-23960 QA: mutation harness against the 99a3205 growth CEL port.

Each mutant breaks exactly one load-bearing property named in the acceptance
scope. A mutant is KILLED if the focused suite fails, SURVIVED if it passes.
Every mutant asserts its own textual application first, so a non-zero exit can
never be credited to the wrong step.
"""
import subprocess, sys
from pathlib import Path

ROOT = Path("/tmp/cand99a3205")
GC = ROOT / "determination/growth_cel.py"
GP = ROOT / "determination/growth_parity.py"
VC = ROOT / "determination/voice_cel.py"
PACK = ROOT / "determination/rules/growth_perils_cel_v1.json"
SUITE = "tests/test_beaaa20177_growth_cel.py"

MUTANTS = [
    # (id, file, old, new, acceptance criterion targeted)
    ("M1-threshold-widen", GC,
     'verdict = "fail" if left > 0 else "pass"',
     'verdict = "fail" if left > 1 else "pass"',
     "AC1/AC6 violation threshold must not move"),
    ("M2-posture-check-drop", GC,
     'if raw["posture"] != POSTURE:\n            raise GrowthCelError("rule pack posture must be monitor_only")',
     'if False:\n            raise GrowthCelError("rule pack posture must be monitor_only")',
     "AC4 no posture other than monitor_only loads"),
    ("M3-rule-key-open", GC,
     'if set(item) != {"intent_id", "expression"}:',
     'if not {"intent_id", "expression"} <= set(item):',
     "AC4 enforce:true rejected at rule level"),
    ("M4-version-pin-drop", GC,
     'if version != expected_version:\n            raise GrowthCelError("rule_pack_version is not the expected pin")',
     'if False:\n            raise GrowthCelError("rule_pack_version is not the expected pin")',
     "AC5 unpinned version must not load"),
    ("M5-forbidden-narrowed", GC,
     'FORBIDDEN_KEYS = VOICE_FORBIDDEN_KEYS | GROWTH_FORBIDDEN_KEYS',
     'FORBIDDEN_KEYS = VOICE_FORBIDDEN_KEYS',
     "AC3 no synthetic content/PII crosses the boundary"),
    ("M6-closed-subset-drop", GC,
     'match = CLOSED_SUBSET.fullmatch(item["expression"])',
     'match = __import__("re").compile(r\'.*e\\.(?P<left>\\w+) > 0 \\? "fail" : "pass"$\').fullmatch(item["expression"])',
     "AC6 port adds no expression capability"),
    ("M7-indeterminate-to-pass", GC,
     'verdict = "indeterminate"\n            observed_count = 0',
     'verdict = "pass"\n            observed_count = 0',
     "AC5/fail-closed: unobserved peril must not read clean"),
    ("M8-intent-transpose", GP,
     '"growth.brand_safety": "brand_safety_signal",\n    "growth.outreach_compliance": "outreach_compliance_missing",',
     '"growth.brand_safety": "outreach_compliance_missing",\n    "growth.outreach_compliance": "brand_safety_signal",',
     "AC6 parity: reference rule identity preserved"),
    ("M9-baseline-collapse", GP,
     'if rule_id in baseline_counts:\n            entry[baseline_field] = _require_counter(\n                baseline_counts[rule_id], f"{rule_id} baseline"\n            )',
     'entry[baseline_field] = _require_counter(baseline_counts.get(rule_id, 0), f"{rule_id} baseline")',
     "AC5 absent baseline must stay indeterminate, not pass"),
    ("M10-stray-counter-allowed", GP,
     'if not reported <= evaluated:',
     'if False:',
     "AC5 counters for rules that did not run must fail closed"),
    ("M11-voice-default-widened", VC,
     'value: Any, path: str = "$", forbidden: frozenset[str] = FORBIDDEN_KEYS',
     'value: Any, path: str = "$", forbidden: frozenset[str] = frozenset()',
     "AC2 voice default unchanged by parameterization"),
    ("M12-pack-posture-enforce", PACK,
     '"posture": "monitor_only"',
     '"posture": "enforce"',
     "AC4 enforce posture rejected at pack level"),
]


def run_suite(target=SUITE):
    p = subprocess.run([sys.executable, "-m", "pytest", "-q", target],
                       cwd=ROOT, capture_output=True, text=True, timeout=900)
    return p.returncode, (p.stdout + p.stderr).strip().splitlines()[-1]


def main():
    rc, last = run_suite()
    print(f"BASELINE growth suite: rc={rc} :: {last}")
    if rc != 0:
        print("ABORT: baseline not green")
        return
    rc2, last2 = run_suite("tests/test_beaaa20004_voice_cel.py")
    print(f"BASELINE voice suite : rc={rc2} :: {last2}\n")

    killed = survived = 0
    for mid, path, old, new, crit in MUTANTS:
        orig = path.read_text(encoding="utf-8")
        if old not in orig:
            print(f"[{mid}] !! NOT APPLIED - anchor text absent; mutant inconclusive")
            continue
        assert orig.count(old) == 1, f"{mid}: anchor not unique"
        path.write_text(orig.replace(old, new), encoding="utf-8")
        try:
            # M11 is a voice-lane property; check it on the voice suite too.
            targets = [SUITE] if mid != "M11-voice-default-widened" else [SUITE, "tests/test_beaaa20004_voice_cel.py"]
            verdicts = []
            for t in targets:
                r, l = run_suite(t)
                verdicts.append((t, r, l))
            dead = any(r != 0 for _, r, _ in verdicts)
        finally:
            path.write_text(orig, encoding="utf-8")
        status = "KILLED  " if dead else "SURVIVED"
        if dead:
            killed += 1
        else:
            survived += 1
        detail = "; ".join(f"{Path(t).name} rc={r} {l}" for t, r, l in verdicts)
        print(f"[{mid}] {status} ({crit})\n    {detail}")

    print(f"\nTOTAL killed={killed} survived={survived}")
    # Confirm restore left the tree pristine.
    d = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                       capture_output=True, text=True)
    print("worktree after restore:", repr(d.stdout.strip()) or "clean")


main()
