---
artifact_type: other
owner: cto
persona_audience: internal
phase: 1
canonical_terms_only: true
brand_backbone_applicable: false   # internal contract patch, not an external-bound asset
focus_claim: "v1.8.0 contract patch (are-scanner-api-v1 v1.7.0 -> v1.8.0) surfacing the new BEHAVIORAL-INTEGRITY business-logic-correctness evidence from the tau-bench (BEAAA-11808) and behavioral-STOP (BEAAA-11809) live probes. THREE ADDITIVE changes: (1) three new evidence_refs classes -- transaction_correctness, stop_compliance, tool_grounded_factuality -- each carrying the already-emitted transcript / tool_call_log / ledger_end_state / post_stop_tool_calls rows; (2) an OPTIONAL, REPORTED-ONLY behavioral_integrity output block that names the new dimension family and its three dimensions but is NOT part of are_total and NOT wired into any pillar weight; (3) reproducibility fields (scanner_version, corpus_version, model_versions, seed) preserved unchanged. Classification: additive-evidence + reported-not-scored subscore surface. NO rubric wiring, NO pillar re-weight, NO change to how are_total is computed -> golden N=5 delta = 0 by construction. Per CEO ruling BEAAA-11807 this is the additive scan-contract half; the scoring/band wiring (STOP knockout, evidence-grade gating, weights) is a SEPARATE CEO+UW ADR gate and is explicitly OUT OF SCOPE here."
contracts_referenced: ["are-scanner-api-v1@v1.7.0 (shipped) -> v1.8.0 (proposed additive minor)", "scanner-implementation-supervision-v1@v1.0", "uw-rubric-supervision-v1@v1.0"]
ticket_refs: ["BEAAA-11810", "BEAAA-11799", "BEAAA-11807", "BEAAA-11808", "BEAAA-11809", "BEAAA-11800", "BEAAA-11803", "BEAAA-11798"]
proof_points_cited:
  - "CEO placement ruling BEAAA-11807 (artifact BEAAA-11807-ruling-v1): placement = NEW BEHAVIORAL-INTEGRITY dimension family (public ARE-standard MINOR bump, additive); the probes 'ship now as additive evidence emitters -- no rubric wiring, golden delta 0'; rubric-wiring opens SEPARATELY under the CEO+UW gate."
  - "BEAAA-11808 tau-bench live wrap MERGED origin/master @7464b0b -- evals/perils/money_movement/tau_bench_live.py emits TauBenchLiveResult.evidence_refs rows {kind: transcript|tool_call_log|ledger_end_state, trial_index, artifact_uri, sha256}; module docstring line 27: 'evidence but is NOT wired into rubric weights / scoring.'"
  - "BEAAA-11809 behavioral STOP live driver MERGED origin/master @a0adcb7 -- probes/stop_instruction/v1/live_driver.py emits StopInstructionResult.evidence_refs rows {kind, trial_index, artifact_uri, sha256} for transcript + post_stop_tool_calls; docstring: 'This is evidence-only ... does not wire the result into scoring.'"
  - "UW concurrence on the behavioral family + STOP band-mapping + Gap-3 evidence bar already on record: BEAAA-11800 (Head of Underwriting, done) ratified in combined CEO ruling BEAAA-11803 (done). No new UW decision outstanding; only the ADR co-sign at the FUTURE scoring/band wiring step."
