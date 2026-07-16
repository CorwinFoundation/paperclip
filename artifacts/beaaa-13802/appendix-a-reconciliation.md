---
artifact_type: "other"
owner: "cto"
persona_audience: "internal"
phase: 1
canonical_terms_only: true
focus_claim: "BEAAA-13802 reconciles restored Appendix A family-ledger counts against prior BackBond Scanner fallback and dispatcher row counts."
contracts_referenced: ["are-scanner-api-v1@v1.0", "scanner-implementation-supervision-v1@v1.1"]
ticket_refs: ["BEAAA-13802", "BEAAA-13761", "BEAAA-13762"]
proof_points_cited: ["IFA/scanner-test-registry-baseline-2026-07-13.csv", "tools/canonical_test_registry.py", "artifacts/beaaa-13761/registry/current-canonical-registry.json", "tools/active_test_registry.py"]
version: "0.2"
prior_version: "0.1"
self_audit_passed: true
escalations_noted: []
---

# BEAAA-13802 Appendix A Reconciliation v0.2

BLUF: Appendix A is restored and authoritative for this close gate on `origin/master@10400821d03d718e2a00f6c33eef775d57b2718f`. The regenerated registry uses the Appendix A family ledger: 61 registry family rows, 715 numeric T1 embedded corpus items, and 1 production_active registry row carrying 4 embedded live-walker prompts. The prior 286 production_active count is a non-authoritative probe-registry fallback count in mapping/probe-row units, not an Appendix A embedded-corpus count; current master counts 90 bound active executable rows and does not treat placeholder/unbound worker rows as executed evidence.

## Scope

This is a registry reconciliation artifact for CTO/CEO review. Contract anchor: `are-scanner-api-v1@v1.0`. Classification: rule-execution metadata/count gate; no scoring math, rubric weights, judge model, API output shape, or harness behavior changed.

## Source And Hash

- Board comment: `ee903788-e935-4856-851b-4a417d54837a`
- Attachment: `6ef25626-7604-4090-934c-6f218ad6bbb5`
- CSV path: `IFA/scanner-test-registry-baseline-2026-07-13.csv`
- CSV SHA-256: `2753b1e22bcf29a12f605a01cf08447a9def3da6ac2f2405ebcc87a5a19ef820`
- Registry source mode: `appendix_a_family_ledger`
- Registry SHA-256: `3e84dde4a01390e3f9f17e667f8bdf135dc05671be224211a3b2e9943822cfaf`

## Counting Unit Decision

The canonical registry counts unique mapping/probe rows. Appendix A embedded corpus items are recorded as `embedded_case_count` and reconciled separately; they do not inflate registry-row counts. This is why `confidence_calibration` is 1 Appendix A family row with 100 embedded items, while the prior fallback ledger had 1 production_active mapping row and the dispatcher currently has 20 executable rows under a different category/source split.

Current-master execution evidence is counted through `tools/active_test_registry.py` only when `status == active` and `production_runner_executable == true`. On `origin/master@10400821d03d718e2a00f6c33eef775d57b2718f`, that gate yields 90 active executable rows out of 280 registry rows; the remaining 190 quarantined or unbound rows are registry/discrepancy context only.

## Top-Level Counts

| Count | Value | Unit |
|---|---:|---|
| Appendix A families | 61 | family rows |
| Appendix A T1 embedded cases | 715 | embedded corpus items |
| Appendix A all numeric embedded cases | 743 | embedded corpus items |
| Appendix A production_active rows | 1 | registry family rows |
| Appendix A production_active embedded prompts | 4 | embedded corpus items |
| Prior fallback production_active | 286 | non-authoritative mapping/probe rows |
| Legacy active dispatcher executable | 281 | placeholder-inclusive dispatcher rows |
| Current master registry rows | 280 | active registry rows |
| Current master active executable | 90 | bound execution rows |
| Current master quarantined/unbound | 190 | not executed evidence |

## Family Reconciliation

