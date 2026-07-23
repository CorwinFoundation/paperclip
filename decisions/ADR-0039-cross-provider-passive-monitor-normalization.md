---
artifact_type: adr
owner: scanner-engineer
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "This ADR adds a provenance-preserving cross-provider normalization + continuity-decision layer to the existing Passive Monitor / Determination Engine so evidence from two independent provider stacks resolves into ONE BackBond continuity band and ONE continuity decision — corroborate, diverge, or degrade — with deterministic BackBond rules deciding and provider-native verdicts as inputs only, without moving any BackBond Score or changing are-scanner-api-v1@v1.0 output semantics."
contracts_referenced: ["are-scanner-api-v1@v1.0", "are-scanner-api-v1.md@v1.3.0", "scanner-implementation-supervision-v1@v1.1", "evidence-profile-v1"]
ticket_refs: ["BEAAA-16490", "BEAAA-16489", "BEAAA-16258", "BEAAA-11372", "BEAAA-11371"]
proof_points_cited: ["20/20 tests/test_cross_provider_continuity_beaaa16490.py pass", "55 passed / 1 skipped determination-engine + evidence-store regression unchanged", "production-path proof scripts/beaaa16490_cross_provider_proof.py resolves all 6 scenarios (3 deterministic cases + 3 negative) and writes a hash-bound, re-derivable evidence bundle", "isolation guard test_no_scoring_surface_imported: new modules import no rubric.scoring / rubric.bands / rubric", "git status: six additions are all new isolated files; no rubric/corpus/scoring/contract file modified"]
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted: ["No rubric weight, scoring math, judge model, corpus, band-cut, output-shape, or tamper-resistance change -> N=5 golden delta 0 by construction. CODE (input class, normalizer, independence check, continuity resolver) is rule-execution plumbing under scanner-implementation-supervision-v1. The THRESHOLDS + corroboration/divergence semantics in determination/rules/continuity_policy_v1.yaml are rule-content and require CTO + Underwriter co-sign BEFORE this policy governs any real (non-fixture) continuity or coverage decision. This candidate is fixture-only, behind BACKBOND_DETERMINATION_EMIT, and NOT promoted; QA gate + Founder sign-off required for promotion per BEAAA-15400/BEAAA-15373."]
scanner_version: v0.5.0
corpus_version: "0.1.0"
probe_set_version: unchanged
rubric_point_id: "N/A (runtime evidence-plane; no rubric point or scoring math touched)"
n5_calibration_delta: "0 by construction (no scoring-path file touched; isolation guard test_no_scoring_surface_imported + git-status proof)"
cto_cosign_ref: "PENDING — continuity-policy@1.0.0 thresholds require CTO+Underwriter co-sign before non-fixture use; code plumbing shippable as rule-execution"
---

# ADR-0039: cross-provider Passive Monitor normalization and continuity decision

## Status

**Proposed** (Scanner Engineer, BEAAA-16490). Candidate handed to QA; not
promoted. The continuity-policy *content* awaits CTO + Underwriter co-sign
(see Classification). Parent: BEAAA-16489 (Track E, cross-provider weakest-link
proof); grandparent BEAAA-16258.

## BLUF

Provider-native runtime telemetry is now an **explicit, provenance-preserving
input class** to the existing Determination Engine. Signed/authenticated
evidence from two **independent** provider stacks for one deployed agent
normalizes — without a parallel monitor and never inline in a vendor request
path — into exactly **ONE BackBond continuity band** and **ONE continuity
decision**. Deterministic BackBond rules decide; the provider's own
determination/attestation is carried as provenance and **never read by the
deciding path**. Conflicting authenticated sources emit a **divergence action**,
not a silent average. No BackBond Score moves and no `are-scanner-api-v1@v1.0`
output semantics change.

## Context

BEAAA-16489 (Track E) requires proving in production that cross-provider
evidence resolves to one BackBond continuity outcome without any single provider
determining it — the "weakest-link" claim for the AI-insurance-stack thesis
(BEAAA-16258). The Passive Monitor IS the existing runtime evidence pipeline
(BEAAA-11371 / Determination Engine v1, BEAAA-11372): immutable Evidence Profile
v1 packets, deterministic rules, no LLM in the deciding path. What was missing
is a first-class notion of *which provider produced the evidence*, an
*independence* check between sources, and a deterministic way to fuse two
providers' determinations into one continuity outcome.

## Decision

Add three isolated, additive pieces — no existing file is modified:

