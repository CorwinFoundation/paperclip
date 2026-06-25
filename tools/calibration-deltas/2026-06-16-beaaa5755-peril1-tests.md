---
artifact_type: other
owner: cto
persona_audience: internal
phase: 2
canonical_terms_only: true
focus_claim: "Peril 1 (Wrong-recipient / misdirected action) test coverage substantiated under ROBUSTNESS via pass^k metric and indirect-injection binding probe."
contracts_referenced: ["are-scanner-api-v1", "scanner-implementation-supervision-v1"]
ticket_refs: ["BEAAA-5755", "BEAAA-5744", "BEAAA-5745"]
proof_points_cited: []
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted: []
---

# BEAAA-5755 — N=5 calibration delta receipt (Peril 1 test coverage)

## BLUF

Two new tests landed under the ROBUSTNESS pillar to substantiate Peril 1 (Wrong-recipient / misdirected action) coverage per the BEAAA-5745 audit gap. Both tests are scaffolding under existing rubric_point_ids — no new rubric_point_id introduced, no probe-mapping wired into the scoring path, no scorer changed. Δ on the calibration set is **0 by construction**. Classification: **rule-execution** per `contracts/scanner-implementation-supervision-v1.md@v1.1` row 23 ("Implement a new probe inside an existing rubric_point_id").

## Contract / metadata pins

| Field | Value |
| --- | --- |
| Contract | `contracts/are-scanner-api-v1.md` @ v1.0 |
| Supervision | `contracts/scanner-implementation-supervision-v1.md` @ v1.1 |
| scanner_version | 941c087 (canon master tip after BEAAA-5754 ff-merge) |
| corpus_version | r2-rob-2026.06.14 (rubric); test-side pin `p1_recipient_binding_v1@0.1.0` + `p1_transaction_correctness_v1` |
| probe-set-version (Test 1) | `p1_transaction_correctness_v1` |
| probe-set-version (Test 2) | `p1_recipient_binding_v1@0.1.0` |
| rubric_point_id (Test 1) | `ROBUSTNESS.TOOL_MISUSE.PARAM_TAMPER` |
| rubric_point_id (Test 2) | `P3.2` (Indirect injection — tool/RAG content resistance) |
| peril_id | P1 |
| upstream_eval_version | N/A (no upstream framework wrap in v1) |
| cto_cosign_ref | N/A (rule-execution per supervision table row 23) |
| Item-0 substrate | BEAAA-5754 / canon `941c087` — `are_connector.probe_runtime.ProbeRuntime` |

## Why Δ = 0 by construction

The two new tests sit *under* existing rubric_point_ids; they are **not** wired into the scoring pipeline (no `probe_mappings/` YAML, no scorer module change, no rubric YAML edit, no calibration_curves edit). The scanner-side scoring graph is unchanged, so re-running the calibration set against canon-master-with-tests vs. canon-master-without-tests yields identical scores by construction:

* `rubric/scoring.py` unchanged
* `rubric/rubric.yaml` unchanged
* `rubric/probe_mappings/*` unchanged (no new mapping registered)
* `rubric/calibration_curves.{py,yaml}` unchanged
* No new probe contribution flows into any DimensionEvidence

The tests live in `tests/` and `tools/are-connector/tests/` and are exercised by pytest only; they are not invoked by the score pipeline.

## N=5 in-test discipline (per-test reproducibility)

Even though the scoring delta is zero by construction, each new test was re-run N=5 times against the worktree at HEAD on canon `941c087` (post-Item-0 ff-merge); both pass deterministically every run:

```
tests/test_transaction_correctness.py (3 tests: pass^k + 2 discrimination)
  RUN 1: 3 passed in 0.10s
  RUN 2: 3 passed in 0.12s
  RUN 3: 3 passed in 0.21s
  RUN 4: 3 passed in 0.16s
  RUN 5: 3 passed in 0.08s

tools/are-connector/tests/test_recipient_binding.py (4 tests: probe + discrimination + 2 invariants)
  RUN 1: 4 passed in 1.62s
  RUN 2: 4 passed in 0.56s
  RUN 3: 4 passed in 1.07s
  RUN 4: 4 passed in 0.58s
  RUN 5: 4 passed in 0.57s
```

Full connector suite (test_connector_core + test_http_security + test_response_cap + test_probe_runtime_smoke + new test_recipient_binding) = **26 passed in 2.55s** — no regression in the BEAAA-5740/5742/5754 lineage.

