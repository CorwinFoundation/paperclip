---
artifact_type: adr
owner: cto
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "BEAAA-24210 closes the five remaining thin scored-case floors while preserving BackBond Scanner scoring and calibration contracts."
contracts_referenced: ["are-scanner-api-v1@v1.3.0", "scanner-implementation-supervision-v1@v1.1"]
ticket_refs: ["BEAAA-24210", "BEAAA-24208"]
proof_points_cited: ["tests/test_beaaa24210_scored_floor.py", "tools/calibration-deltas/2026-08-18-7f59bf57-vs-beaaa24210-scored-floor.md"]
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted: ["No rubric, scoring, API, model, pricing, or tamper-resistance change; N=5 drift gate passed."]
---

# ADR-0069: Deterministic scored-case floor increment

Decision: add 35 single-turn, judge-free cases through the existing deterministic scored-lane executor. Additions are P1.2 +4, P1.6 +7, P2.5 +7, P3.2 +7, and P3.3 +10.

The cases carry pinned corpus versions and authored negative controls. Their lexical oracles emit explicit PASS or FAIL, stable payload and response hashes, and reachable YAML evidence references. They add evidence breadth without becoming raw-score sources.

The legacy BEAAA-23857 family constant remains unchanged so its frozen 32-case regression stays exact. BEAAA-24210 uses a separate bounded family constant shared by registry materialization and executor dispatch. The registered executor tamper-evidence SHA is refreshed to the immutable candidate content and remains fail-closed.

No rubric weight, calibration curve, scoring formula, BackBond Scanner API field, model, published-probe claim, or pricing semantic changes.

Verification:

- Strict registry validation: zero errors and zero runtime aliases.
- Final canonical scored counts: P1.2=20, P1.6=20, P2.5=20, P3.2=20, P3.3=20; total 249.
- Focused tests: 32 passed.
- Baseline and candidate N=5, seeds 0 through 4: every total and pillar delta 0; every spread 0.

Rollback: revert the single BEAAA-24210 implementation commit. This removes the five mappings, fixtures, registry/executor bindings, SHA refresh, tests, and calibration artifact together.
