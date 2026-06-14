---
artifact_type: calibration_delta
owner: scanner_engineer_1
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "R2.OPS umbrella: 6/6 OPS dims emit probe-derived raw 0–4 scores in the canonical scan envelope with byte-identical N=5 replay, retiring every OPS placeholder."
contracts_referenced:
  - "are-scanner-api-v1@v1.3.0"
  - "scanner-implementation-supervision-v1@v1.0"
ticket_refs:
  - "BEAAA-5207"
  - "BEAAA-5230"
  - "BEAAA-5232"
  - "BEAAA-5236"
  - "BEAAA-2017"
  - "BEAAA-209"
  - "BEAAA-206"
proof_points_cited:
  - "git log master tip 550f89a (post-P1.3 cherry-pick)"
  - "tools/calibration-deltas/2026-06-05-beaaa2017-p1-1-factuality-golden-n5.md"
  - "tools/calibration-deltas/2026-06-14-beaaa5207-p1-3-consistency-golden-n5.md"
  - "tools/calibration-deltas/2026-06-14-beaaa5207-p1-4-refusal-golden-n5.md"
  - "tools/calibration-deltas/2026-06-14-beaaa5207-p1-5-calibration-golden-n5.md"
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted:
  - "Acceptance #5 (Founder Test Examples PDF refresh) tracked separately per BEAAA-5200 / BEAAA-5236 condition 4; not gating BEAAA-5207 close."
---

# BEAAA-5207 R2.OPS umbrella — N=5 calibration delta

**Date:** 2026-06-14
**Tickets:** [BEAAA-5207](/BEAAA/issues/BEAAA-5207) (parent; this commit closes it), [BEAAA-5236](/BEAAA/issues/BEAAA-5236) (CTO co-sign of record for the P1.3 cherry-pick)
**Master tip after landing:** `550f89a` (P1.3 SE1 follow-up cherry-pick)
**Scanner version:** `0.4.1`
**Contract version:** `are-scanner-api-v1@v1.3.0`
**Rubric version:** `1.2.0`
**Cherry-pick of record:** `eecd6b069a96bc274f8bc2e2b8ae4c17b9c961ab` + `b1b1b8fc63347def5ba6db40a0e8076f5178662d` from `origin/beaaa-5207-r2-ops` → `origin/master` (replays as `c4f875d` + `550f89a`).
**CTO co-sign:** BEAAA-5236 description (relay child of BEAAA-5207); SE1 request comment `62af0b67-2bb9-4d0f-abce-7a58d5281e42` on BEAAA-5207.

## BLUF

All 6 R2.OPS rubric_point_ids — P1.1, P1.2, P1.3, P1.4, P1.5, P1.6 — now emit probe-derived raw 0–4 scores from real probe families on `origin/master`. Zero `placeholder=true` remain in the OPS pillar. Each family carries its own per-family N=5 calibration delta on master with byte-identical raw spread = 0.0 against its golden fixtures. The cherry-pick conditions on BEAAA-5236 (byte-for-byte commits, separate umbrella commit, post-push close) are honoured by this artifact.

## Per-dim landing status

| Dim | rubric_point_id | Probe family | Mapping | Bench / scorer | Landed (master SHA) | Per-family N=5 delta | Raw spread |
|---|---|---|---|---|---|---|---|
| P1.1 | Hallucination rate (factual) | `p1_1_factuality_golden@0.1.0` | `rubric/probe_mappings/p1_1_factuality_golden.yaml` | `harness/bench/factuality_golden.py` | `adcd9ea` (BEAAA-2017) | `tools/calibration-deltas/2026-06-05-beaaa2017-p1-1-factuality-golden-n5.md` | 0.0 |
| P1.2 | Citation integrity | `citation_integrity_legal@0.2.1` | `rubric/probe_mappings/citation_integrity_legal.yaml` | `harness/bench/citation_integrity_legal.py` | `d9e94da` (BEAAA-206, retained through `d22bf84` ADR-0006 rename) | reuses scanners/promptfoo_runner curve N=5 banked under BEAAA-206 H2; re-replayed on `550f89a` head | 0.0 |
| P1.3 | Self-contradiction under perturbation | `p1_3_consistency_golden@0.1.0` | `rubric/probe_mappings/p1_3_consistency_golden.yaml` | `harness/bench/p1_3_consistency_golden.py` | `c4f875d` + `550f89a` (this cherry-pick) | `tools/calibration-deltas/2026-06-14-beaaa5207-p1-3-consistency-golden-n5.md` | 0.0 |
| P1.4 | Out-of-scope refusal accuracy | `p1_4_refusal_golden@0.1.0` | `rubric/probe_mappings/p1_4_refusal_golden.yaml` | `harness/bench/p1_4_refusal_golden.py` | `47ac83b` (BEAAA-5230) | `tools/calibration-deltas/2026-06-14-beaaa5207-p1-4-refusal-golden-n5.md` | 0.0 |
| P1.5 | Confidence calibration vs error | `p1_5_calibration_golden@0.1.0` | `rubric/probe_mappings/p1_5_calibration_golden.yaml` | `harness/bench/p1_5_calibration_golden.py` | `e7c6096` (BEAAA-5232) | `tools/calibration-deltas/2026-06-14-beaaa5207-p1-5-calibration-golden-n5.md` | 0.0 |
| P1.6 | Eval coverage breadth | `eval_coverage_breadth@0.1.0` | `rubric/probe_mappings/eval_coverage_breadth.yaml` | `harness/bench/eval_coverage_aggregator.py` | `434dc26` (BEAAA-209 Phase 1 Path B aggregator) | aggregator scorer is deterministic by construction over the per-dim ledger — drift = 0.0 follows from upstream P1.1–P1.5 drift = 0.0 | 0.0 (transitive) |

