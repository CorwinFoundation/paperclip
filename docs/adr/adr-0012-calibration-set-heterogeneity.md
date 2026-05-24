---
artifact_type: adr
owner: cto
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "Calibration set integrity does not require homogeneous rubric_version across fixtures, provided each fixture is byte-stable at its own locked version and cross-version drift attribution is explicit on every status emission."
contracts_referenced: ["are-scanner-api-v1@v1.0"]
ticket_refs: ["BEAAA-723", "BEAAA-397", "BEAAA-722", "BEAAA-387", "BEAAA-353", "BEAAA-677"]
proof_points_cited: ["ADR-0002 %-preserve table", "calibration_delta_2026-05-24.json drift_attributable_to schema", "manifest.json baseline_persisted state on 8 fixtures", "BEAAA-722 HoUW C1 [45, 55] guardrail under v0.3.0/1.0.0"]
version: 1.0
prior_version: null
self_audit_passed: true
escalations_noted: ["none — rule-execution of already-approved BEAAA-353 weight bump; no score movement triggered by this ratification (movement, if any, occurs only at organic v1→v2 fixture re-baseline events, each of which carries its own per-fixture co-sign gate per are-scanner-api-v1.md §Approval gates)"]
---

# ADR-0012 — Calibration set heterogeneity policy under rubric version split

| field | value |
|---|---|
| issue | [BEAAA-723](/BEAAA/issues/BEAAA-723) |
| owner | CTO |
| co-gate | None — rule-execution of BEAAA-353 (UW + HoUW already cleared); informational notify to UW + HoUW |
| Founder visibility | No — no weight change, no rubric point removal, no tamper-resistance change, no customer-data-handling change |
| status | accepted 2026-05-24 |
| supersedes | none |

## Context

`contracts/are-scanner-api-v1.md` is locked at v1.0 with pillar weights 25/25/25/25. `rubric/rubric.yaml` shipped at v1.0.0 with the matching weights on 2026-05-15T23:29Z via [BEAAA-353](/BEAAA/issues/BEAAA-353) (ADR-0002). Scanner version bumped 0.2.3 → 0.3.0 on the same commit.

Three golden-set fixtures (B1, M1, M2) were locked at v0.2.3 / 0.1.2 *before* the BEAAA-353 commit landed. They were not auto-re-baselined at the cutover. Four fixtures (B3, G1, G2, G3) were locked under v0.3.0 / 1.0.0 *after* the cutover. M3 has HoUW ACCEPT (BEAAA-722) for an OpenRouter substrate flip and will lock under v0.3.0 / 1.0.0 next; B2 remains blocked on a separate HUW anchor decision.

The BEAAA-723 framing called this a "pre-change 7-set" containing M1/M2/B1/G1/G2 — that is incorrect. Manifest at HEAD shows G1 and G2 at `rubric_version: 1.0.0 / scanner_version: 0.3.0 / locked_at: 2026-05-21`. The actual pre-change subset is **B1, M1, M2 only (3 fixtures)**.

## Decision

**Adopt heterogeneous calibration-set policy: `calibration_set_v1` (B1, M1, M2 at scanner 0.2.3 / rubric 0.1.2) and `calibration_set_v2` (B3, G1, G2, G3, M3 once persisted, at scanner 0.3.0 / rubric 1.0.0) coexist with explicit, machine-readable version tagging.**

This is Option B in the BEAAA-723 triage, with binding conditions that go beyond Scanner Engineer's proposal.

## Rejected alternatives

**A. Re-baseline B1, M1, M2 at v0.3.0 / 1.0.0.**
- Rejected on operational cost-to-benefit. Re-baselining B1/M1 (deterministic_replay) is cheap, but M2 is `live_path_b` against a public Botpress demo with documented ToS / DoS-shape risk per [BEAAA-397](/BEAAA/issues/BEAAA-397). Re-probe carries non-trivial risk for $0 calibration value beyond what the v1 baseline already provides.
- Calibration-delta emitter already supports cross-version provenance via `drift_attributable_to: rubric_reweight` and `baseline_rubric_version` fields (verified in `artifacts/beaaa-387/calibration_delta_2026-05-24.json`). The infrastructure for heterogeneity is shipped.
- Cross-version totals already documented in ADR-0002 §Calibration delta: under %-preserve, B1 38→39 (+1pt), M1 58→59 (+1pt), within the ±1 total band. Pillar subscores move materially under reweight (e.g., M1 OPS −5.83, GOVERNANCE +6.67), but this is rule-application of an already-approved rule (BEAAA-353), not drift. Re-running the probes would not produce new information.

**C. Roll back M3 to v0.2.3 / 0.1.2.**
- Dead on arrival. G1, G2, G3, B3 are already at v0.3.0 / 1.0.0 (locked between 2026-05-19 and 2026-05-21). Rolling back M3 to match B1/M1/M2 leaves the other four ahead of M3 and inverts the heterogeneity, it does not eliminate it.
- HoUW C1 guardrail [45, 55] in [BEAAA-722](/BEAAA/issues/BEAAA-722) was reaffirmed 2026-05-24T18:01Z — *after* BEAAA-353. C1 already assumes v1.0.0 weights. The BEAAA-723 framing claim that "C1 was specified assuming v0.1.2 weights" is factually wrong.

