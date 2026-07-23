---
artifact_type: implementation_closeout
owner: scanner_engineer
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "BEAAA-16992 binds Scanner production controls, rollback proof, and evidence retention into one internal gate for the customer-ready BackBond Scanner release."
contracts_referenced: ["are-scanner-api-v1@v1.0", "scanner-implementation-supervision-v1@v1.1"]
ticket_refs: ["BEAAA-17081", "BEAAA-17075", "BEAAA-17073", "BEAAA-17052", "BEAAA-17051", "BEAAA-16992", "BEAAA-16994", "BEAAA-16987", "BEAAA-16979", "BEAAA-16973"]
proof_points_cited: ["api/production_controls.py", "scripts/beaaa16992_production_controls_evidence.py", "artifacts/beaaa-16992/production-controls-evidence.json", "ops/scan-log-backup/README.md", "docs/deploy/sequence-20-production-deployment-runbook.md", "api/edge_gateway.py"]
version: 0.5
prior_version: "artifacts/beaaa-16992/production-controls-closeout.md@v0.4"
self_audit_passed: true
escalations_noted: ["Rule-execution production-control evidence harness and closeout. No scoring math, band cut, judge model, rubric weight, Scanner API output shape, covered peril, customer data-handling expansion, or tamper-resistance reduction. Public or production promotion remains blocked on exact-SHA QA APPROVED plus Founder sign-off."]
---

# BEAAA-16992 Production Controls Closeout

BLUF: PASS for [BEAAA-17081](/BEAAA/issues/BEAAA-17081) exact-SHA production-control evidence binding; NOT authorized for shared or production promotion. Exact-SHA QA approval and Founder sign-off remain required.

## Scope

This is a production-control evidence harness and internal closeout for CTO review that proves tenant isolation, authn/authz, secret handling, retention, rate limits, monitoring, backup/restore, incident response, design-partner instrumentation hooks, and <=30-minute rollback within the existing BackBond Scanner contract surface.

## Classification

Rule-execution under `scanner-implementation-supervision-v1@v1.1` rows:

- Smoke test for an existing operation.
- Refactor/internal control path that does not touch behavior.
- Operational tooling with no Scanner API output-shape change.

No CTO co-sign is required for this implementation closeout. Escalation would be required if QA or CTO turns this into a scoring, judge, contract, data-handling, or tamper-resistance change.

## Evidence

Retained packet: `artifacts/beaaa-16992/production-controls-evidence.json`.

The packet binds:

- Candidate baseline: `fb58d5e5cf7d132afd55c177f4a385ba5b29c76a`.
- Candidate identity: `candidate_commit_sha` and `evidence_build_head_sha` are both derived from `git rev-parse HEAD` at packet build time. The packet includes `candidate_identity_matches_build`; any mismatch sets `candidate_identity_failure_code: candidate_commit_sha_mismatch` and blocks `overall_pass`.
- Signed Wave 1A target input: `authorized_wave1a_target_commit_sha` remains the historical production-authority fixture `6f9733632b3569f1eb46445e4b2a7c2522761f48`; it is not used as the production-control candidate identity.
- Runtime production-control source: `api/production_controls.py`.
- Prior Wave 1A production-like evidence: `artifacts/beaaa-16979/*`.
- Deployment controls: `docs/deploy/sequence-20-production-deployment-runbook.md`.
- Edge auth/rate-limit controls: `api/edge_gateway.py`.
- Backup controls: `ops/scan-log-backup/backup_scan_log.py`, `restore_scan_log.py`, and `README.md`.

Acceptance results:

- Candidate identity matches build SHA: PASS.
- Authorized end-to-end design-partner scan: PASS.
- Tenant isolation and evidence retention: PASS by temporary tenant-separated scan-log rows plus retained evidence hashes.
- Backup restore RPO <=1h / RTO <=4h: PASS by temporary scan-log online backup + restore dry-run; RPO is measured from inserted scan-log timestamp to backup completion.
- Monitored rollback <=30m: PASS by a timed local registry rollback mutation proving `340 -> 305` active executable rows with unrelated rows unchanged.
- Critical/high security findings: PASS through `api.production_controls.repo_security_findings`; any `critical` or `high` finding is blocking and included in `overall_pass`.
- Auth/rate limit controls: PASS by executable `api.edge_gateway` checks for valid tenant resolution, revoked and expired keys, max-concurrent denial, scans-per-minute denial, and tenant-b isolation from tenant-a bucket exhaustion.
- Instrumentation: PASS; result record, customer-feedback hook, and eventual-outcome hook are written through `api.production_controls.ProductionControlLedger` to retained JSONL ledgers with per-record SHA-256 validation and read back from `artifacts/beaaa-16992/instrumentation-store/`.
- Runtime wiring: PASS; `ScanRuntime` accepts an optional `ProductionControlLedger` and shipped security-findings source, `make_default_production_scan_runtime()` wires both under the mounted Scanner state tree, and the scan handler regression proves repeated identical runtime scans retain one result record instead of accumulating stale duplicate rows.
- Rollback retention: PASS; retained evidence is written under the same `ARE_STATE_DIR` host-mounted tree that `scripts/deploy/box_deploy.sh` mounts to `/var/lib/are-scanner`, and the rollback proof fails if the mount is absent or the retained evidence hash changes.

