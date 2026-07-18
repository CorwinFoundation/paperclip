---
artifact_type: report
owner: scanner_engineer
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "BEAAA-15506 restores the BackBond Scanner demo readiness gate so only required registry evidence controls promotion readiness while portable attestations remain reproducible."
contracts_referenced: ["are-scanner-api-v1@v1.0", "scanner-implementation-supervision-v1@v1.1"]
ticket_refs: ["BEAAA-15506", "BEAAA-15498"]
proof_points_cited: ["artifacts/beaaa-15506/readiness-perfect-278.json", "artifacts/beaaa-15506/n5-release-gate.json", "tests/test_demo_readiness_gate_beaaa15495.py"]
version: "0.1"
prior_version: null
self_audit_passed: true
escalations_noted: ["Rule-execution bug fix under scanner-implementation-supervision-v1@v1.1 rows 'Smoke test for an existing operation' and 'Refactor that does not touch behavior'; no scoring math, judge model, rubric weight, contract output shape, or tamper-resistance posture changed.", "FAL audit file agents/cmo-os.md was not present in this workspace; artifact is internal and no external-bound Brand Backbone gate applies."]
---

# BEAAA-15506 Closeout

BLUF: readiness semantics now fail closed only on required/live readiness-bearing rows and explicit registry contradictions; the shipped 278-row registry can return `ready:true` with 90 complete required cases while the 188 non-countable rows remain visible as `not_required_by_registry` reporting rows.

## Classification

Rule-execution bug fix for the CTO. It changes the readiness gate around existing evidence and registry metadata, not scoring math, rubric weights, judge models, or the `are-scanner-api-v1@v1.0` output contract.

## Evidence

- Focused pytest: `python3 -m pytest tests/test_demo_readiness_gate_beaaa15495.py -q` -> 10 passed.
- Perfect shipped-registry feed: `python3 scripts/check_demo_readiness_gate.py --registry tools/probe-registry/active_registry.json --evidence artifacts/beaaa-15506/perfect-feed --calibration artifacts/beaaa-15506/golden-clean.json --repo-root "$PWD" --output artifacts/beaaa-15506/readiness-perfect-278.json` -> exit 0.
- Perfect shipped-registry result: `ready=true`, `ready_set=90`, `registry.case_count=278`, `registry.required_case_count=90`, `registry.non_countable_reporting_count=188`, `rejected_cases=188`, `ready_set_vs_live_registry_delta.status=quiet`.
- Portable attestation: focused test `test_semantic_sha256_is_independent_of_absolute_workspace_path` covers same logical registry/evidence/calibration under two absolute roots with identical `semantic_sha256`.
- Ready-set delta: focused tests cover empty quiet delta and non-empty `ready_candidate` delta with evidence links.
- N=5 golden release gate: `python3 scripts/release_gate_n5.py --baseline current-master --candidate beaaa15506-readiness-semantics --today 2026-07-18 --json-out "$PWD/artifacts/beaaa-15506/n5-release-gate.json" --out-dir "$PWD/artifacts/beaaa-15506"` -> exit 0; JSON `passed=true`, `single_turn_passed=true`, `single_turn_spread_passed=true`, `single_turn_delta_passed=true`.

## Metadata

- scanner_version: `0.5.0`
- corpus_version: `0.1.1`
- probe-set-version: `active-probe-registry`
- rubric_point_id: active registry rows in `tools/probe-registry/active_registry.json`
- N=5 calibration delta: `artifacts/beaaa-15506/n5-release-gate.json`, passed
- cto_cosign_ref: not required; rule-execution classification