version: "v1.8.0"
prior_version: "are-scanner-api-v1@v1.7.0"
self_audit_passed: true
self_audit_note: "Pre-submission self-audit (cto-os Supreme Law). (1) Focus claim cites the CEO placement ruling (BEAAA-11807), both merged emitter commits (7464b0b, a0adcb7) with their evidence-row shapes, and the standing UW concurrence (BEAAA-11800/11803). (2) Carrier Rule clean -- no carrier or customer named. (3) Canonical terms only -- BackBond Scanner / BackBond Score; no BMI / ARI / ARE-as-product / BEAI / IFA in body prose. (4) Internal patch; not an external-bound asset -> Brand Backbone Step-0 gate N/A (persona_audience: internal). (5) Supreme Law / reproducibility: the four reproducibility fields (scanner_version, corpus_version, model_versions, seed) are UNCHANGED and remain required; every new evidence row carries a sha256 fingerprint + reachable artifact_uri (clickable, tamper-evident); NO scoring-path code touched by this patch (contract + openapi + changelog only) -> golden N=5 delta = 0 by construction, and the emitters it documents are themselves evidence-only (delta 0 already verified at BEAAA-11808/11809). (6) Tamper resistance: neutral-to-strengthened -- new evidence rows are append-only, sha-fingerprinted, and the behavioral_integrity block is reported-not-scored so it cannot be gamed into are_total. (7) NO pillar-weight / rubric-weight / scoring-math / judge change -> NO Founder gate; pillar weights stay 25/25/25/25 Founder-locked. (8) Co-sign: additive-evidence + reported-not-scored -> per BEAAA-11810 acceptance #4 UW is NOTIFIED, co-sign NOT required for THIS bump; the pricing-relevant band wiring (STOP knockout etc.) is deferred to the SEPARATE CEO+UW ADR gate where UW co-sign IS required (and UW concurrence is already on record via BEAAA-11800/11803). (9) Rollback: additive fields, Tier-2 PR revert; consumers that ignore the new surface are byte-identical to v1.7.0."
escalations_noted:
  - "Version selection -- v1.8.0 (v1.7.0 + 1 minor). Registry check: contracts/ carries patch files through are-scanner-api-v1-v1.7.0-patch.md (ACCEPTED, merged @5d90b5f); this is the next unused minor. Bumped exactly once."
  - "NO Founder gate: pillar weights 25/25/25/25 UNCHANGED (Founder-locked 2026-05-12); are_total computation UNCHANGED; tamper resistance neutral-to-strengthened."
  - "NO UW co-sign gate on THIS bump: additive-evidence + reported-not-scored, golden delta 0. UW is NOTIFIED per acceptance #4. The scoring/band wiring that WOULD require UW co-sign (STOP knockout hard-floor on Preferred-A, weights, evidence-grade gating) is a SEPARATE CEO+UW ADR gate per BEAAA-11807 / BEAAA-11798 v2 and is OUT OF SCOPE here."
---

# Contract patch — `are-scanner-api-v1.md` v1.7.0 -> v1.8.0

**Status:** DRAFT authored + CTO-reviewed (agent `88e68019`) under [BEAAA-11810](/BEAAA/issues/BEAAA-11810). Additive-evidence-only; no scoring change. Lands on canonical master once CI (`golden scanner regression`, `python pytest`) is green.

**Design authority:** CTO (contract design), inside delegated scope. Placement decided by CEO ruling [BEAAA-11807](/BEAAA/issues/BEAAA-11807) (NEW BEHAVIORAL-INTEGRITY family, additive, public ARE-standard MINOR bump). Underwriter concurrence on the behavioral family / STOP band-mapping / Gap-3 evidence bar already on record: [BEAAA-11800](/BEAAA/issues/BEAAA-11800) (Head of Underwriting, done), ratified in combined ruling [BEAAA-11803](/BEAAA/issues/BEAAA-11803).

**Change classification:** additive minor. No existing field renamed or removed. Every extension is opt-in (a new `evidence_refs` `class` value, or a new OPTIONAL top-level reported block). Consumers that ignore the new surface receive a **byte-identical v1.7.0 response**. Tier-2 PR-revert rollback.

**The load-bearing invariant (read first):** this patch is the **additive scan-contract half** of the behavioral-integrity work. It surfaces *evidence* and a *reported, unscored* subscore block. It does **NOT** wire anything into `are_total`, does **NOT** change any pillar weight, and does **NOT** implement the STOP knockout or any band logic. That wiring is a **separate CEO + Underwriter approval gate** (per [BEAAA-11807](/BEAAA/issues/BEAAA-11807) §"Engineering path" and [BEAAA-11798](/BEAAA/issues/BEAAA-11798) directive v2). Nothing here alters how the Underwriter prices today.

---

## §0. Why now — placement is decided, evidence is landed

The τ-bench money-movement probe ([BEAAA-11808](/BEAAA/issues/BEAAA-11808), merged `7464b0b`) and the behavioral-STOP probe ([BEAAA-11809](/BEAAA/issues/BEAAA-11809), merged `a0adcb7`) are on canonical master **as evidence-only emitters** — both modules state in their own docstrings that they are not wired into scoring. They already produce `evidence_refs` rows. What is missing is the **versioned, documented contract surface** that names those rows so the Underwriter and any future auditor can consume them without a silent, undocumented field appearing in the envelope.

CEO ruling [BEAAA-11807](/BEAAA/issues/BEAAA-11807) fixed placement: a **NEW dimension family, working name BEHAVIORAL-INTEGRITY**, housing three dimensions —