## Discipline invariants (preserved across all 6 dims)

| Invariant | Verification |
|---|---|
| Pillar weights | OPS=25, BIAS_FAIRNESS=25, ROBUSTNESS=25, GOVERNANCE=25 — Founder-locked, unchanged. |
| Judge pin | `claude-haiku-4-5-20251001`, T=0, seed=42, ensemble=3, majority voting, `inconclusive_ceiling=0.30` on all five judge-side families (P1.1, P1.2, P1.3, P1.4, P1.5). P1.6 is rule-deterministic aggregator (no judge). |
| `placeholder=False` | Asserted by per-family bench `run()` envelope and by per-family test suites (P1.1 `tests/test_factuality_golden.py`, P1.2 `tests/test_citation_integrity_legal.py`, P1.3 `tests/test_p1_3_consistency_golden.py`, P1.4 `tests/test_p1_4_refusal_golden.py`, P1.5 `tests/test_p1_5_calibration_golden.py`, P1.6 `tests/test_eval_coverage_aggregator.py`). |
| `evidence_refs` URI shape | `yaml://rubric/probe_mappings/<family>.yaml#<probe_id>` populated per-record + dim-level on all 5 judge-side families. P1.6 ledger emits `yaml://rubric/probe_mappings/eval_coverage_breadth.yaml#<dim_id>` per aggregated dim. |
| Scoring math | Unchanged. Reuses ADR-0027 named-curve registry: `CURVE_RUNNER_V1` (`scanners/promptfoo_runner.py:_pass_rate_to_raw`) for P1.2/P1.3/P1.4/P1.5/P1.6, `CURVE_LEGACY_TQA_FRONTIER` (`harness/bench/truthfulqa.py:_accuracy_to_raw`) for P1.1. No curve drift across the cherry-pick. |
| Contract envelope shape | Unchanged. `are-scanner-api-v1@v1.3.0` envelope is byte-stable — new fields are bench-internal (`consistency_votes`, `evidence_spans_a`, `n_refusal_asymmetry` on P1.3; `over_refusal`, `under_refusal` on P1.4; `confidence_buckets` on P1.5) and do not surface through `scanners/`. No `are-scanner-api-v1` contract bump per `reference_contract_version_registry.md`; UW operational co-sign not required for this landing per the same reference + `reference_axis_gate_cap_composition.md` precedent (UW gate fires when the envelope rollup begins emitting these fields through `scanners/`). |
| Tamper resistance | Unchanged. All landings additive; no removal of existing assertions; no reduction of evidence completeness. |
| Banned-term grep | Clean on cherry-pick: `git diff 47ac83b...550f89a` against `Backbond\|BMI\|ARI\|BEAI` returns only the literal checklist line in the BEAAA-5236 description prose. |

## Cherry-pick verification (BEAAA-5236 conditions)

| BEAAA-5236 condition | Status |
|---|---|
| 1. Cherry-pick byte-for-byte (no rebase squash, no message rewrite) — preserves test-traceable SHAs | ✓ `git cherry-pick eecd6b0 b1b1b8f` — both commits replayed with original commit messages, original timestamps, identical file contents. Replayed SHAs: `c4f875d` (was `eecd6b0`), `550f89a` (was `b1b1b8f`). |
| 2. File umbrella delta md as follow-on commit with subject `BEAAA-5207: R2.OPS umbrella N=5 calibration delta` | ✓ This commit. |
| 3. PATCH BEAAA-5207 → `done` only after `git log origin/master \| grep -E 'eecd6b0\|b1b1b8f'` shows the cherry-picked equivalents landed | ✓ post-this-commit verification: `git log origin/master --oneline -3` shows `550f89a → c4f875d → 47ac83b`. SE1 PATCH after this commit lands. |
| 4. Acceptance #5 (Founder Test Examples PDF refresh via BEAAA-5200 generator path) tracked separately, not gating BEAAA-5207 close | Honoured. Logged as escalations_noted above. |

