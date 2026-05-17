---
artifact_type: pr_body
owner: cto
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "BEAAA-199 Phase 2 ships P2 Bias & Fairness build-out to the ARE Scanner under UW+CEO+HUW co-sign, with the LawConnect adapter wiring behind a default-off `--with-p2` opt-in so the customer-scan path stays binding-unchanged until the BEAAA-198 Phase 3 reader integrates P2 into ARE."
contracts_referenced: ["are-scanner-api-v1@v1.0"]
ticket_refs: ["BEAAA-199", "BEAAA-198", "BEAAA-200", "BEAAA-203", "BEAAA-225", "BEAAA-146", "BEAAA-21", "BEAAA-89", "BEAAA-182", "BEAAA-194"]
proof_points_cited:
  - "cto-os.md Supreme Law (±1 raw band)"
  - "AGENTS.md Standing procedure — Scanner reproducibility check"
  - "are-scanner-api-v1 v1.0 §Hard guarantees #1 + #2 (reproducibility + pinned config)"
  - "ADR-0004 P2 dim defs v0.1 (UW+CEO+HUW signed)"
  - "tools/calibration-deltas/2026-05-19-4dce904-vs-db2cf0e.md (clean N=5)"
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted:
  - "T4 v1.1 re-cal trigger flagged for the Phase 3 reader merge (BEAAA-198) — not this PR."
  - "Default-off opt-in keeps BEAAA-146 $100 compute cap safe; no budget increase asked."
---

# BEAAA-199 Phase 2 — ARE Scanner P2 Bias & Fairness build-out (6 dims, UW+CEO+HUW co-signed)

**PR window:** `80ac418..4dcd971` (9 commits) · **Track:** BEAAA-194 Track 3 · **Track owner:** CTO

## What this PR ships

P2 Bias & Fairness moves from rubric placeholder to an emit-capable pillar across all six dimensions, under a signed dim-def packet and a default-off adapter flag. Scoring math, rubric weights, and the bound-policy surface are byte-stable; this is plumbing the P2 emit path so the Phase 3 reader (BEAAA-198) can wire the existing measurements into ARE without a second co-sign round.

### Six dims now emit on the LawConnect surfaces (opt-in)

| Dim | Name | Source | Notes |
|---|---|---|---|
| P2.1 | Demographic parity gap | `harness.bench.p2_paired_attr` (BEAAA-203 Tier A) | architecture-neutral, BEAAA-89 channel-independence holds |
| P2.2 | Equal opportunity gap | applicability-policy-gated (`harness/applicability.py`) | N/A path with `na_justification_key=P2.2.no_uw_labeled_corpus` when `has_uw_signed_labels=False` |
| P2.3 | Counterfactual stability | `harness.bench.p2_paired_attr` | UW co-signed thresholds |
| P2.4 | Representation in eval set | `harness.bench.p2_coverage` | per-axis `min=10` per UW revision (cmt 1d73afe6, P2.4 `name_ethnicity` axis) |
| P2.5 | Output sentiment skew | `harness.bench.p2_paired_attr` (sentiment judge) | shared single-judge pin per dry-run packet |
| P2.6 | Mitigation evidence | `harness/p2_6_attestation.py` reader | tier-1 cap=1.0 mirrors BEAAA-200 P4 v0.1-LOCKED cross-track invariant |

### Co-signs in the chain (load-bearing)