- **Transaction-Correctness** (τ-bench / money-movement correctness)
- **Stop-Compliance** (behavioral STOP)
- **Tool-Grounded Factuality** (business-factual hallucination)

— added as an additive public-standard **minor** bump, existing pillar definitions and weights untouched. This patch is that minor bump on the scan contract.

## §1. New `evidence_refs` classes — additive

`evidence_refs[]` gains three new permitted values on the `class` (evidence-class) discriminator. Each is **additive**: v1.7.0 consumers that do not recognise the value ignore the row; no existing class is renamed or removed.

| New evidence class | Emitting probe | Rows (already emitted on master) |
|---|---|---|
| `transaction_correctness` | τ-bench money-movement (`evals/perils/money_movement/tau_bench_live.py`) | `{class: "transaction_correctness", kind: "transcript" \| "tool_call_log" \| "ledger_end_state", trial_index, artifact_uri, sha256}` |
| `stop_compliance` | behavioral STOP (`probes/stop_instruction/v1/live_driver.py`) | `{class: "stop_compliance", kind: "transcript" \| "post_stop_tool_calls", trial_index, artifact_uri, sha256}` |
| `tool_grounded_factuality` | business-factual hallucination (deployment-measured; category-proxy HaluEval is INDICATIVE-only per [BEAAA-11807](/BEAAA/issues/BEAAA-11807) §3) | `{class: "tool_grounded_factuality", kind: "transcript" \| "grounding_check", trial_index, artifact_uri, sha256}` |

**Evidence-row contract (unchanged shape).** Every new-class row is the same tamper-evident shape the shipped scanner already uses: a reachable `artifact_uri` (transcript / tool-call log / ledger end-state / post-STOP tool-call log) plus a `sha256` fingerprint of the exact artifact bytes. This satisfies the Supreme-Law evidence-trail requirement: a downstream reader clicks from the behavioral-integrity signal straight to the reproducible transcript that produced it.

**Evidence-grade tag (Gap-3, additive, non-binding).** A `tool_grounded_factuality` row MAY carry an OPTIONAL `evidence_grade ∈ {indicative, deployment_measured}`. Per [BEAAA-11807](/BEAAA/issues/BEAAA-11807) §3, `indicative` (category-proxy, e.g. HaluEval) is a screening signal and is **not bind-grade on its own**; a full-limit bind on business-factual exposure requires `deployment_measured` evidence or an explicit sub-limit. This tag is metadata on the evidence row only — it does not itself set a score or a band.

## §2. `behavioral_integrity` — OPTIONAL, REPORTED-NOT-SCORED output block

The `ScoreResponse` gains ONE new OPTIONAL top-level object, `behavioral_integrity`. It is **reported, not scored**: it is **not** a member of `pillars`, it does **not** contribute to `are_total` / `bmi`, and it carries **no weight**. Omitting it (or a consumer ignoring it) leaves the v1.7.0 envelope byte-identical.

```jsonc
"behavioral_integrity": {                 // OPTIONAL; additive v1.8.0; reported-not-scored
  "family": "BEHAVIORAL-INTEGRITY",       // working taxonomy string; final canon fixed in the scoring-wiring ADR vs glossary.md
  "scored": false,                        // v1.8.0 INVARIANT: always false until the separate CEO+UW scoring-wiring gate lands
  "dimensions": {
    "transaction_correctness": {
      "reported_raw": 0.0,                 // OPTIONAL 0..4 reported signal (e.g. pass^k -> band); NOT a pillar/are_total input
      "reliability_band": "reliable | mostly_reliable | inconsistent | unreliable",  // from τ-bench pass^k; evidence only
      "evidence_refs": [ /* class: transaction_correctness rows, see §1 */ ]
    },
    "stop_compliance": {
      "reported_raw": 0.0,                 // OPTIONAL reported signal; NOT an are_total input
      "post_stop_action_observed": false,  // true => agent acted after STOP (the fail signal the future knockout will read)
      "evidence_refs": [ /* class: stop_compliance rows, see §1 */ ]
    },
    "tool_grounded_factuality": {
      "reported_raw": 0.0,                 // OPTIONAL reported signal; NOT an are_total input
      "evidence_grade": "indicative | deployment_measured",   // see §1 Gap-3 tag
      "evidence_refs": [ /* class: tool_grounded_factuality rows, see §1 */ ]
    }
  }
}
```

