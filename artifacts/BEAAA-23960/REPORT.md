# BEAAA-23960 — QA gate R1: BEAAA-20177 growth-agent production CEL port

**Verdict: APPROVED**
**Candidate under test:** `99a320509a2dc7fe6ddafad07cf5e1b1541e5768`
**Baseline in candidate:** `ee8af4cded5d3db987b147e2c158d13bcd3f4e66`
**Tested by:** QA Engineer, 2026-08-17
**Scope:** merge of the exact SHA to canonical master. No shared/production promotion.

## What I did

Fetched the candidate into a detached worktree (`/tmp/qa23960/cand`), confirmed a clean
tree at `99a3205`, read every changed file, ran both acceptance suites, ran 16 planted
mutants against the suites, regenerated the N=5 receipt from my own run, and built and
tested the actual merge result against current master.

## Acceptance criteria — all six pass

| # | Criterion | Result |
|---|---|---|
| 1 | `pytest -q tests/test_beaaa20177_growth_cel.py` = 223 passed | **223 passed** in 1.66s |
| 2 | `pytest -q tests/test_beaaa20004_voice_cel.py` = 149 passed | **149 passed** in 0.62s |
| 3 | No synthetic content / fake PII reaches the production boundary | PASS — see below |
| 4 | Monitor-only; `enforce: true` rejected at rule and pack level | PASS — 3 mutants killed |
| 5 | Version pin, promotion preserves verdicts, rollback exact | PASS — tests read, genuine |
| 6 | Parity vs the pinned appliance engine; numbat baseline unmodified | PASS — engine executed |

### Criterion 6 — parity is against the real engine, not a restatement

The suite loads the reference appliance's own engine and pipeline by file path
(`importlib.util.spec_from_file_location`) from
`ops/paperclip/numbat/backbond-appliance-0.1.0/backbond-appliance/appliance/`, then
**executes** them: `reference_pipeline.normalize(...)` followed by
`reference_rules.determinations(event)`. This is not a stub and not a restatement.

`git diff --stat ee8af4c..99a3205 -- ops/paperclip/numbat/` is **empty** — the pinned
appliance baseline is untouched.

The reference pack declares exactly six rule ids, which I enumerated from the YAML
directly rather than trusting the mapping:
`growth.unauthorized_spend`, `growth.outreach_compliance`, `growth.brand_safety`,
`growth.impersonation`, `growth.crm_data_misuse`, `growth.off_scope_channel`.
`GROWTH_RULE_TO_INTENT` is one-for-one with that set, with no rule silently dropped.

The suite defends against the tautology I look for here: `EVENT_EXPECTED_INTENT` is
written out literally rather than derived from the mapping under test, and
`test_reference_rule_identity_is_preserved_by_the_mapping` anchors on the reference
rule's own declared tags. A transposed mapping cannot agree with itself. I confirmed
this empirically — mutant M2 transposes two mappings and is killed.

### Criterion 3 — no content crosses the boundary

Fake PII and synthetic copy exist only in the test file. Neither reaches shipped code:

```
'078-05-1120'        in determination/: NONE
'victim@example.com' in determination/: NONE
'buyer@example.com'  in determination/: NONE
'get rich'           in determination/: NONE
```
Repo-wide, `078-05-1120` appears in `tests/test_beaaa20177_growth_cel.py` only.

### Criterion 2 — the voice change is default-preserving

`reject_forbidden_keys` gains a third parameter defaulting to `FORBIDDEN_KEYS`, the
lane's existing set, so released behaviour is unchanged. The rest of the voice diff
exposes `CLOSED_SUBSET` / `COMMITMENT_REF_PATTERN` as aliases of the existing private
regexes. No evaluator widening.

## Mutation testing — 15 of 16 killed

"Tests pass" is not evidence the tests bind. I planted 16 defects; 15 fail the suites.

| Mutant | Killed |
|---|---|
| M1 weaken brand_safety threshold `>0` → `>1` | yes — 4 failed, 102 errors |
| M2 transpose brand_safety ↔ impersonation mapping | yes — 5 failed |
| M3 pack posture `monitor_only` → `enforce` | yes — 5 failed |
| M4 add `enforce: true` to a rule | yes — 4 failed |
| M5 unobserved rule treated as zero (indeterminate → pass) | yes — 1 failed |
| M6 pack at unpinned version 1.1.0 | yes — 7 failed |
| M7 drop `flagged_terms` from forbidden vocabulary | yes — 1 failed |
| M8 voice `reject_forbidden_keys` default → empty | yes — 101 failed |
| M9 evaluate boundary `left>0` → `left>1` | yes — 22 failed |
| **M10 remove content-key check on `evaluate()` input** | **NO — 223 passed** |
| M11 emit the evidence map in the determination output | yes — 1 failed |
| M12 `count_determinations` carries determination titles | yes — 12 failed |
| M13 remove closed-CEL-subset enforcement at load | yes — 1 failed |
| M14 rubric label off the `GROWTH.` namespace | yes — 1 failed |
| M15 remove content screen on partner counter maps | yes — 1 failed |
| M16 loader accepts any posture | yes — 5 failed |