1. **`determination/provider_normalization.py`** — a `ProviderEvidenceEnvelope`
   input class and `normalize_provider_evidence()` that maps a provider envelope
   into the existing immutable `EvidencePacket`/`StructuredEvent` model while
   preserving provenance: `source_provider_id`, `provider_stack`,
   `attestation_type`, `attestation_key_id`, `authentication_method`,
   `integrity_status`, `received_timestamp`, the immutable `source_event_ids`,
   event timestamps, and the operator-declared `independence_group`. The
   provider-native determination is stored under
   `provenance.provider_native_determination` and is **not** copied into the
   event attributes the rules read. PII is masked and disallowed keys (customer
   weights, proprietary/system prompts, raw transcripts, credentials) are
   dropped before retention (fail-closed). `check_independence()` returns
   not-independent when sources share a provider id, stack, signing key, or
   declared group.

2. **`determination/rules/continuity_policy_v1.yaml`** + **`determination/continuity.py`**
   — a versioned deterministic policy (`continuity-policy@1.0.0`) and a
   `ContinuityDecisionEngine` that runs the unchanged `DeterminationEngine` per
   provider packet, collapses each to a BackBond signal
   (`covered_peril_candidate` > `insufficient_evidence` > `no_finding`), and
   resolves ONE of four terminal bands:
   - **CONTINUE** — ≥2 independent, authenticated, fresh sources corroborate no
     finding → `continue_monitoring`.
   - **ELEVATED** — ≥2 corroborate a covered-peril candidate →
     `review_covered_peril_candidate` (human lane, not auto-adjudicated).
   - **DIVERGENCE** — authenticated sources conflict (candidate vs no-finding) →
     `divergence_hold`; never averaged, neither provider breaks the tie.
   - **DEGRADED** — a source is missing / stale / unauthenticated / not
     independent → `degraded_insufficient_independent_corroboration`.

3. The emitted **evidence bundle** is machine-verifiable: `bundle_sha256` binds
   the outcome to every source's preserved provenance + determination ids;
   `verify_bundle()` / `recompute_decision()` re-derive the band and decision
   from the bundle alone, proving (a) reachability and (b) that the provider
   verdict — present in provenance — played no part in the decision.

The band emitted here is a coarse Passive-Monitor **continuity posture**, not the
pre-bind numeric BackBond Score or the frozen A/B/C/D underwriting band
(`rubric/bands.py`); those are untouched.

## Classification (scanner-implementation-supervision-v1)

- **Rule-execution (shippable now):** the input class, normalizer, PII
  redaction, independence check, continuity resolver, fixtures, tests, and proof
  script. This is evidence-plane plumbing that implements within the existing
  runtime-evidence lane, analogous to the Determination Engine adapters.
- **Rule-content (CTO + Underwriter co-sign required before non-fixture use):**
  the thresholds and corroboration/divergence semantics in
  `continuity_policy_v1.yaml` (freshness window, min corroborating sources, the
  four band→decision mappings). This candidate is fixture-only, behind
  `BACKBOND_DETERMINATION_EMIT`, and **not promoted**, so shipping the candidate
  for QA review does not put the policy into force.

Uncertain classification was resolved by escalating the *policy content* to
co-sign rather than defaulting to rule-execution, per the Supreme Law.

## Verification

- `python3 -m pytest tests/test_cross_provider_continuity_beaaa16490.py -q` →
  **20 passed**.
- `python3 -m pytest tests/test_determination_engine_beaaa11372.py tests/test_evidence_store_read_adapter_beaaa11378.py -q`
  → **55 passed, 1 skipped** (unchanged; no determination-engine regression).
- `python3 scripts/beaaa16490_cross_provider_proof.py` → all 6 scenarios OK;
  writes `artifacts/beaaa-16490/cross_provider_continuity_evidence.json` (the
  reachable, hash-bound evidence bundle).
- **N=5 golden delta = 0 by construction.** No scoring-path file is touched;
  `test_no_scoring_surface_imported` asserts the new modules import no
  `rubric.scoring` / `rubric.bands` / `rubric`, and `git status` shows the six
  additions are all new isolated files. QA may run the full live
  `scripts/release_gate_n5.py` on the candidate SHA for independent confirmation.

## Rollback

Delete/`git revert` the six isolated additions
(`determination/provider_normalization.py`, `determination/continuity.py`,
`determination/rules/continuity_policy_v1.yaml`,
`determination/fixtures/cross_provider/`,
`tests/test_cross_provider_continuity_beaaa16490.py`,
`scripts/beaaa16490_cross_provider_proof.py`). Nothing else imports them; no
schema, adapter, or scoring surface changes, so removal returns the runtime
evidence plane to its prior state.

## Consequences

- Track E gains a machine-verifiable, provenance-preserving cross-provider
  proof with an adversarial (provider-verdict-cannot-override) negative test.
- Promotion into any real continuity/coverage decision requires the policy-
  content co-sign above, plus QA `APPROVED` on the exact SHA and Founder
  sign-off (BEAAA-15400 / BEAAA-15373).
