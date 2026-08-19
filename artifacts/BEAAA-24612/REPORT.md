# BEAAA-24612 — producer remediation for QA gate BEAAA-18614

    artifact_id: BEAAA-24612-producer-remediation
    owner: CTO (agent 88e68019-1cb6-4c4f-83e1-9fd340fb6821)
    persona_audience: internal
    ticket_ref: BEAAA-24612 (child of canonical gate BEAAA-18614)
    baseline_master: c76d2b9280009b8b40a08cda4780ed4505f2d7be
    prior_candidate: 2b78c9225ee061ae27ccc6f7421dbc409bb49115 (PR #208)
    scanner_version: 0.5.0
    date: 2026-08-19

## Answer to the scoping question first

**No single existing SHA carries both halves, and PR #201 must not be merged.**

QA asked which one exact SHA carries the promotion half and the runner half. The
answer changed while this ticket sat: master moved from `54101263` (quoted in the
ticket) to `c76d2b92`, and somewhere in that range the runner half landed in a
different, superseding form.

| ref | `_execute_aiuc_lane_c_case` | `_declares_mcp_or_a2a` | lane-C runner present |
|---|---|---|---|
| master `c76d2b92` | 0 | 0 | yes — `harness/lane_c_adapter.py` + `_execute_lane_c_case` (BEAAA-23860) |
| PR #201 `56ecf470` | 2 | 2 | yes — the older design |
| PR #208 `2b78c922` | 0 | 0 | no (promotion half only) |

Master's own lane-C runner (ruling BEAAA-23860, commit `53d94594`) replaced the
PR #201 design: rows are re-homed from `content_candidate_worker` to
`bench_wrapper`, seeds come from what the registry row declares, and the send
shape is shared with the capture path. Rebasing PR #201 on top of that would
reintroduce the superseded runner. **PR #201 should be closed, not merged.**

So the single SHA QA gates is the promotion half rebased onto current master,
plus the F6 fix. That is what this ticket produces.

## F6 (Critical) — fixed, with the defect reproduced first

### The defect is live on current master, and worse than the ticket records

Reproduced on unmodified `c76d2b92` via the real production dispatch path
(`RegisteredCaseExecutor._execute_case`), real registry row
`mcp_tool_integrity:mcp-01`, `capability_manifest={}`:

| condition | verdict | score | target calls | judge calls |
|---|---|---|---|---|
| dev flag off | `PASS` | None | 1 | 0 |
| dev flag on | `PASS` | None | 1 | 0 |
| **required** | `not_applicable` | None | **0** | **0** |

QA measured `score=1.0` on `2b78c922`'s content-candidate path; on master the
rows take the `bench_wrapper` path, which returns `score=None` but still probes
the target and reports `PASS`. The score field is no longer the problem. The
problem is that the scanner reports a tool-integrity verdict about an MCP
integration the target never declared.

Script: `artifacts/BEAAA-24612/f6-repro.py`.

### Root cause

`mcp_tool_integrity` is the only lane-C family that declares an applicability
gate. It is declared in three places and enforced in none of the production ones:

- `evals/aiuc_path_b/mcp_tool_integrity.py:21` — `APPLICABILITY_FLAG = "has_mcp_tool_integration"`
- `FamilySpec.applicability_flag`, which `evals.aiuc_path_b.base.run_family`
  honors (`architecture_flags.get(flag, False)` — fail-closed)
- `rubric/probe_mappings/mcp_tool_integrity.yaml` — `applicability.gate_flag`,
  `na_when_absent: true`

`grep` for `gate_flag` and `na_when_absent` across `*.py` returns nothing. The
eval library respects the gate; the production executor never asked.

### The fix

`tools/registered_case_executor.py` resolves applicability at the **dispatch
boundary in `_execute_case`, ahead of every runner branch**, rather than inside
one of them:

- `_declared_applicability_flag()` reads `FamilySpec.applicability_flag` — the
  same field `run_family` uses, so the eval library and production cannot
  disagree. Nothing is hardcoded: no family list, no flag name.
- `_resolve_applicability()` is fail-closed to match `run_family` and
  `na_when_absent: true`. Only an explicit `True` makes a gated family apply.
- `_not_applicable_outcome()` builds the terminal N/A without touching
  `self.subject` or `self.judge`, so the budget ledger records zero calls.

Placing it at the boundary is deliberate. PR #201 put its check inside
`_execute_aiuc_lane_c_case`, which left the content-candidate path — the exact
path QA reproduced on — ungated, and made correctness depend on which branch of
an `if`-chain a row happened to reach.

Post-fix, same script, same three conditions: `not_applicable` / `None` / 0 / 0
on both flag settings, and the positive control (`has_mcp_tool_integration:
True`) still executes with a live target call.

### Adversarial coverage (ruling E)

`tests/test_beaaa24612_mcp_applicability_gate.py`, 14 tests, all green.

Deleting the guard **from the production source** (not monkeypatching the test's
own inputs) and re-pinning `EVALUATOR_SHA256` so the contract check cannot mask
the mutation:

- **10 fail** — every applicability assertion flips `not_applicable` → `PASS`
- **4 still pass** — the positive control, the two ungated-family tests and the
  spec/mapping contract test, i.e. exactly the assertions that must hold whether
  or not the gate exists

Mutation harness: `/tmp/mutate_gate.py`, reproduced in the report appendix.
Both files were restored byte-identically afterwards (`cmp` clean) and the suite
returned to 14/14.

The suite covers six ways a manifest can fail to declare the surface (empty,
absent, explicit `False`, unrelated flag, malformed non-mapping, and the string
`"true"`, which must not count as `True`), both dev-flag settings, and all 12
gated cases rather than just the first.

## F9 (Blocking) — rebased

Resolved onto `c76d2b92`. The three conflicts were mechanical: master round-trip
reformatted the YAMLs and added `execution_evidence` bodies; the candidate adds
fields to the `binding:` block. The binding fields were re-applied to master's
reformatted files with identical content and master's quoting style.

### One deliberate deviation from `2b78c922`, with the measurement behind it

`2b78c922` also changed `tools/active_test_registry.py`: three names added to
`AIUC_PATH_B_FOUNDER_DEV_SCORING_ACTIVE_FAMILIES`, and a hunk in
`_merge_content_candidate_inventory`. **Both are dead on current master and are
not carried.**

- Master generalised `_scoring_active()` / `_reporting_status()` to honour a
  mapping's own `binding.attestation_tier` + `binding.dev_scoring_active`, so
  the frozenset entry is now a second way to say what the YAML already says.
- The `_merge_content_candidate_inventory` hunk targets rows whose runner is
  `content_candidate_worker`. All 44 lane-C rows now declare `bench_wrapper`, so
  that hunk cannot execute for them.

Measured with the YAML binding blocks alone and no registry change:

| | lane-C rows | scoring_active | reporting_status | whole-registry scoring_active |
|---|---|---|---|---|
| baseline, flag off | 44 | 0 | 44 reported_not_scored | 329 |
| baseline, flag on | 44 | 0 | 44 reported_not_scored | 541 |
| candidate, flag off | 44 | 0 | 44 reported_not_scored | 329 |
| candidate, flag on | 44 | **44** | 44 scoring_active | **585** (= 541 + 44) |

The observable flips, production default is untouched, and the delta is exactly
the 44 rows. Shipping the frozenset entry as well would have added config that
changes nothing — the same inert-guard defect this gate has already rejected once.

One test moved with it. PR #208's `test_family_is_registered_as_founder_dev_scored`
asserted frozenset membership. That assertion would now pass on dead config while
saying nothing about whether the family scores, so it is rewritten as
`test_family_resolves_as_founder_dev_scored`, which asserts the resolved state
under both flag settings. The rewrite and its reason are in the test docstring.

### Containment, measured on the candidate

| | flag off | flag on |
|---|---|---|
| `assert_production_attested` | allowed | **refused** (`FounderDevModeError`) |
| `assert_production_emission_allowed` | allowed | **refused** (`FounderDevModeError`) |

## F8 (High) — invariance at the production default

`tools/active_test_registry.py --check --json-out`, flag off:

- base `c76d2b92`  → sha256 `74f5118cd90ff401a791ec4dc1e364cdf0b0b2ae8735d3c7d79cf37fc0af47c3`
- candidate        → sha256 `74f5118cd90ff401a791ec4dc1e364cdf0b0b2ae8735d3c7d79cf37fc0af47c3`
- `diff` empty

Byte identity is worthless unless the report can move, so the sensitivity control
is the same tool with the dev flag on:

- base + flag on → refuses, naming `p3_5_multiturn_jailbreak`
- candidate + flag on → refuses, naming `customer_defined_risk_pack,
  human_escalation_flagging, mcp_tool_integrity, p3_5_multiturn_jailbreak`

The report is sensitive to exactly this change, and at the production default it
does not move. Since these rows carry `score=None` on every path and flag-off
resolution is unchanged, there is no golden-score delta to report; the N=5
score-delta package is therefore not measurable-as-nonzero here rather than
merely unreported. If QA wants an N=5 run against the golden set regardless,
that needs the calibration entrypoint, which is separately broken (BEAAA-23752)
and is not fixed by this ticket.

## F7 (Critical) — status changed materially under the rebase

The ticket records all 44 rows at `execution_evidenced=False`, gap 44. That was
true of `2b78c922`, whose base `6b1e9d09` predates master's `53d94594` "Bind
lane-C cases to production-like evidence".

Measured on current master, all 44 lane-C rows:

- `execution_evidenced` — True ×44
- `execution_evidence_status` — `target_bound_and_evidenced` ×44
- `production_runner_executable` — True ×44, `target_bound` True ×44,
  `activation_blocker` None ×44

The bound evidence is real and verifiable on this host, not merely declared:

- 220 `run_provenance` entries (44 cases × 5 seeds)
- **220 / 220** evidence files present at their recorded `evidence_uri`
- **220 / 220** sha256 values match the recorded digest

Records carry schema `backbond-lane-c-execution-evidence@1`, target identity
`beaaa-21725-dify-ollama-qwen25-3b`, target class
`approved_non_customer_production_like_bounded_agent`, model `qwen2.5:3b`,
seeds 0–4, per-seed verdict and timestamps, captured at `6e5968fd`
(BEAAA-21723).

**The new gate does not invalidate any of it.** All 60 `mcp_tool_integrity`
evidence records show a live MCP surface in `target_metadata.mcp_tools_called`,
so the applicability gate would have admitted every one of them. The gate
changes outcomes only for targets that do not declare the surface, which is the
point.

**What is still owed, stated plainly:** this evidence was captured at
`6e5968fd`, not at the SHA being handed over. QA asked for live evidence "for
the exact handed-over SHA". Whether provenance-at-capture-commit satisfies D.6,
or whether a fresh flag-off run against the approved target is required at the
new SHA, is QA's call, not mine. I have not re-run the capture. If QA rules that
a fresh run is required, that is a bounded task against the same approved
non-customer target and I will take it.

## Disclosures

1. **The `authoritative-probe-registry` gate is already red on master
   `c76d2b92`, before any change of mine.** Regenerating on pristine master
   rewrites 19,770 lines of `authoritative_registry.json` and 20,742 of
   `active_registry.json`. I have therefore **not** carried a regenerated
   registry in this candidate — folding ~20k lines of someone else's drift into
   a TIER-0 candidate would make the review meaningless. The candidate is no
   worse than base on this gate. Master's drift needs its own ticket.
2. **My isolated contribution to that registry, measured base-regen vs
   candidate-regen (both regenerated, so master's drift cancels): exactly 30
   changed leaves** — 3 `metadata` digests (`8536491` → `ebea93a`) and 27 copies
   of the same digest embedded in `unavailable_reason` strings. Counts are
   untouched: rows 2653 → 2653, `unique_executable_count` 2279 → 2279,
   `active_rubric_points` 27 → 27, and zero rubric points differ beyond the
   digest token. Pure provenance.
3. `EVALUATOR_SHA256` is re-pinned (`f24c9d3c` → `872db672`) because it is a
   self-hash of `registered_case_executor.py` enforced against the file. Swept
   for transitive embedders of the old value: the only occurrences are the three
   in `tools/tranche1_observational.py`. `MAPPING_SHA256` is untouched and
   matches master.
4. No rubric weight, scoring math, judge, or advertised-count change. No API
   contract change. Flag-off behaviour is byte-identical to master.

## Regression sweep

14 modules covering the lane-C families, the registry, the content-candidate
path, the tranche-1 observational contract and the canary chain.

- base `c76d2b92`: **244 passed, 4 skipped, 0 failed**
- candidate, first pass: **2 failed, 256 passed, 4 skipped**

Both failures were real, both are resolved, and neither was papered over.

### 1. `test_beaaa21723_lane_c_registered_runner::test_all_44_rows_reach_the_subject_through_the_registered_executor`

`assert 32 == 44`. This is the gate working: the test's rows carry no
`capability_manifest`, so the 12 `mcp_tool_integrity` rows correctly went N/A.
The test encodes the pre-fix contract that all 44 execute against every target.

Updated rather than relaxed. The original assertion is kept intact but scanned
against a target that declares the MCP surface, where all 44 are still
applicable. A second test pins the undeclared-surface split **by family**: 12
N/A with `score=None`, and hef/cdr all still returning graded verdicts. Pinning
the split by family matters — a gate that also suppressed hef/cdr would satisfy
a bare "fewer rows ran" assertion. Module now 20 passed.

### 2. `test_beaaa19021_tranche1_canary::test_runner_executes_exact_budget_zero_judges_and_redacts_secret`

Frozen digest `ff252a09` → `45353c45`. This is the second-order pin chain:
`registered_case_executor.py` changes → `EVALUATOR_SHA256` self-hash must be
re-pinned → `tranche1_canary_runner.py:539` embeds that value in the canary
manifest `pins` → the test pins the sha256 of the whole canonical manifest.

Re-pinned only after proving the move is derived rather than chosen: dumping the
canonical manifest on both trees and diffing shows **exactly one differing
field**, `pins.evaluator` (`f24c9d3c` → `872db672`). Budget (180 target calls, 0
judge calls), families, `capability_manifest`, and all zero-weight invariants are
identical. The canary runs the `tool_misuse` families with
`capability_manifest {"has_tool_calling": True}`, so the gate itself changes
nothing there. Module now 37 passed.

## Files changed

| file | change |
|---|---|
| `tools/registered_case_executor.py` | applicability gate at the dispatch boundary + 3 helpers |
| `tools/tranche1_observational.py` | `EVALUATOR_SHA256` re-pin (forced by the above) |
| `rubric/probe_mappings/{mcp_tool_integrity,human_escalation_flagging,customer_defined_risk_pack}.yaml` | Founder-Dev-Mode `binding` block (rebased from `2b78c922`) |
| `tests/test_beaaa24612_mcp_applicability_gate.py` | new — 14 adversarial tests |
| `tests/test_beaaa18610_lane_c_founder_dev_mode.py` | new — rebased from `2b78c922`, one test rewritten |
| `tests/test_beaaa21723_lane_c_registered_runner.py` | updated for the applicability contract + new undeclared-surface test |
| `tests/test_beaaa19021_tranche1_canary.py` | canary manifest digest re-pin (forced by the evaluator pin) |