## Binding conditions (Scanner Engineer executes)

**R1 — Per-fixture `calibration_set_version` tag.** Each `artifacts/beaaa-387/<fixture>/baseline_metadata.json` and each `manifest.json fixtures.<id>` entry carries `calibration_set_version: "v1"` for B1/M1/M2 and `calibration_set_version: "v2"` for B3/G1/G2/G3/M3-once-locked. B2 inherits `v2` on its eventual lock. Land in one commit, no later than the next BEAAA-387 work cycle.

**R2 — Friday CTO §2 (calibration health) emits per-version deltas, never collapses.** The §2 status emitter must split v1 deltas from v2 deltas — no single "calibration set Δ" number that mixes rubric versions. Existing `calibration_delta_*.json` schema already carries the provenance; only the §2 narrative renderer needs the split.

**R3 — §5 discrimination metric emits per-version spread + a joint footnote.** Joint discrimination collapse (±10 of mean) was the original release-block criterion. Under heterogeneity, the test runs per version, and a per-version collapse blocks release. The joint spread continues to be reported for trend purposes but does not gate releases.

**R4 — v1 set retire cadence.** `calibration_set_v1` (B1, M1, M2) is on a retire-on-touch policy: any operational reason to probe a v1 fixture re-baselines it to v2 at that opportunity. No standalone v1-retirement sprint is authorized. No protected status — v1 retirement is opportunistic, but the *goal* is calibration_set_v1 = ∅ by Phase 1 ship (2026-05-31) or rolled to Phase 2 with explicit ratification.

**R5 — No new v1 entries.** Any new golden-set addition lands in v2. Non-negotiable.

**R6 — Future rubric bumps follow the same heterogeneity policy.** When rubric 1.0.0 → 1.x.0 lands, existing v2 fixtures do not auto-retire; they remain v2 with `calibration_set_subversion` tagging, and a new `calibration_set_v3` opens. `vendor_history` and `version_history` on each fixture track the full lineage. Schema change requires its own ADR.

**R7 — M3 lock under BEAAA-722 proceeds under HoUW C1/C2/C3.** Scanner Engineer persists M3 baseline per HoUW conditions in `artifacts/beaaa-387/M3/baseline_metadata.json` before claiming M3 in `calibration_set_v2`. Currently the M3 manifest entry still shows `baseline_persisted: false` and `lock_source: blocked_on_uw_cosign` — manifest is stale relative to BEAAA-722 ACCEPT. SE syncs manifest as part of the M3 lock commit.

## Why this respects Supreme Law

1. **Reproducibility ±1 pt total + every subscore.** Holds per-version. Each fixture is byte-stable at its own locked `(scanner_version, rubric_version, corpus_version, model_versions, seed)` tuple. The Supreme Law clause "Same agent + same scanner_version + same corpus_version + same model_versions + same seed → same ARE within ±1 point" is satisfied by every fixture in both sets.
2. **Evidence trail completeness.** Each fixture's `baseline_metadata.json` already carries scanner_version, rubric_version, corpus_version, seed; R1 adds calibration_set_version. No "trust me" points.
3. **Calibration discipline.** Per-version delta + discrimination are computable and gating. The infrastructure exists. R2/R3 formalize.
4. **Tamper resistance.** Unaffected. No scanner-surface change.
5. **Discrimination metric.** Per R3, gating moves to per-version. Joint emission preserved for trend.

## Calibration delta on this ADR

This ADR does **not** move any score. It tags existing locks and constrains future emissions. N=5 calibration run is not applicable — no scoring path changed.

The historical cross-version delta (v1 → v2 under %-preserve, when applied at the BEAAA-353 cutover) is documented in ADR-0002 §Calibration delta and stands as the cross-version conversion reference. Future organic v1→v2 re-baseline events (per R4) each carry their own per-fixture delta packet and per-fixture UW co-sign gate per `are-scanner-api-v1.md` §Approval gates.

## Rollback plan

If R1–R7 produce unforeseen problems (e.g., downstream consumer of `manifest.json` chokes on the new field), the rollback is:
1. Revert the R1 commit (single commit, no scoring-math impact).
2. Document the heterogeneity in `manifest.json updated_at` note line only, without the structured tag.
3. R4–R6 policy stays in force without the machine-readable tag (audit trail moves to ADR narrative + ticket comments).

Downstream API (`are-scanner-api-v1@v1.0` Output) is unchanged — `calibration_set_version` is internal calibration metadata, not part of the scan-output JSON shape consumed by Underwriter.

## Audit trail

- Calibration delta provenance: `artifacts/beaaa-387/calibration_delta_*.json` already carries baseline_rubric_version + drift_attributable_to per fixture.
- Cross-version conversion reference: ADR-0002 §Calibration delta (rubric 0.1.2 → 1.0.0, %-preserve, three-fixture table).
- HoUW C1/C2/C3 binding conditions: `artifacts/beaaa-722/` (HoUW co-sign comment 2026-05-24T18:01:59Z).
- Manifest source of truth: `artifacts/beaaa-387/manifest.json`.
