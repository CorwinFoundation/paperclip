---
artifact_type: implementation_closeout
owner: scanner_engineer
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "BEAAA-16945 adds production-connected reported-not-scored evidence for high-severity data isolation and consequential-action controls without changing BackBond Scanner pricing."
contracts_referenced: ["are-scanner-api-v1@v1.0", "scanner-implementation-supervision-v1@v1.1"]
ticket_refs: ["BEAAA-16945", "BEAAA-16943"]
proof_points_cited: ["evals/aiuc_path_b/cross_session_data_leak.py", "evals/aiuc_path_b/consequential_action_confirmation.py", "evals/aiuc_path_b/wave1a_candidate.py", "tests/test_beaaa16945_wave1a_candidate.py::test_wave1a_signature_is_stable_across_exact_candidate_sha"]
version: 0.2
prior_version: "artifacts/beaaa-16945/wave1a-candidate-report.md@v0.1"
self_audit_passed: true
escalations_noted: ["Prior candidate cd349cd4b2d205d69492ad94c4088a7f7fb1f000 was rejected by QA on BEAAA-16965 because its report signature was not reproducible and the report was outside the immutable candidate.", "Public or production promotion remains blocked pending exact-SHA QA APPROVED on BEAAA-16965 and Founder sign-off."]
---

# BEAAA-16945 Wave 1A Candidate Report

BLUF: Wave 1A now has an in-repo reported-not-scored candidate path for `cross_session_data_leak` and `consequential_action_confirmation`, plus an immutable-report fix for QA's `cd349cd4...` reproducibility finding. Targeted tests pass, direct golden N=5 replay is delta 0, and underwriting-active count remains 0.

## Candidate Scope

- Baseline commit: `5c084879167956be6e31e507b1b94509ab586596`.
- Rejected candidate commit: `cd349cd4b2d205d69492ad94c4088a7f7fb1f000`.
- Replacement candidate commit: exact SHA recorded on BEAAA-16965 handoff; the packet records whichever candidate SHA QA supplies in `candidate_commit_sha`.
- Contract: `are-scanner-api-v1@v1.0`.
- Scanner version: `0.5.0`.
- Promotion state: `reported_not_scored`.
- Underwriting state: `not_underwriting_active`.
- Model versions: none; deterministic local oracle only.
- Candidate manifest signature: `sha256:da5e5210cf25aeb64bc3fb585de629ebbd81ffe31b2f4dac5fe5040a9404f5a7`.
- Signature preimage excludes `candidate_commit_sha` and `signed_manifest`; this avoids an impossible Git self-reference while preserving the exact SHA as a packet field for QA comparison.

## Counts

| Family | Unique scenarios | Seeds | Executions | Underwriting-active |
|---|---:|---:|---:|---:|
| `cross_session_data_leak` | 20 | 0-4 | 100 | 0 |
| `consequential_action_confirmation` | 15 | 0-4 | 75 | 0 |
| Total | 35 | 0-4 | 175 | 0 |

## Pins

| Pin | Value |
|---|---|
| `cross_session_data_leak` corpus SHA-256 | `f3546ba138e1455516cf4f41397895fe55d58a7112c2fa58c966593eee795edf` |
| `consequential_action_confirmation` corpus SHA-256 | `6800b57caf9c8438244aaebf506e839ea1665d22787e083cc5bb50b80faa3c16` |
| Wave 1A registry config SHA-256 | `92a4b1c9a37beee89de39019192d651ee07b8583d0b1ce717d2abfc8d9756029` |

## Evidence Hashes

| Family | Seed 0 | Seed 1 | Seed 2 | Seed 3 | Seed 4 | Trace SHA-256 | Canary |
|---|---|---|---|---|---|---|---|
| `cross_session_data_leak` | `c96c1191ff744891c8f230e38e04e3544ae35be8cd48a52b82779fad2b9f9327` | `3c80b05456357bf79ca4fcdcbdefbbb9c6a0744044e0bdf5bfa26dd4583d7bc7` | `f0105b470a4b95e09999529804d25d636c386c3ed7de4f8eff6e8116649c92ac` | `7f616d85b3593f93f1962a823bed5252e766766ce772d603fe790d203a42b550` | `c54d8018290347681f7fb36214c930e22a87563d0f91d91f7c7f29456d37df95` | `09682d6383e42f2c0735302f2447c940777d6dcb6a4ade313fe3efe90e45f989` | `csdl-20-canary` |
| `consequential_action_confirmation` | `f0987847883516ef565958eaa5752d38d831edb5642098cb3cf9f7abe7b8a5fe` | `871313b0153fd4ab15c4eb4d447461b55ace8465a6958dcd73d91311907b3d48` | `e1978c2f1a2f1088f904dfaba3b845651f1c93af3afd13e316cb53acbe85f9cf` | `7b28d233a06b270dd6de174654a215deb50bfc8ea0f1bfd515fa4b353ec40377` | `dd85f11495d5978fdb66bef422d75eb54c5605ff4298a07dec7a6bd2b7da9671` | `6019371a4ce7155003f34e3ffdc41e21e61f455b5d081afd690557976f5176d4` | `cac-15-canary` |

## Authorization And Fail-Closed Controls

The candidate packet requires explicit target authorization, authorization signature, tenant boundary, session boundary, declared tool inventory, use-case scope, exact family scope, corpus/config SHA-256 pins, immutable evidence, and complete K=5 execution. Any missing or conflicting field returns `not_underwriting_active` with `underwriting_active_count: 0`.

Fail-closed regression coverage includes missing authorization, missing declared tools, family-scope conflict, corpus hash mismatch, mutable evidence, evaluator incompletion, strict missing decision fields, and non-boolean decision fields.

## Verification

- `pytest -q tests/test_beaaa16945_wave1a_candidate.py tests/test_aiuc_path_b_lane_c_beaaa16272.py tests/test_beaaa16647_aiuc_count_freeze.py` -> 96 passed.
- Direct golden N=5 scorer replay -> PASS, total delta 0 and pillar delta 0 for all available locked golden fixtures. See `tools/calibration-deltas/2026-07-22-beaaa16945-wave1a-direct-n5.md`.
- `test_wave1a_signature_is_stable_across_exact_candidate_sha` proves the packet records the supplied exact candidate SHA while the signed evidence-body signature regenerates exactly.

## Remaining Gate

BEAAA-16965 remains the one canonical QA child. The rejected candidate must be marked `SUPERSEDED: cd349cd4b2d205d69492ad94c4088a7f7fb1f000 -> <replacement SHA>` in the issue handoff, then QA must personally retest the exact replacement SHA before any shared or production promotion.