| Family | Appendix disposition | Tier | Embedded cases | Fallback active rows | Dispatcher rows | Delta explanation |
|---|---|---|---:|---:|---:|---|
| `advbench` | `blocked_dependency` | `T2 framework/external dataset` | 0 | 1 | 1 | Appendix A disposition is blocked_dependency; embedded items do not enter active registry counts. Prior fallback counted 1 repo-proven production_active mapping/probe rows for the same family name. Active dispatcher has 1 executable rows matching this family/source name. |
| `agentdojo` | `blocked_dependency` | `T2 framework/external dataset` | 0 | 4 | 4 | Appendix A disposition is blocked_dependency; embedded items do not enter active registry counts. Prior fallback counted 4 repo-proven production_active mapping/probe rows for the same family name. Active dispatcher has 4 executable rows matching this family/source name. |
| `bbq` | `needs_validation` | `T1 in-repo runner (cases external/derived)` | 0 | 0 | 0 | Appendix A disposition is needs_validation; embedded items do not enter active registry counts. No exact same-name production_active fallback or dispatcher row matched this Appendix A family. |
| `citation_integrity_legal` | `executable_not_connected` | `T1 in-repo runner + embedded cases` | 80 | 4 | 4 | Appendix A embedded corpus items are T1 assets but not production_active because they are executable_not_connected. Prior fallback counted 4 repo-proven production_active mapping/probe rows for the same family name. Active dispatcher has 4 executable rows matching this family/source name. |
| `confidence_calibration` | `executable_not_connected` | `T1 in-repo runner + embedded cases` | 100 | 1 | 1 | Appendix A embedded corpus items are T1 assets but not production_active because they are executable_not_connected. Prior fallback counted 1 repo-proven production_active mapping/probe rows for the same family name. Active dispatcher has 1 executable rows matching this family/source name. |
| `counterfactual_stability` | `executable_not_connected` | `T1 in-repo runner + embedded cases` | 60 | 1 | 1 | Appendix A embedded corpus items are T1 assets but not production_active because they are executable_not_connected. Prior fallback counted 1 repo-proven production_active mapping/probe rows for the same family name. Active dispatcher has 1 executable rows matching this family/source name. |
| `demographic_parity` | `needs_validation` | `T3 cases without runner` | 4 | 2 | 2 | Appendix A disposition is needs_validation; embedded items do not enter active registry counts. Prior fallback counted 2 repo-proven production_active mapping/probe rows for the same family name. Active dispatcher has 2 executable rows matching this family/source name. |
| `donotanswer` | `needs_validation` | `T1 in-repo runner (cases external/derived)` | 0 | 0 | 0 | Appendix A disposition is needs_validation; embedded items do not enter active registry counts. No exact same-name production_active fallback or dispatcher row matched this Appendix A family. |
| `equal_opportunity` | `fixture_only` | `T4 config-only` | 0 | 1 | 1 | Appendix A disposition is fixture_only; embedded items do not enter active registry counts. Prior fallback counted 1 repo-proven production_active mapping/probe rows for the same family name. Active dispatcher has 1 executable rows matching this family/source name. |
| `eval_coverage_breadth` | `fixture_only` | `T4 config-only` | 0 | 0 | 0 | Appendix A disposition is fixture_only; embedded items do not enter active registry counts. No exact same-name production_active fallback or dispatcher row matched this Appendix A family. |
| `eval_representation` | `fixture_only` | `T4 config-only` | 0 | 1 | 1 | Appendix A disposition is fixture_only; embedded items do not enter active registry counts. Prior fallback counted 1 repo-proven production_active mapping/probe rows for the same family name. Active dispatcher has 1 executable rows matching this family/source name. |
| `factscore` | `blocked_dependency` | `T2 framework/external dataset` | 0 | 2 | 2 | Appendix A disposition is blocked_dependency; embedded items do not enter active registry counts. Prior fallback counted 2 repo-proven production_active mapping/probe rows for the same family name. Active dispatcher has 2 executable rows matching this family/source name. |
| `garak` | `blocked_dependency` | `T2 framework/external dataset` | 0 | 9 | 9 | Appendix A disposition is blocked_dependency; embedded items do not enter active registry counts. Prior fallback counted 9 repo-proven production_active mapping/probe rows for the same family name. Active dispatcher has 9 executable rows matching this family/source name. |
| `garak_beaaa152` | `blocked_dependency` | `T2 framework/external dataset` | 0 | 18 | 18 | Appendix A disposition is blocked_dependency; embedded items do not enter active registry counts. Prior fallback counted 18 repo-proven production_active mapping/probe rows for the same family name. Active dispatcher has 18 executable rows matching this family/source name. |
| `hallucination_legal` | `executable_not_connected` | `T1 in-repo runner + embedded cases` | 125 | 4 | 4 | Appendix A embedded corpus items are T1 assets but not production_active because they are executable_not_connected. Prior fallback counted 4 repo-proven production_active mapping/probe rows for the same family name. Active dispatcher has 4 executable rows matching this family/source name. |
| `halueval` | `blocked_dependency` | `T2 framework/external dataset` | 0 | 5 | 5 | Appendix A disposition is blocked_dependency; embedded items do not enter active registry counts. Prior fallback counted 5 repo-proven production_active mapping/probe rows for the same family name. Active dispatcher has 5 executable rows matching this family/source name. |
| `harmbench` | `blocked_dependency` | `T2 framework/external dataset` | 0 | 7 | 7 | Appendix A disposition is blocked_dependency; embedded items do not enter active registry counts. Prior fallback counted 7 repo-proven production_active mapping/probe rows for the same family name. Active dispatcher has 7 executable rows matching this family/source name. |
| `indirect_injection` | `executable_not_connected` | `T1 in-repo runner + embedded cases` | 10 | 0 | 0 | Appendix A embedded corpus items are T1 assets but not production_active because they are executable_not_connected. No exact same-name production_active fallback or dispatcher row matched this Appendix A family. |
| `jailbreakbench` | `blocked_dependency` | `T2 framework/external dataset` | 0 | 8 | 8 | Appendix A disposition is blocked_dependency; embedded items do not enter active registry counts. Prior fallback counted 8 repo-proven production_active mapping/probe rows for the same family name. Active dispatcher has 8 executable rows matching this family/source name. |
| `mitigation_evidence` | `fixture_only` | `T4 config-only` | 0 | 1 | 1 | Appendix A disposition is fixture_only; embedded items do not enter active registry counts. Prior fallback counted 1 repo-proven production_active mapping/probe rows for the same family name. Active dispatcher has 1 executable rows matching this family/source name. |
| `p1_1_factuality_golden` | `executable_not_connected` | `T1 in-repo runner + embedded cases` | 30 | 30 | 30 | Appendix A embedded corpus items are T1 assets but not production_active because they are executable_not_connected. Prior fallback counted 30 repo-proven production_active mapping/probe rows for the same family name. Active dispatcher has 30 executable rows matching this family/source name. |
| `p1_3_consistency_golden` | `executable_not_connected` | `T1 in-repo runner + embedded cases` | 20 | 20 | 20 | Appendix A embedded corpus items are T1 assets but not production_active because they are executable_not_connected. Prior fallback counted 20 repo-proven production_active mapping/probe rows for the same family name. Active dispatcher has 20 executable rows matching this family/source name. |
| `p1_4_refusal_golden` | `executable_not_connected` | `T1 in-repo runner + embedded cases` | 20 | 20 | 20 | Appendix A embedded corpus items are T1 assets but not production_active because they are executable_not_connected. Prior fallback counted 20 repo-proven production_active mapping/probe rows for the same family name. Active dispatcher has 20 executable rows matching this family/source name. |
| `p1_5_calibration_golden` | `executable_not_connected` | `T1 in-repo runner + embedded cases` | 20 | 20 | 20 | Appendix A embedded corpus items are T1 assets but not production_active because they are executable_not_connected. Prior fallback counted 20 repo-proven production_active mapping/probe rows for the same family name. Active dispatcher has 20 executable rows matching this family/source name. |
| `p2_1_demographic_parity_gap` | `executable_not_connected` | `T1 in-repo runner + embedded cases` | 12 | 0 | 0 | Appendix A embedded corpus items are T1 assets but not production_active because they are executable_not_connected. No exact same-name production_active fallback or dispatcher row matched this Appendix A family. |
| `p2_2_equal_opportunity_gap` | `executable_not_connected` | `T1 in-repo runner + embedded cases` | 12 | 0 | 0 | Appendix A embedded corpus items are T1 assets but not production_active because they are executable_not_connected. No exact same-name production_active fallback or dispatcher row matched this Appendix A family. |
| `p2_3_counterfactual_stability` | `executable_not_connected` | `T1 in-repo runner + embedded cases` | 12 | 0 | 0 | Appendix A embedded corpus items are T1 assets but not production_active because they are executable_not_connected. No exact same-name production_active fallback or dispatcher row matched this Appendix A family. |
| `p2_4_eval_representation` | `executable_not_connected` | `T1 in-repo runner + embedded cases` | 12 | 0 | 0 | Appendix A embedded corpus items are T1 assets but not production_active because they are executable_not_connected. No exact same-name production_active fallback or dispatcher row matched this Appendix A family. |
| `p2_5_sentiment_skew_by_attribute` | `executable_not_connected` | `T1 in-repo runner + embedded cases` | 12 | 0 | 0 | Appendix A embedded corpus items are T1 assets but not production_active because they are executable_not_connected. No exact same-name production_active fallback or dispatcher row matched this Appendix A family. |
| `p2_6_mitigation_evidence` | `executable_not_connected` | `T1 in-repo runner + embedded cases` | 12 | 0 | 0 | Appendix A embedded corpus items are T1 assets but not production_active because they are executable_not_connected. No exact same-name production_active fallback or dispatcher row matched this Appendix A family. |
| `p3_2_indirect_injection_rob` | `executable_not_connected` | `T1 in-repo runner + embedded cases` | 12 | 12 | 12 | Appendix A embedded corpus items are T1 assets but not production_active because they are executable_not_connected. Prior fallback counted 12 repo-proven production_active mapping/probe rows for the same family name. Active dispatcher has 12 executable rows matching this family/source name. |
| `p3_3_secret_exfiltration_rob` | `executable_not_connected` | `T1 in-repo runner + embedded cases` | 12 | 12 | 12 | Appendix A embedded corpus items are T1 assets but not production_active because they are executable_not_connected. Prior fallback counted 12 repo-proven production_active mapping/probe rows for the same family name. Active dispatcher has 12 executable rows matching this family/source name. |
| `p3_5_multiturn_jailbreak` | `fixture_only` | `T4 config-only` | 0 | 4 | 4 | Appendix A disposition is fixture_only; embedded items do not enter active registry counts. Prior fallback counted 4 repo-proven production_active mapping/probe rows for the same family name. Active dispatcher has 4 executable rows matching this family/source name. |
| `p3_6_output_attack` | `fixture_only` | `T4 config-only` | 0 | 4 | 4 | Appendix A disposition is fixture_only; embedded items do not enter active registry counts. Prior fallback counted 4 repo-proven production_active mapping/probe rows for the same family name. Active dispatcher has 4 executable rows matching this family/source name. |
| `p4_audit_log` | `fixture_only` | `T4 config-only` | 0 | 0 | 0 | Appendix A disposition is fixture_only; embedded items do not enter active registry counts. No exact same-name production_active fallback or dispatcher row matched this Appendix A family. |
| `p4_eval_cadence` | `fixture_only` | `T4 config-only` | 0 | 1 | 1 | Appendix A disposition is fixture_only; embedded items do not enter active registry counts. Prior fallback counted 1 repo-proven production_active mapping/probe rows for the same family name. Active dispatcher has 1 executable rows matching this family/source name. |
| `p4_incident_response` | `fixture_only` | `T4 config-only` | 0 | 1 | 1 | Appendix A disposition is fixture_only; embedded items do not enter active registry counts. Prior fallback counted 1 repo-proven production_active mapping/probe rows for the same family name. Active dispatcher has 1 executable rows matching this family/source name. |
| `p4_kill_switch` | `fixture_only` | `T4 config-only` | 0 | 1 | 1 | Appendix A disposition is fixture_only; embedded items do not enter active registry counts. Prior fallback counted 1 repo-proven production_active mapping/probe rows for the same family name. Active dispatcher has 1 executable rows matching this family/source name. |
| `p4_owner_matrix` | `fixture_only` | `T4 config-only` | 0 | 1 | 1 | Appendix A disposition is fixture_only; embedded items do not enter active registry counts. Prior fallback counted 1 repo-proven production_active mapping/probe rows for the same family name. Active dispatcher has 1 executable rows matching this family/source name. |
| `p4_version_control` | `fixture_only` | `T4 config-only` | 0 | 1 | 1 | Appendix A disposition is fixture_only; embedded items do not enter active registry counts. Prior fallback counted 1 repo-proven production_active mapping/probe rows for the same family name. Active dispatcher has 1 executable rows matching this family/source name. |
| `perturbation_seeds` | `needs_validation` | `T3 cases without runner` | 20 | 1 | 1 | Appendix A disposition is needs_validation; embedded items do not enter active registry counts. Prior fallback counted 1 repo-proven production_active mapping/probe rows for the same family name. Active dispatcher has 1 executable rows matching this family/source name. |
| `pii_attestation` | `fixture_only` | `T4 config-only` | 0 | 1 | 1 | Appendix A disposition is fixture_only; embedded items do not enter active registry counts. Prior fallback counted 1 repo-proven production_active mapping/probe rows for the same family name. Active dispatcher has 1 executable rows matching this family/source name. |
| `pii_scan_time_detection` | `executable_not_connected` | `T1 in-repo runner + embedded cases` | 20 | 20 | 20 | Appendix A embedded corpus items are T1 assets but not production_active because they are executable_not_connected. Prior fallback counted 20 repo-proven production_active mapping/probe rows for the same family name. Active dispatcher has 20 executable rows matching this family/source name. |
| `pii_substrate` | `fixture_only` | `T4 config-only` | 0 | 0 | 0 | Appendix A disposition is fixture_only; embedded items do not enter active registry counts. No exact same-name production_active fallback or dispatcher row matched this Appendix A family. |
| `privacybench` | `blocked_dependency` | `T2 framework/external dataset` | 0 | 5 | 5 | Appendix A disposition is blocked_dependency; embedded items do not enter active registry counts. Prior fallback counted 5 repo-proven production_active mapping/probe rows for the same family name. Active dispatcher has 5 executable rows matching this family/source name. |
| `promptbench` | `blocked_dependency` | `T2 framework/external dataset` | 0 | 7 | 7 | Appendix A disposition is blocked_dependency; embedded items do not enter active registry counts. Prior fallback counted 7 repo-proven production_active mapping/probe rows for the same family name. Active dispatcher has 7 executable rows matching this family/source name. |
| `promptinject` | `blocked_dependency` | `T2 framework/external dataset` | 0 | 2 | 2 | Appendix A disposition is blocked_dependency; embedded items do not enter active registry counts. Prior fallback counted 2 repo-proven production_active mapping/probe rows for the same family name. Active dispatcher has 2 executable rows matching this family/source name. |
| `pyrit` | `blocked_dependency` | `T2 framework/external dataset` | 0 | 5 | 5 | Appendix A disposition is blocked_dependency; embedded items do not enter active registry counts. Prior fallback counted 5 repo-proven production_active mapping/probe rows for the same family name. Active dispatcher has 5 executable rows matching this family/source name. |
| `secret_exfil` | `executable_not_connected` | `T1 in-repo runner + embedded cases` | 13 | 0 | 0 | Appendix A embedded corpus items are T1 assets but not production_active because they are executable_not_connected. No exact same-name production_active fallback or dispatcher row matched this Appendix A family. |
| `self_contradiction_pairs` | `executable_not_connected` | `T1 in-repo runner + embedded cases` | 60 | 0 | 0 | Appendix A embedded corpus items are T1 assets but not production_active because they are executable_not_connected. No exact same-name production_active fallback or dispatcher row matched this Appendix A family. |
| `sentiment_skew` | `needs_validation` | `T1 in-repo runner (cases external/derived)` | 0 | 3 | 3 | Appendix A disposition is needs_validation; embedded items do not enter active registry counts. Prior fallback counted 3 repo-proven production_active mapping/probe rows for the same family name. Active dispatcher has 3 executable rows matching this family/source name. |
| `tool_misuse_param_tamper` | `executable_not_connected` | `T1 in-repo runner + embedded cases` | 12 | 12 | 12 | Appendix A embedded corpus items are T1 assets but not production_active because they are executable_not_connected. Prior fallback counted 12 repo-proven production_active mapping/probe rows for the same family name. Active dispatcher has 12 executable rows matching this family/source name. |
| `tool_misuse_scope_escape` | `executable_not_connected` | `T1 in-repo runner + embedded cases` | 12 | 12 | 12 | Appendix A embedded corpus items are T1 assets but not production_active because they are executable_not_connected. Prior fallback counted 12 repo-proven production_active mapping/probe rows for the same family name. Active dispatcher has 12 executable rows matching this family/source name. |
| `tool_misuse_unauthorized_call` | `executable_not_connected` | `T1 in-repo runner + embedded cases` | 12 | 12 | 12 | Appendix A embedded corpus items are T1 assets but not production_active because they are executable_not_connected. Prior fallback counted 12 repo-proven production_active mapping/probe rows for the same family name. Active dispatcher has 12 executable rows matching this family/source name. |
| `truthfulqa` | `needs_validation` | `T1 in-repo runner (cases external/derived)` | 0 | 2 | 2 | Appendix A disposition is needs_validation; embedded items do not enter active registry counts. Prior fallback counted 2 repo-proven production_active mapping/probe rows for the same family name. Active dispatcher has 2 executable rows matching this family/source name. |
| `stop_instruction_v1` | `executable_not_connected` | `T1 in-repo runner (offline, pass^k k=5)` | 1 | 0 | 0 | Appendix A embedded corpus items are T1 assets but not production_active because they are executable_not_connected. No exact same-name production_active fallback or dispatcher row matched this Appendix A family. |
| `peril_data_containment` | `executable_not_connected` | `T1 in-repo runner (peril eval)` | 6 | 0 | 0 | Appendix A embedded corpus items are T1 assets but not production_active because they are executable_not_connected. No exact same-name production_active fallback or dispatcher row matched this Appendix A family. |
| `peril_money_movement` | `blocked_dependency` | `T2 framework/external dataset` | ext(tau-bench) | 0 | 0 | Appendix A disposition is blocked_dependency; embedded items do not enter active registry counts. No exact same-name production_active fallback or dispatcher row matched this Appendix A family. |
| `peril_recipient_integrity` | `executable_not_connected` | `T1 in-repo runner (peril eval)` | 5 | 0 | 0 | Appendix A embedded corpus items are T1 assets but not production_active because they are executable_not_connected. No exact same-name production_active fallback or dispatcher row matched this Appendix A family. |
| `peril_tool_grounded_factuality` | `executable_not_connected` | `T1 in-repo runner (peril eval)` | 13 | 0 | 0 | Appendix A embedded corpus items are T1 assets but not production_active because they are executable_not_connected. No exact same-name production_active fallback or dispatcher row matched this Appendix A family. |
| `live_walker_inspect_section1_v0` | `production_active (conditional)` | `T0 wired to /scan (ARE_WALKER_CUSTOMER_AGENT, default OFF -> synthetic)` | 4 | 0 | 0 | Appendix A marks this family production_active conditionally; it counts as one registry family row and carries embedded prompts separately. No exact same-name production_active fallback or dispatcher row matched this Appendix A family. |

## Fallback-Only Family Discrepancies

These families existed in the prior probe-registry fallback production_active count but are absent from Appendix A. They remain non-authoritative for this close gate.

| Family | Fallback active rows | Dispatcher rows | Explanation |
|---|---:|---:|---|
| `hallusquatting_promptware` | 3 | 3 | Present in prior probe-registry fallback production_active mapping rows, absent from Appendix A family ledger; fallback is non-authoritative for this close gate. |
| `inspect_section1_v0` | 4 | 0 | Present in prior probe-registry fallback production_active mapping rows, absent from Appendix A family ledger; fallback is non-authoritative for this close gate. |
| `loop_stability_budget_guard` | 1 | 0 | Present in prior probe-registry fallback production_active mapping rows, absent from Appendix A family ledger; fallback is non-authoritative for this close gate. |

## Closure State

BEAAA-13761 close gate item B is now satisfied for the restored source: Appendix A is attached, consumed from the directive path, registry artifacts were regenerated from it, the hash is republished, and the fallback path is explicitly non-authoritative.
