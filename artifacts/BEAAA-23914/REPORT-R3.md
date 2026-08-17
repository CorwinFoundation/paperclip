# BEAAA-23914 R3 — QA gate: CI drift-gate node-id repoint `b66694b5`

**Result: APPROVED** for exact SHA `b66694b546c588ed2ab9461bb690f6a742644d62`.

| | |
|---|---|
| Candidate | `b66694b546c588ed2ab9461bb690f6a742644d62` |
| Branch | `agent/beaaa-23903-gate-nodeid-fix` |
| Sole parent | `6366b77f` — confirmed current canonical master at the remote |
| Surface | `scripts/authoritative_probe_registry_integration_gate.sh`, 1 insertion / 1 deletion |
| Prior rounds | R1 `2e28a2cd` ✅, R2 `6366b77f` ✅ (promoted) |
| Tested | 2026-08-17, worktrees `/tmp/qa23914r3/{cand,base}`, Python 3.12.3 |

Disclosure: `b66694b5` reads author `QA <qa@backbond.invalid>` — shared VM git config, not authorship.
The producer wrote it; I tested it. Master head re-confirmed via GitHub (`list_commits`), not from local refs,
which have been unreliable throughout this ticket: `6366b77f` is head, `b66694b5` is its direct child.

## I missed this in R1 and R2

The rename `test_legacy_2202_… -> test_legacy_2212_…` shipped in `2e28a2cd`, which I approved, and rode
through the merge I approved in R2. Both rounds I ran the *module*
(`pytest tests/test_authoritative_probe_registry_beaaa20357.py`), which passes, and never ran
`scripts/authoritative_probe_registry_integration_gate.sh`, which invokes individual tests by pinned pytest
node id. My R1 report even observed that `--check` and the pytest modules catch different things and that
"both halves of the acceptance command are load-bearing" — but the acceptance command was not the CI gate,
and I did not go looking for the wrapper that CI actually runs.

Carried forward: **when a candidate renames a test, sweep for `<file>.py::<name>` pins before approving.**

## The defect, characterized precisely

A/B on a clean tree:

- `6366b77f` (master): `sh scripts/authoritative_probe_registry_integration_gate.sh .` → **exit 4**,
  `ERROR: not found: …::test_legacy_2202_is_not_default_executed_reported_or_scored`
- `b66694b5`: → **exit 0**

That reproduces the CI failure exactly. The important question is what the gate was still guarding, so I
built a mutant that drifts the registry *without* tripping any earlier step: add a probe **and** regenerate the
committed artifact, so `python3 -m tools.probe_registry --check` stays green while `category_counts.executable`
becomes 2213 against the frozen 2212.

| | `6366b77f` (base) | `b66694b5` (candidate) |
|---|---|---|
| Gate exit on drifted tree | **4** | **1** |
| Reason | `ERROR: not found` — assertion never evaluated | `assert 2213 == 2212` — real failure |
| Preceding tests | 9 passed, then collection error | 1 failed, 4 passed |

So the guard on base is genuinely inert: the legacy-count assertion does not execute, whether or not the
registry has drifted.

One precision worth stating, because the producer's framing could be read more strongly than the evidence
supports: base still exits **non-zero**, so CI is red and a drift would not slip through *unnoticed*. What is
broken is that the gate fails for the wrong reason — it can no longer distinguish "the registry drifted" from
"a test got renamed," and the assertion it exists to run does not run. Inert guard and misattributed signal,
not silent admission. The candidate restores it.

Disclosed methodology failure: my first mutant attempt was confounded. `set -eu` plus the script's line 7
(`python3 -m tools.probe_registry --repo . --check`) aborts the whole script before any node id is reached, so
both base and candidate exited 1 for the same irrelevant reason. That mutant proved nothing; the
regenerate-the-artifact version above is the one that isolates the node-id line. (Same trap as R1's first
tamper mutant — a gate exiting non-zero is not evidence until you know *which* step failed.)

## Independent node-id sweep — wider than the producer's

They swept `scripts/` and `.github/`. I extracted every `<path>.py::<name>` occurrence across `scripts/`,
`.github/`, `ci_plugin/`, `ops/`, `tools/`, `bin/`, `deploy/`, `infra/` and `Makefile`: **47 pins, 38 of them
under `tests/`**. I then resolved each one with `pytest --collect-only` against the candidate tree.

**36 of 38 resolve.** The two that do not are prose in historical calibration-delta markdown, not executed by
anything:

- `tools/calibration-deltas/2026-07-15-5e605087-vs-rebased.md` — `…::test_ac1_...`, literally an ellipsis
- `tools/calibration-deltas/2026-07-15-canon-c1debd2-vs-beaaa14431-applicability-validation.md` —
  `…::_current_g1_applicability`, a helper function, not a test

Neither appears in any `.sh`, `.yml`, or `.yaml`. The producer's claim holds: this ticket renamed exactly one
test, and this was the only stale pin in an executed context. The other 7 node ids in the same gate script all
resolve, and the gate's own self-test `test_failed_integration_gate_makes_push_unreachable` passes.

## The golden-set claim is accurate

Verified rather than accepted, since it cites a shortfall against scope numbers I reported on:

- `tests/test_p1_1_factuality_golden.py` + `tests/test_p1_4_refusal_golden.py` on **master `6366b77f`**:
  `7 failed, 8 passed`
- Same two modules on **candidate `b66694b5`**: `7 failed, 8 passed` — identical

Pre-existing, not caused by this candidate, not fixed by it. The failures are `34 != 30` / `26 != 20` and
`'0.2.0' != '0.1.0'` — precisely the ten BEAAA-22121 probes (P1.1 30→34, P1.4 20→26) plus the mapping version
bump. "Roughly 7 assertions across 2 golden modules" is exactly right. Routing to BEAAA-23949 rather than
refreezing corpus-adjacent golden counts under this ticket's authorization is the correct call.

Useful for whoever takes 23949: the golden count-named tests
(`test_yaml_has_30_unique_probes_and_pinned_judge`, `test_yaml_has_20_unique_probes_paired_balanced`) are
referenced only in a historical markdown, not in any executed gate — so renaming them during a golden refreeze
would **not** recreate this bug in CI. It would still be worth re-running the sweep after any rename.

## Verdict

One line, correct, load-bearing, no side effects. Nothing else in the tree changes: `git diff --numstat` is
`1 1 scripts/authoritative_probe_registry_integration_gate.sh`. APPROVED for `b66694b5`.

The BEAAA-22121 drift remains **not fully closed** — `active_registry.json` at 2202 and the golden-set red set
both stand, per BEAAA-23949. This approval covers the node-id repoint only.

## Commands run

```
git worktree add --detach /tmp/qa23914r3/cand b66694b5
git worktree add --detach /tmp/qa23914r3/base 6366b77f
cd base && sh scripts/authoritative_probe_registry_integration_gate.sh .   # exit 4, ERROR: not found
cd cand && sh scripts/authoritative_probe_registry_integration_gate.sh .   # exit 0
# mutant: add probe + regenerate artifact so --check stays green
cand-mut → exit 1, assert 2213 == 2212      base-mut → exit 4, assertion never ran
grep -rhoE '[A-Za-z0-9_/.-]+\.py::[A-Za-z0-9_:]+' scripts/ .github/ ci_plugin/ ops/ tools/ bin/ deploy/ infra/ Makefile
  → 47 pins, 38 under tests/, each resolved with pytest --collect-only  # 36 resolve, 2 are prose
pytest -q tests/test_p1_1_factuality_golden.py tests/test_p1_4_refusal_golden.py   # 7 failed/8 passed on both
```
