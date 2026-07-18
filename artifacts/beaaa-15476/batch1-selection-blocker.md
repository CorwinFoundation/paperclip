---
artifact_type: calibration_delta
owner: scanner_engineer_2
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "The approved Batch 1 selection cannot yield 100 unique normalized prompt/oracle pairs from the pinned runner commit."
contracts_referenced: ["are-scanner-api-v1@v1.0"]
ticket_refs: ["BEAAA-15476", "BEAAA-15471", "BEAAA-15435"]
scanner_version: "0.5.0"
corpus_version: "0.1.1"
probe-set-version: "runner@784778473d3e"
rubric_point_id: "P1.1-P1.6,P2.5,P3.1-P3.3,P3.5-P3.6"
n5_calibration_delta: "not run: no Scanner-state or score behavior change; candidate manifest fails pre-execution uniqueness gate"
model_versions: []
seed_recipe: "selection audit is deterministic; execution seeds remain 0-4 for the required later N=5 gate"
cto_cosign_ref: null
self_audit_passed: true
---

# BEAAA-15476 Batch 1 selection blocker

## BLUF

Do not freeze or execute the approved Batch 1 selection. At runner commit `784778473d3e`, the 113 non-core target-bound rows materialize to only 90 distinct normalized `(prompts, expected)` identities. The approved first-100 slice contains 100 unique `case_id` values but only 77 distinct normalized identities, so 23 rows would be duplicate evidence and the BEAAA-15476 acceptance gate must fail closed.

## Reproduction

The audit used an isolated `git archive 784778473d3e`, imported `build_registry()` and `materialize_text_case()` from that archive, excluded the four already-live core families (`p1_1_factuality_golden`, `p1_3_consistency_golden`, `p1_4_refusal_golden`, `p1_5_calibration_golden`), and computed each identity with the pinned `text_case_identity()` implementation.

Observed results:

- Eligible non-core target-bound rows: `113`
- Distinct normalized identities across the entire eligible pool: `90`
- Approved first-100 slice unique `case_id` values: `100`
- Approved first-100 slice distinct normalized identities: `77`
- Duplicate rows in the approved first-100 slice: `23`
- Live target calls: `0`
- Judge calls: `0`

Representative collision groups include:

- `misleading.FalseAssertion` / `garak.misleading.FalseAssertion`
- `snowball.GraphConnectivity` / `garak.snowball.GraphConnectivity`
- `dan.Dan_11_0` / `dan.AntiDAN` / `garak.dan.AntiDAN` / `garak.promptinject.HijackHateHumans`
- `garak.exploitation.SQLInjection` / `P3.2-ROB-02` / `P3.2-ROB-10`
- `garak.leakreplay.LiteratureCloze` / `privacybench.indirect_inference.exfil`
- `jbb.privacy.exfil` / `jbb.privacy.injection`

## Required unblock decision

The Founder/CTO owner of approved Plan A revision `cab2837a` must revise the approved input or acceptance criterion. The smallest evidence-preserving options are either (a) reduce Batch 1 to at most 90 distinct cases from this pinned pool, or (b) approve additional materializable source rows beyond runner commit `784778473d3e` so the batch can reach 100 distinct normalized identities. Scanner Engineer #2 must not silently substitute rows because that would depart from the approved canonical-order selection.

## Classification and invariants

This audit is rule execution under the supervision-contract row for baseline fixture/manifest work: it changes no rubric, judge, scoring math, output shape, runtime registry, or tamper-resistance posture. No candidate manifest was created, no Scanner state changed, and therefore no N=5 score run is meaningful yet. Once a revised selection is approved and frozen, N=5 seeds 0-4 remains mandatory before merge under `are-scanner-api-v1@v1.0`.

## Rollback

Remove this diagnostic artifact only. Runtime registry and score behavior are byte-identical because no manifest, configuration, or code was changed.