## Verification

```text
python3 scripts/beaaa16992_production_controls_evidence.py
```

Result: PASS. Output path `artifacts/beaaa-16992/production-controls-evidence.json`; rollback measured minutes ceil `1`; backup/restore integrity `true`.

```text
pytest -q tests/test_beaaa16992_production_controls.py tests/test_release_gate_n5_beaaa10187.py
```

Result: `25 passed`.

Regression coverage added under [BEAAA-17052](/BEAAA/issues/BEAAA-17052):

- Rollback hash proof fails when retained evidence is absent before rollback.
- Rollback hash proof fails when retained evidence is tampered after rollback.
- Security findings fail closed for `critical` and `high` severities.
- Packet `overall_pass` fails when the shipped security source returns a high finding.
- Instrumentation proof fails when a required retained ledger is absent.
- Instrumentation proof fails when a retained ledger row hash is tampered.
- Instrumentation proof cites the shipped runtime ledger source.
- Rollback proof fails when the retained evidence is not under the mounted state path.
- Scan handler writes one retained runtime production-control result record across two identical runs.
- Runtime security findings source blocks scan execution before `run_scan_fn` when a high finding is present.

Regression coverage added under [BEAAA-17081](/BEAAA/issues/BEAAA-17081):

- The packet top-level `candidate_commit_sha` equals `git rev-parse HEAD`.
- The retained instrumentation result record writes the build SHA, not the historical signed Wave 1A target SHA.
- A supplied candidate SHA that differs from the build SHA fails closed with `candidate_commit_sha_mismatch` and blocks `overall_pass`, even if the historical signed Wave 1A target remains valid.

```text
python3 scripts/beaaa539_n5_calibration.py
```

Result: PASS. B1/G1/G2/M1/M2/M3 all observed five identical totals. Total spread `0`; total delta `0`; max pillar spread `0`; max pillar delta `0`; `all_in_band_pm1=True`.

```text
python3 scripts/release_gate_n5.py --baseline 1b93c090-beaaa17051-pre-repair --candidate beaaa17075-production-controls-residual-repair --today 2026-07-23 --out-dir "$PWD/tools/calibration-deltas"
```

Result: PASS for the [BEAAA-17075](/BEAAA/issues/BEAAA-17075) residual repair. The release runner writes `tools/calibration-deltas/2026-07-23-1b93c090-beaaa17051-pre-repair-vs-beaaa17075-production-controls-residual-repair.md`; single-turn spread PASS, single-turn delta PASS, approval provenance PASS, unresolved movement `[]`, `release_reliance_authorized=true`.

## Operational Metadata

- `scanner_version`: `0.5.0`
- `corpus_version`: golden fixture corpus versions plus Wave 1A expected hashes
- `probe-set-version`: `cross_session_data_leak@0.1.0`; `consequential_action_confirmation@0.1.0`
- `rubric_point_id`: `P3.9`; `ROBUSTNESS.DATA_ISOLATION.CROSS_SESSION_DATA_LEAK`; `P3.7`; `ROBUSTNESS.TOOL_MISUSE.CONSEQUENTIAL_ACTION_CONFIRMATION`
- `N=5 calibration delta`: `tools/calibration-deltas/2026-07-23-beaaa16979-wave1a-scored-candidate-n5.json`; total spread `0`; total delta `0`; max pillar spread `0`; max pillar delta `0`; `all_in_band_pm1=True`
- `cto_cosign_ref`: N/A, rule-execution
- `contract`: `are-scanner-api-v1@v1.0`
- `merged_sha_on_canon_master_at_close`: not applicable until this work is merged to canonical master; do not close the implementation parent `done` before reachability is proven

## FAL Audit

- Focus: Pass. The artifact supports continuous Scanner evidence behind the trust layer.
- Alignment: Pass. It makes no new score, pricing, coverage, or insurance-maturity claim.
- Linkage: Pass. It uses the shared BackBond Scanner evidence and rollback-control language from the release program.

## Gate

Shared or production promotion remains blocked until the QA Engineer records evidence-backed `APPROVED` on the canonical QA child for the exact candidate SHA, and public or production promotion also receives Founder sign-off. This packet is evidence for that gate, not a bypass.
