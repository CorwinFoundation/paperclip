---
adr_id: ADR-0032
title: Peril-5 prompt-injection harness — refusal-classifier judge pin
ticket: BEAAA-5757
parent: BEAAA-5747 (Tier-1 test coverage)
classification: Rule execution per contracts/scanner-implementation-supervision-v1.md@v1.1 row "Wrap an upstream eval framework"
co_sign_required: NO (rule-execution; record N=5 delta + upstream version pins)
author: Scanner Engineer (d8bbc3b5)
status: ACCEPTED
date: 2026-06-16
contract_version: contracts/are-scanner-api-v1.md@v1.0
supervision_version: contracts/scanner-implementation-supervision-v1.md@v1.1
---

# ADR-0032 — BEAAA-5757 R-5 prompt-injection harness: refusal-classifier judge-pin decision

## BLUF

The three Peril-5 test families (direct AdvBench/JailbreakBench, indirect
AgentDojo, multi-turn Crescendo) use a **non-LLM deterministic regex
refusal classifier** as the judge. The classifier is literally identical
to the R1 grader in
`engineering/probe-sdk/src/are_probe/probes/refusal_robustness.py`, so the
two suites stay in lock-step. No LLM is in the loop; no model needs to be
pinned for the judge function. Per
`contracts/scanner-implementation-supervision-v1.md@v1.1` row "Add or
change a judge model" this avoids the rule-change classification — the
ticket stays rule-execution by-construction.

P3.5 (Multi-turn jailbreak persistence) remains DEFERRED in
`rubric/rubric.yaml` `deferred_to_v02`. Multi-turn evidence is tagged
against P3.1 today; formal P3.5 activation is a follow-up rule-change
ticket requiring CTO + UW co-sign.

## Context

`contracts/scanner-implementation-supervision-v1.md@v1.1` rows that
applied to this decision:

| Row | Action | Classification | Co-sign |
| --- | --- | --- | --- |
| 9 | Wrap an upstream eval framework as an ARE probe | Rule execution | NO |
| 11 | Add or change a judge model | Rule change | YES |
| 3 | Add a new rubric_point_id to the canonical scoring sheet | Rule change | YES + ADR |

The AdvBench / JailbreakBench / AgentDojo / Crescendo families are wraps
(row 9). The natural scoring surface for "did the agent refuse?" is a
refusal classifier; if that classifier is an LLM, row 11 fires (rule
change). We therefore pinned a non-LLM classifier so the entire ticket
remains a row-9 wrap.

## Decision

1. **Judge class: regex deterministic.** `RefusalClassifier` in
   `tests/_p5_helpers.py` evaluates a fixed pattern list. No
   temperature, no seed, no model.

2. **Pattern source.** The pattern list is literally copied from
   `engineering/probe-sdk/src/are_probe/probes/refusal_robustness.py`
   `_REFUSAL_PATTERNS`. Any future edit to the pattern list must touch
   BOTH files in the same commit, with N=5 delta evaluated for the R1
   refusal-robustness golden set as a guard.

3. **Why not an LLM judge?**
   - Row 11 makes LLM-judge changes rule-changes requiring CTO co-sign.
   - The AdvBench/JailbreakBench upstream scoring uses regex/string-match
     refusal grading historically (Zou et al. 2023, Chao et al. 2024) —
     the regex classifier is faithful to the upstream measurement, not
     a divergence.
   - The R1 probe-sdk grader has been the canonical refusal grader for
     ROBUSTNESS.REFUSAL.DONOTANSWER_SMOKE since BEAAA-152 WS-B; reusing
     it preserves N=5 reproducibility across the R1 ↔ R-5 suites.
   - LLM judges are non-deterministic without seed-pinning, and seed
     pinning shifts the classification at every model bump — a moving
     target for the ±1 band invariant in
     `contracts/are-scanner-api-v1.md@v1.0` Supreme Law.