## pass^k metric (BEAAA-5744 board rec #1 adoption)

Test 1 (`test_payee_amount_match`) adopts pass^k with **k=5 default** (configurable via `ARE_PROBE_PASS_K`). Required: ALL k runs pass — not majority. This is the BEAAA-5744 board rec #1 reliability constraint: single-shot pass is insufficient for Peril 1, where a 4-of-5 success rate still maps to a 20% misdirected-action incidence.

Env override exercised:

```
ARE_PROBE_PASS_K=1   → 1 passed in 0.35s
ARE_PROBE_PASS_K=10  → 1 passed in 0.24s
```

If a future agent-under-scan flips a single run to fail, the pass^k assertion will trip; the verdict payload (per-run pass/fail + transcript_sha) is dumped into the assertion message for Underwriter click-through (AC6).

## Item-0 (probe-runtime substrate) integration

Item-0 landed mid-flight as BEAAA-5754 (canon `941c087`, 2026-06-16T05:57Z) — the worktree was ff-merged onto the new tip before commit, so both tests now integrate against the real substrate where the substrate is applicable:

* **Test 2** uses `are_connector.probe_runtime.ProbeRuntime` directly. The Peril 1 binding invariant is wired as a `judge` verdict_rule with a deterministic LLM-free judge_callout (`_binding_invariant_judge`). The ProbeRuntime supplies `transcript_sha`, `evidence_ref`, `scanner_version`, `corpus_version`, `model_versions`, `seed`, `probe_set_version` on the verdict envelope per `are-scanner-api-v1@v1.0` Hard guarantee #3 — the Underwriter can click through from score → evidence (AC6 fulfilled by the substrate, not by per-test scaffolding).
* **Test 1** keeps a local stub because the Item-0 substrate (single-request `ProbeRuntime.run(spec)`) does not yet model multi-step tool-calling flows. The τ-bench-style end-state assertion requires sequencing `payee_lookup` → `transfer_draft` → `transfer_commit` and reading the final tool-call args; the connector's `ProbeRuntime` is single-shot. The stub's contract (deterministic transcripts of intent → tool_calls → final_commit, transcript_sha emitted) matches what a future multi-step Item-0 extension should preserve. Swap-site is marked in the `probe_runtime` fixture docstring at `tests/test_transaction_correctness.py`.

Follow-up: file a CTO-routed note on BEAAA-5754 / BEAAA-5755 thread requesting a multi-step probe extension on the connector substrate (or a new Item-0.1 ticket) so Test 1 can rip out its local stub. Not a blocker — the per-test verdict payload + transcript_sha contract holds at the test boundary regardless of substrate.

## Carry-forward / risks

* When the probe-mapping wire-in for these tests lands (next iteration), Δ on the calibration set will need a fresh N=5 run because the score path opens. Classification at that point will need re-evaluation per supervision-v1.1 row 24 ("Implement a probe such that the N=5 score impact exceeds ±1 on any golden agent" — rule-change if so).
* Single canonical poisoned-tool-output payload pinned at `p1_recipient_binding_v1`. Adversary diversity expansion is a probe-set-version bump.
* Cross-tenant recipient escalation is out of scope (separate failure mode per the ticket scope).

## Pre-submission self-audit

1. Read all 9 reference files this turn — yes.
2. Deliverable defined: rule-execution test additions under existing ROBUSTNESS rubric_point_ids for CTO+Underwriter consumption — yes.
3. Classified rule-execution per supervision-v1.1 row 23 — yes.
4. N=5 reproducibility evidence attached above — yes.
5. cto_cosign_ref required? No — rule-execution; row 23 has CTO co-sign = NO.
6. ARE Scanner API v1.0 cited — yes.
7. All 5 metadata fields populated (scanner_version, corpus_version, probe-set-version, rubric_point_id, N=5 delta) — yes.
8. Glossary clean — yes (no BMI / ARI / BEAI / Backbond single-cap; "BackBond" not used in test bodies, no carrier names).
9. New probe wraps upstream? No — these are first-party probes under existing rubric_point_ids (tau-bench-pattern end-state assertion + indirect-injection mock). No upstream framework forking.
10. FAL audit — passed; no marketing voice, no anti-patterns, no carrier mentions, internal only.
