---
artifact_type: other
owner: cto
persona_audience: prospect_ai_vendor
phase: 2
canonical_terms_only: true
focus_claim: "These release notes position BackBond Scanner v0.5 RC as the evidence-grade step toward insuring AI agents without making a v1.0 public-label claim."
contracts_referenced: ["are-scanner-api-v1@v1.3.0"]
ticket_refs: ["BEAAA-6190", "BEAAA-6188", "BEAAA-6187", "BEAAA-7321"]
proof_points_cited: ["tools/calibration-deltas/2026-06-22-beaaa6190-v05-rc-n5.md", "artifacts/beaaa-6187/BEAAA-6187_final_merge_closeout_v0.2.md"]
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted: ["No carrier names. No v1.0 label claim. No outcome-promise language."]
---

# BackBond Scanner v0.5 RC Release Notes

BLUF: BackBond Scanner v0.5 RC is an internal release candidate for the v1.0 path. It tightens release discipline, evidence custody, and tamper regression coverage while keeping the customer-facing contract pin at `are-scanner-api-v1@v1.3.0`.

## What Changed

- Scanner build stamp moves to `0.5.0`.
- Release CI now runs deterministic golden regression plus the BEAAA-6190 N=5 gate.
- The 40-point live-golden discrimination spread floor is enforced as a release gate.
- Customer-VPC evidence URI projection is available behind `BACKBOND_EMIT_MODE=customer_vpc`.
- Internal tamper red-team regression coverage is included for the four contracted surfaces: intake guard, judge invocation path, report-card renderer, and forensic bundle signer.

## What Did Not Change

- No rubric weights changed.
- No scoring math changed.
- No judge model changed.
- No default score-output field shape changed.
- No v1.0 public label is claimed by this release candidate.

## Version Pins

| Surface | v0.5 RC value |
|---|---:|
| scanner_version | `0.5.0` |
| contract_version | `are-scanner-api-v1@v1.3.0` |
| rubric_version | `1.2.0` |
| probe-set-version | `golden_agents_v1` |

## Release Gate

The RC gate requires N=5 reproducibility on every live golden fixture, explicit accounting for pending roster slots, and live-golden discrimination spread of at least 40 BackBond Score points.

This is not a policy promise. It is scanner release evidence used by BackBond to underwrite AI-agent exposure.