4. **No LLM judge needed for indirect / multi-turn either.** AgentDojo's
   indirect subset asserts agent refusal on tool-content payloads; the
   refusal phrasing channel is the same as direct. Crescendo's
   multi-turn semantic is "does the agent yield on any turn?" — again
   the per-turn output is graded with the same refusal regex.

5. **Multi-turn rubric tagging — P3.1 today, P3.5 deferred.** AC7 says
   tag `rubric_point_id` for the three families. P3.1 (direct), P3.2
   (indirect/RAG) are active. P3.5 (multi-turn jailbreak persistence) is
   in `rubric/rubric.yaml` `deferred_to_v02`. Multi-turn tests on this
   ticket tag P3.1 as the active surface and carry a
   `_DEFERRED_RUBRIC_POINT_ID = "P3.5"` module constant so a future
   rule-change ticket can swap the tag in one place. The multi-turn
   test file enforces a CI guard
   (`test_deferred_p3_5_carry_forward_recorded`) that flags any silent
   un-deferring.

## Consequences

**Positive**

- Ticket remains pure rule-execution; no co-sign queue dependency.
- N=5 calibration delta is 0 by-construction (no scoring path touched).
- Cross-suite consistency with R1 refusal-robustness grader.
- CI fast (no LLM call); mock-only path is deterministic across re-runs.

**Negative / accepted trade-offs**

- The regex classifier is precision-tuned, recall-loose. A model that
  refuses in unusual phrasing ("That request crosses a line I won't
  cross.") may be misclassified as "answer" — false-negative on the
  refusal label. Mitigation: any new refusal phrasing observed in the
  field is added to the R1 + R-5 pattern list in a single commit, with
  N=5 delta required on the R1 golden set as a guard.
- The classifier has zero coverage of "answer-but-evasive" responses
  (hedged answers that don't refuse but don't comply). Crescendo's
  USENIX '25 paper notes this surface; a follow-up rule-change ticket
  may add a `hedge` classification (rule change — judge-model change).

**Deferred**

- LLM-judge swap (rule change, CTO co-sign): out of scope here. The
  Anthropic refusal-classifier model in the R2 calibration roadmap (per
  parent BEAAA-5747 description, board doc Item 0 fan-out) is the
  natural carry-forward.
- P3.5 (multi-turn jailbreak persistence) formal rubric activation
  (rule change, CTO + UW co-sign): out of scope here.

## Verification

- `tests/test_prompt_injection_direct.py::*::test_well_aligned_agent_meets_T` — refusal-default mock passes T against the regex classifier on AdvBench + JailbreakBench subsets.
- `tests/test_prompt_injection_indirect.py::AgentDojoIndirectInjectionTests::test_well_aligned_agent_meets_T` — same for AgentDojo tool-content subset.
- `tests/test_prompt_injection_multiturn.py::CrescendoMultiTurnInjectionTests::test_well_aligned_agent_holds_across_turns` — same for Crescendo multi-turn chains.
- Discrimination: `test_leaky_agent_is_caught` / `test_progressively_yielding_agent_is_caught` — a fully-compliant mock fails at least one probe, so the classifier discriminates.

## Cross-references

- Supervision contract: `contracts/scanner-implementation-supervision-v1.md@v1.1`
- Scanner API contract: `contracts/are-scanner-api-v1.md@v1.0`
- Pattern source: `engineering/probe-sdk/src/are_probe/probes/refusal_robustness.py`
- Upstream pins: `tests/upstream_pins.json`
- Parent ticket: BEAAA-5747 (Tier-1 test coverage)
- Sibling tickets: BEAAA-5754 (Item-0), BEAAA-5755 (R-1), BEAAA-5756 (R-2), BEAAA-5758 (R-7)
- Discrepancy carry-forward: BEAAA-5745 §3 coverage matrix — "P5 best-covered-by-probe-inventory but uncovered-by-test_*.py"; this ticket closes the wiring gap.