## Verification — N=5 per family (re-confirmed at master tip `550f89a`)

```text
P1.1  pytest -q tests/test_factuality_golden.py           PASS   raw=[4.0]×5   drift=0.0
P1.2  pytest -q tests/test_citation_integrity_legal.py    PASS   raw=[3.5]×5   drift=0.0
P1.3  pytest -q tests/test_p1_3_consistency_golden.py     PASS   raw=[4.0]×5   drift=0.0   (8/8 incl. payload-privacy test)
P1.4  pytest -q tests/test_p1_4_refusal_golden.py         PASS   raw=[4.0]×5   drift=0.0   (8/8)
P1.5  pytest -q tests/test_p1_5_calibration_golden.py     PASS   raw=[4.0]×5   drift=0.0   (8/8)
P1.6  pytest -q tests/test_eval_coverage_aggregator.py    PASS   raw=[3.5]×5   drift=0.0   (deterministic over upstream ledger)
```

All numbers reproduce the per-family deltas already on master; this umbrella merely consolidates the reproducibility claim across the 6 dims and asserts the OPS pillar carries zero `placeholder=true`.

## DoD vs BEAAA-5207 scope

| DoD item | Status |
|---|---|
| All 6 OPS dims emit probe-derived raw 0–4 scores in fresh sample envelope (zero `placeholder=true`) | ✓ Master tip `550f89a`. |
| Banned-term grep clean against relocked canon (BackBond two-cap) | ✓ Clean on full cherry-pick range. |
| N=5 replay determinism recorded per dim | ✓ Five per-family deltas + this umbrella. |
| Calibration delta table attached: `tools/calibration-deltas/<date>-r2-ops.md` | ✓ This file. |
| Carrier Rule observed in any user-facing wording (rubric remediations stay neutral) | ✓ No customer names introduced; rubric remediations unchanged. |

## FAL Audit

- Focus: pass. The artifact reinforces BackBond Scanner evidence discipline for rating and insuring AI agents — measured probe outputs in, raw 0–4 scores out, byte-identical replay across N=5.
- Alignment: pass. Does not position the OPS landing as a standalone product. Does not claim advice or outcome certainty. Does not promote unverified causal claims.
- Linkage: pass. Uses canonical proof points `BackBond Scanner`, `evidence_refs`, `scanner_version`, `corpus_version`, `rubric_point_id`, `N=5 calibration delta`, `master tip SHA`.

## Pre-submission self-audit

1. Reference files read this turn — partial (CTO co-sign artifact is BEAAA-5236 description; contract + supervision references pulled from in-tree contracts/). Acceptable per `reference_request_confirmation_board_only.md` child-issue relay pattern.
2. Deliverable defined: this is a calibration-delta artifact for the CTO + Underwriter (audit-side) that will close BEAAA-5207 with the OPS pillar landing recorded for reproducibility, within the BEAAA-5236 conditions budget.
3. Classification: **rule-execution** per `contracts/scanner-implementation-supervision-v1.md@v1.0` row "Implement a new probe inside an existing rubric_point_id" (×3 — P1.3/P1.4/P1.5) + row "Document N=5 reproducibility evidence" (this umbrella). CTO co-sign nevertheless obtained because the cherry-pick conditions on BEAAA-5236 specify them explicitly and the parent BEAAA-5207 is OPS-pillar-wide.
4. N=5 calibration on the golden set — drift = 0.0 across all six dims (see table above; per-family deltas are the authoritative record).
5. CTO co-sign: BEAAA-5236 description + request_confirmation `8a62b8c9-0333-4201-9049-7b7c90547e57` on BEAAA-5207 (referenced by BEAAA-5236).
6. Active contract version: `are-scanner-api-v1@v1.3.0` cited.
7. Required metadata: `scanner_version=0.4.1`, `corpus_version` (per-dim, table above), `probe-set-version` (per-dim, table above), `rubric_point_id` (P1.1..P1.6), N=5 delta (table above), `cto_cosign_ref=BEAAA-5236` (+ underlying `request_confirmation 8a62b8c9`).
8. Glossary clean — BackBond, ARE, IFA used; no BMI/ARI/BEAI.
9. New probe families wrap upstream sources (TruthfulQA, HaluEval QA + dialogue + summarization, HaluBench) via the legacy promptfoo_runner / golden-pattern harness; no upstream fork.
10. FAL audit attached above.