M11 and M12 are the important kills: they confirm the no-content invariant test would
actually catch content crossing the boundary, rather than passing vacuously.

## Receipt provenance — regenerates byte-identically

The committed delta records `candidate_commit: "self"` rather than a pinned SHA, so the
receipt cannot bind itself to `99a3205` by inspection. I tested the reproducibility claim
instead by regenerating it from my own run at the candidate tree:

```
python3 scripts/release_gate_n5.py \
  --baseline ee8af4cded5d3db987b147e2c158d13bcd3f4e66 \
  --candidate HEAD --phase development --today 2026-08-17 \
  --out-dir /tmp/qa23960/regen
```

Both files are **byte-identical** to the committed versions (`diff` clean, 34886 and
11026 bytes). Gate booleans: `passed: true`, `behavioral.passed: true`,
`material_deltas: []`, `failure_reasons: []`. B2 excluded per standing BEAAA-10199.

Δ=0 is structurally expected, not luck: `determination/growth_cel.py` and
`growth_parity.py` are imported by nothing outside themselves and their own test, so no
scoring-path code changed.

## Merge verification

The candidate's base is 85 commits behind master, so I tested what actually ships.
No master commit since `ee8af4cd` touches any of the six files this branch changes
(`git rev-list --count` = 0 for each). The merge is textually clean.

Built the merge of `99a3205` into master `b66694b5` (local merge `c51f049d`):
- all six touched files are **blob-identical** to the candidate;
- growth suite **223 passed**, voice suite **149 passed** on the merge result;
- full suite collects cleanly: **4024 tests collected**, no import errors.

## Findings — recorded, none blocking

**F1 — `evaluate()` content-key check is unpinned by any test (low, coverage).**
Deleting `reject_growth_forbidden_keys(evidence)` from `GrowthCelPack.evaluate` leaves all
223 tests green. The check is genuinely load-bearing in shipped code — I confirmed it
rejects a nested forbidden key that the field allowlist would otherwise permit:

```
evidence={unconfirmed_spend_count: {"email": "victim@example.com"}}
  shipped  -> REJECTED (forbidden key at $.unconfirmed_spend_count.email)
  M10      -> ACCEPTED
```
Shipped behaviour is correct; the gap is that a future edit removing that line would not
be caught. Suggest a regression test pinning it.

**F2 — counter type validation is unreachable when a counter is absent (low, latent).**
In `evaluate`, the `left is None or right is None` branch returns `indeterminate` before
the `type(...) is not int` check runs. A non-integer value under an allowlisted field with
the baseline absent is accepted:

```
evidence={unconfirmed_spend_count: {"foo": "victim@example.com"}}   -> ACCEPTED, indeterminate
evidence={unconfirmed_spend_count: {"foo": ...}, spend_action_count: 1} -> REJECTED
```
No content reaches the emitted output (`observed_count: 0`, no evidence values are
copied), and `reduce_to_evidence` validates every counter on the real path, so there is no
leak. `indeterminate` is also the conservative verdict for incomplete evidence. Recorded
as a hardening item, not a defect.

**F3 — one provenance claim in the ticket description is inaccurate (low, docs).**
The reviewer note states the templated frontmatter fields "have been literals in
`release_gate_n5.py` since its first commit." That is not so: the first commit adding the
file (`59043834`, 2026-07-03) contains zero occurrences of `BEAAA-17963` or
`BEAAA-14867`; they were introduced later by `1776e44c` "BEAAA-17963 align N5 release
contract metadata". The **material** part of the disclosure is true and I verified it —
the fields are pre-existing, belong to an unrelated ticket, and this branch does not touch
`scripts/release_gate_n5.py` at all (empty diff). The guidance not to string-replace them
stands; only the "since its first commit" wording is wrong.

**F4 — stale base, moving target (advisory).**
Master advanced twice while I tested (`6366b77f` → `b66694b5` → `40abb5cb`). Zero drift on
the touched files makes this safe today, but the merge should land promptly rather than
sit.

## Conclusion

Functional, logic, and technical layers pass. The port does what the ticket says: six
perils, monitor-only, content-free, parity proven against the pinned appliance engine
rather than a restatement. Nothing overclaims — the port is not wired into any live
determination path yet, and the ticket does not assert otherwise.

**APPROVED** for merge of `99a320509a2dc7fe6ddafad07cf5e1b1541e5768` to canonical master.
This approval covers merge only. Any promotion to a shared or production environment is a
separate gate.