**Why `scored: false` is a versioned invariant.** It is the machine-checkable promise that this bump did not change pricing. The Underwriter reads `behavioral_integrity` as *informational evidence* only. Any future flip of `scored` to `true`, or any wiring of `reported_raw` / `post_stop_action_observed` into `are_total` or into band logic, is a **new contract version under the CEO+UW scoring-wiring gate** — it cannot happen silently under v1.8.0.

## §3. Reproducibility fields — intact

`scanner_version`, `corpus_version`, `model_versions`, and `seed` are **unchanged** and remain REQUIRED on every `ScoreResponse` (base contract §Reproducibility; openapi `ScoreResponse.required`). The τ-bench and STOP evidence rows are produced under the same pinned seed/corpus/model envelope as the rest of the scan and inherit those stamps. No new reproducibility field is introduced and none is relaxed.

## §4. Approval-gate disposition (BEAAA-11810 acceptance §2/§4)

- **This bump = additive-evidence + reported-not-scored.** No scoring change; golden N=5 delta = 0 by construction. Per acceptance #4, the **Underwriter is NOTIFIED, co-sign is NOT required** for this version bump.
- **The pricing-relevant work is fenced off and gated.** The STOP knockout (behavioral STOP FAIL = hard floor on Preferred-A, verify-over-attest vs P4.3), the below-Preferred-A weighted STOP mapping, the τ-bench transaction-correctness weighting, and the Gap-3 bind-grade evidence bar are ALL deferred to the **separate CEO + Underwriter scoring-wiring ADR gate** ([BEAAA-11807](/BEAAA/issues/BEAAA-11807) §"Engineering path"; [BEAAA-11798](/BEAAA/issues/BEAAA-11798) v2). UW concurrence on those decisions is already on record ([BEAAA-11800](/BEAAA/issues/BEAAA-11800) / [BEAAA-11803](/BEAAA/issues/BEAAA-11803)); the remaining UW step there is the implementation ADR co-sign, not a new decision.
- **No Founder gate:** pillar weights unchanged, `are_total` computation unchanged, tamper resistance neutral-to-strengthened.

## §5. `api/openapi.yaml` changes — additive

- `ScoreResponse` gains ONE optional property `behavioral_integrity` (schema `BehavioralIntegrity`, §2 shape). Not added to `ScoreResponse.required`.
- New component schema `BehavioralIntegrity` with `scored` fixed-invariant `false` documented, and the three reported dimensions.
- `EvidenceRef.class` enum (or the evidence-row `class` field description) extended with `transaction_correctness`, `stop_compliance`, `tool_grounded_factuality`; optional `evidence_grade` on `tool_grounded_factuality` rows.
- `info.description` notes the v1.8.0 additive behavioral-integrity surface; `contract_version` example bumped to `are-scanner-api-v1@v1.8.0`. (`info.version` in openapi tracks the API-doc line independently and is left to the openapi maintainer; the authoritative contract version is this patch file.)

## §6. Reproducibility / calibration impact

- This patch touches **contract prose + openapi schema + changelog only** — no scanner scoring code, no rubric, no corpus, no judge. Golden N=5 delta = 0 **by construction**.
- The emitters it documents ([BEAAA-11808](/BEAAA/issues/BEAAA-11808), [BEAAA-11809](/BEAAA/issues/BEAAA-11809)) landed as evidence-only with golden delta 0 already verified at those tickets.
- `behavioral_integrity.scored: false` guarantees the block cannot enter `are_total`, so it cannot move any golden total or subscore.

## §7. Rollback

Additive fields; Tier-2 PR revert. Removing the `behavioral_integrity` block and the three new `evidence_refs` classes reverts the surface to v1.7.0 with no data loss (the underlying probe artifacts remain committed and hash-valid). No feature flag needed — nothing here is on the scoring path.

## §8. Changelog / version-history

Appended to the base contract `§Versioning — history` table:

| Version | Date | Bump class | Authoritative source |
|---|---|---|---|
| **v1.8.0** | **2026-07-05** | **additive minor — BEHAVIORAL-INTEGRITY evidence surface (τ-bench / STOP / tool-grounded factuality); 3 new `evidence_refs` classes + reported-not-scored `behavioral_integrity` block; no scoring change** | **This patch; placement CEO ruling BEAAA-11807; emitters BEAAA-11808 / BEAAA-11809; UW concurrence BEAAA-11800 / BEAAA-11803; owning ticket BEAAA-11810** |

**No silent additions:** every field in this patch is enumerated above with its type, its opt-in status, and its non-scoring invariant. The scoring/band wiring that would change pricing is explicitly excluded and separately gated.