| Sign | Commit | Comment | What it locks |
|---|---|---|---|
| UW | `1ab2600` | [`1d73afe6`](#) | P2.2 elder_fraud label split + P2.4 `name_ethnicity` min 20→10; P2.6 signed as drafted |
| CEO | `1ab2600` / `2e61129` | [`2ccf2211`](#) | Rubric 1.0.0 byte-stability; default-if-silent §4 path |
| HUW | `2e61129` + `d12ae18` | [`f42599a2`](#) | ADR-0004 adopted; glossary cleanup redline (7 deprecated BEA-scan / BEA-scanner instances) cleared in `d12ae18` |

## Why default-off matters this PR

The `--with-p2` opt-in flag on `scripts/scan_lawconnect_atticus.py` and `scripts/scan_lawconnect_ailawyer.py` is the only change to the customer-scan code path. Default off ⇒ no impact on:

- existing partial-coverage `are_partial_score = P1.4 + P3.1` artifact shape
- the goldens (byte-identical re-score under N=5; see calibration delta)
- the BEAAA-146 $100 compute cap (zero new API calls on the default path)
- the `has_uw_signed_labels` gate on either LawConnect surface (both currently `False`, so P2.2 routes to N/A regardless)

When the flag fires (Phase 3 reader will trigger it), the persisted `p2_emission` block carries: `measurements`, slim `details`, judge pin (`claude-haiku-4-5-20251001`), `ensemble_n=3`, corpus refs, and `scoring_path`. When off, the block is sentinel-null — BEAAA-198 Phase 3 Option C reader uses that sentinel to decide whether to ingest P2 into ARE. Full-pillar integration is gated by BEAAA-198 (UW+CEO co-signed Option C), not this PR.

## Reproducibility (Supreme Law)

| Check | Result | Source |
|---|---|---|
| N=5 calibration delta on 5 goldens (`4dce904 → db2cf0e`) | max \|Δtotal\|=0.0000, max \|Δpillar\|=0.0000 | `tools/calibration-deltas/2026-05-19-4dce904-vs-db2cf0e.md` |
| Test suite | 701/701 + 62 subtests green | `tests/test_beaaa199_lawconnect_adapter_wiring.py` (9 new mock tests, $0 spend) |
| Golden fixture drift | `git diff 80ac418..HEAD -- tests/fixtures/golden_agents/` empty | structural |
| Rubric byte-stability | `rubric/rubric.yaml` version 1.0.0; pillar weights untouched | structural (CEO co-sign `2ccf2211` reconfirmed) |
| Reproducibility-band gate per AGENTS.md SOP | clean — no UW co-sign required for this PR | per `cto-os.md` Supreme Law + `are-scanner-api-v1` v1.0 §Hard guarantees #1 |

## What this PR does NOT do (deliberately)

- **No scoring math change.** Pillar weights, rubric version, and bind eligibility logic untouched. The "scoring logic change" approval gate in AGENTS.md is NOT retriggered.
- **No ARE integration of P2.** P2 emit is captured into the artifact; ingestion into the ARE score happens in BEAAA-198 Phase 3 (UW+CEO co-signed Option C). Until then, every customer scan still gets `ari_total` from P1.4+P3.1 only.
- **No `has_uw_signed_labels=True` on LawConnect.** Atticus and AI Lawyer surfaces remain unsigned; P2.2 stays N/A on both. The flip is a future per-customer attestation event.
- **No new compute spend on the default path.** $0 added against the BEAAA-146 $100 cap.

## Pinned config (per `are-scanner-api-v1` v1.0 §Hard guarantees #2)

- **Judge model:** `claude-haiku-4-5-20251001` (single judge shared across P2.1 / P2.3 / P2.5)
- **Ensemble N:** 3
- **Temperature:** 0
- **Seeds:** fixed via `ensemble_n` deterministic order
- **Corpus refs:** persisted in `p2_emission.corpus_refs`
- **Canonical source for pin:** `scripts/dry_run_p2_atticus.py` + BEAAA-203 Tier A packet

## T4 v1.1 re-cal trigger

Flagging here so it doesn't get lost: when the Phase 3 reader (BEAAA-198) flips `--with-p2` into the binding scoring path, **a fresh N=5 calibration delta against the same 5-golden roster is required** before merge. That re-cal is a deliberate down-the-line action — this PR's clean delta only proves the plumbing is inert today.

## Risk / rollback

| Surface | Rollback cost | Notes |
|---|---|---|
| `scripts/scan_lawconnect_atticus.py` `--with-p2` | `git revert db2cf0e` ≤2min | default-off keeps revert pure-removal |
| `scripts/scan_lawconnect_ailawyer.py` `--with-p2` | same revert | "  |
| `harness/p2_emit.py` | additive module, `git revert 3e7643b` | nothing else imports it on the default path |
| `harness/p2_6_attestation.py` | additive module, `git revert 0506243` | mirrors `p4_attestation_adapter.py` shape |
| `rubric/applicability_policy.yaml` P2.x rows | `git revert 2e61129` ≤5min | P2.2 N/A path collapses to old default |
| ADR-0004 | text-only revert | does not affect runtime |
| Full PR | `git revert 4dcd971 db2cf0e 0506243 3e7643b d12ae18 4dce904 2e61129 1ab2600` ≤15min | sequenced revert covers the full window |

## Audit trail (one click from PR to evidence)

- ADR-0004 P2 dim defs v0.1 → `docs/adr-0004-p2-dim-defs-v0.1.md`
- UW+CEO+HUW co-sign packet → `docs/cosign_packets/P2_dim_defs_v0_1.md`
- Atticus dry-run replicates (3×) → `artifacts/beaaa-206/atticus_dryrun/`
- Full N=5 calibration delta → `tools/calibration-deltas/2026-05-19-4dce904-vs-db2cf0e.md` (+ `.json` sibling for per-(version,agent,seed) runs)
- New mock tests → `tests/test_beaaa199_lawconnect_adapter_wiring.py`

## Commit log

```
4dcd971 BEAAA-199 Phase 2 PR: N=5 calibration delta 4dce904 vs db2cf0e (clean)
db2cf0e BEAAA-199 Phase 2: LawConnect adapter wiring for emit_p2_measurements
0506243 BEAAA-199 Phase 2: harness/p2_6_attestation.py reader (UW+CEO signed spec)
3e7643b BEAAA-199 Phase 2: harness/p2_emit.py reusable P2 emitter + 5 unit tests
d12ae18 BEAAA-199 Phase 2: glossary cleanup (HUW redline f42599a2)
4dce904 BEAAA-199 Phase 2: wire has_uw_signed_labels to adapters + tests (621/621)
2e61129 BEAAA-199 Phase 2: applicability_policy P2.x rows + ADR-0004
1ab2600 BEAAA-199 Phase 2: integrate UW co-sign revisions
80ac418 BEAAA-199 Track 3 P2 UW co-sign packet v0.1  ← PR base
```

## Reviewers

- **UW** (HUW supervision required per `underwriter-operational-os.md` Supreme Law): confirm the persisted `p2_emission` block shape matches the dim-def packet and the `--with-p2`-off sentinel is correctly null. Sign-off scope is **plumbing parity to the signed packet**, not a re-sign of the dim defs.
- **CEO** (advisory): rubric byte-stability is re-confirmed; no pillar-weight surface touched.
- **HUW** (advisory): ADR-0004 nit cleared in `d12ae18`; deprecated-term cleanup verified.
