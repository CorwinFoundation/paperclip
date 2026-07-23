---
artifact_type: other
owner: scanner_engineer
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "This policy contains Scanner accumulation risk by making temporary feature flags, deprecation shims, and rollback artifacts expire against evidence instead of memory."
contracts_referenced: ["are-scanner-api-v1@v1.3.0", "scanner-implementation-supervision-v1@v1.1"]
ticket_refs:
  - "BEAAA-15619"
  - "BEAAA-15604"
  - "BEAAA-15629"
  - "BEAAA-5275"
  - "BEAAA-13363"
  - "BEAAA-818"
proof_points_cited:
  - "Founder directive revision aecee8cb-a23c-4ed2-8ff3-585628140f93"
  - "ADR-0033 R1: one-release `bmi` shim removal follow-up"
  - "ADR-0057 Phase 3: snapshot-dir rollback retained one cycle, then retired"
  - "BEAAA-818 OTel mapping: legacy-name removal requires fresh CTO + Underwriter co-sign on a v2.0 contract ticket"
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted:
  - "Rule-execution process artifact only: no scoring, rubric, corpus, band, judge, probe-set, contract-surface, production-box, key, signing-material, or promotion change."
  - "N=5 calibration delta: n/a by construction; this artifact changes documentation/process only and does not execute or alter Scanner code."
  - "QA promotion gate: not triggered; no code, build, configuration, shared-environment, or production promotion."
---

Owner: scanner_engineer
Phase 1 gating items status: carrier paper N, reinsurance N, LOIs 0 of 3-5, wording N, Scanner v0.1 in execution hardening
Self-audit: passed
Escalations this week: none

# Scanner Subtractive Process And First Retirement List

## BLUF

[BEAAA-15619](/BEAAA/issues/BEAAA-15619) creates the mandatory removal-date rule and the first evidence-backed retirement list required by Founder directive revision `aecee8cb-a23c-4ed2-8ff3-585628140f93` on [BEAAA-15604](/BEAAA/issues/BEAAA-15604). This artifact does not remove code or configuration. Each retirement below becomes executable only through its own ticket, with the normal BackBond Scanner supervision gates.

## Removal-Date Rule

Every new feature flag, deprecation shim, transition adapter, rollback artifact, and temporary compatibility path in the BackBond Scanner repo must carry a ticketed removal record before merge. The record must live in the owning issue and, when the temporary path is documented in-repo, in the same artifact or adjacent runbook.

Required fields:

| Field | Requirement |
|---|---|
| `temporary_path` | Exact file, flag, key, route, artifact, or operational path. |
| `owner_issue` | The BEAAA ticket that owns removal or renewal. |
| `removal_condition` | Objective condition that makes retirement safe. Ticket closure alone is insufficient. |
| `removal_date` | Calendar date for review or removal. If a release/event gate is required, record both the event and a latest review date. |
| `dependency_proof` | Evidence that all readers, deploy paths, or rollback dependencies no longer require the path. |
| `verification` | Focused command, manifest check, checksum, or reviewer evidence required before removal. |
| `rollback` | How to restore the path if removal breaks an active consumer. |
| `contract_gate` | State whether removal touches scoring, rubric, corpus, band, judge, probe-set, contract surface, tamper resistance, production credentials, or promotion. |

Default dates:

| Temporary path class | Default latest review date |
|---|---|
| One-release deprecation shim | Seven days after the next scanner release is merged to canonical master. |
| Feature flag created for rollback safety | Fourteen days after the flagged feature is the presented default in production, unless the owning ADR sets a stricter window. |
| Operator rollback snapshot or bundle | One successful replacement deploy cycle after the rollback window, after checksum verification. |
| Transition adapter between old and new telemetry/envelope names | The next contract-major review, unless a CTO + Underwriter co-sign sets an earlier removal. |
| Test-only compatibility shim | Thirty days after the last active test fixture or harness consumer is migrated. |

Enforcement:

- PRs adding a temporary path without these fields fail review under [BEAAA-15619](/BEAAA/issues/BEAAA-15619).
- Removing a temporary path that touches Scanner output, scoring, rubric, corpus, band, judge, probe-set, or tamper resistance is classified under `scanner-implementation-supervision-v1@v1.1` before code work starts.
- Documentation-only retirement-list updates use `N=5 calibration delta: n/a by construction`; code removals use the normal golden-set gate unless the supervising contract explicitly permits a structural identity proof.
- Archives, bundles, and recovery snapshots are preserved until the related PR has merged and checksums are verified, per Founder directive [BEAAA-15604](/BEAAA/issues/BEAAA-15604).

## First Safe Retirement List

These are retirement candidates, not removals. Each row has enough evidence to open or update a removal ticket without re-investigating the whole repo.

| Candidate | Owner | Removal condition/date | Dependency proof | Verification before removal | Rollback |
|---|---|---|---|---|---|
| `rubric/scoring.py` emitted `"bmi"` compatibility key from ADR-0033 | Scanner Engineer under [BEAAA-5275](/BEAAA/issues/BEAAA-5275) follow-up | Remove after the next scanner release containing ADR-0033 has shipped and all direct `score()` readers use `are_total`; latest review: 2026-08-02. | ADR-0033 states the `"bmi"` key is a one-release deprecation shim and R1 carries a follow-up to remove it after the next release ships. Current direct-test evidence is `tests/test_scoring.py` asserting `are_total == bmi`; removal requires updating that test to assert absence of legacy key only after reader audit. | `rg -n '"bmi"|get\\("bmi"|\\[["'\\''"]bmi["'\\'']\\]' api harness rubric scanners tests scripts docs` shows no runtime reader except historical docs/tests intentionally updated in the removal PR; focused `python3 -m pytest tests/test_scoring.py tests/ -k "envelope or contract"`. | Re-add the alias line bound to the same integer and restore the compatibility assertion. |
| ADR-0057 snapshot-dir rollback retained for one cycle | CTO/deploy owner under [BEAAA-13363](/BEAAA/issues/BEAAA-13363) | Retire after two green ADR-0057 pipeline-posted deploy records and one successful replacement deploy cycle; review immediately after [BEAAA-15607](/BEAAA/issues/BEAAA-15607) closes. | ADR-0057 Phase 3 says snapshot-dir rollback is retained one cycle, then retired. Founder directive P1 requires exactly two green deploy records before production drift controls are considered live. | Verify both deploy records cite images built from `origin/master`, manual approval was exercised, auto-rollback observation was recorded, and snapshot checksums match before archive deletion. | Restore the previous snapshot directory from the preserved archive and re-run the ADR-0057 acceptance checks. |
| `harness/otel_shim.py` legacy-to-OTel export compatibility path | CTO + Underwriter if removal is ever proposed; current owner remains Scanner evidence pipeline | Do not remove under v1.x. Review only at the next contract-major telemetry surface decision or OTel GenAI semantic conventions GA, whichever comes first. | `docs/BEAAA-818_evidence_refs_otel_mapping_v0.2.md` states old names are not removed in v1.0.4 and legacy-name removal requires fresh CTO + Underwriter co-sign on a v2.0 contract ticket. | No code removal without a v2.0 contract ticket, all exporter consumers migrated, and contract tests proving the new telemetry names are the only supported customer-facing surface. | Re-enable the shim at span export time; scoring pipeline remains unaffected by design. |
| `api/scan_handlers.py` and `api/scan_runtime_factory.py` transitional concentration risk | P4 decomposition owner under [BEAAA-15618](/BEAAA/issues/BEAAA-15618) | Not a direct deletion candidate. Create decomposition tickets after the [BEAAA-15618](/BEAAA/issues/BEAAA-15618) plan is accepted; first review date: 2026-08-09. | Founder directive P4.2 names both files for decomposition planning, protected by golden roster and contract tests. This row prevents adding new long-lived compatibility branches to either file without a removal record. | Decomposition PRs must cite unchanged contract output shape and run focused scan-handler/runtime-factory contract tests plus N=5 if any behavior path can affect score evidence. | Revert the specific decomposition PR; do not restore broad file growth as an accepted steady state. |
| Test-only legacy fixture shims (`tests/test_agent_interface.py`, Inspect runner compatibility tests) | Scanner Engineer owning the related fixture migration | Review 30 days after [BEAAA-15618](/BEAAA/issues/BEAAA-15618) decomposition plan acceptance, or earlier when the last legacy fixture shape is migrated. | Existing comments identify legacy fixture or harness compatibility, not production scoring behavior. They are safe candidates only after the fixture/harness readers are migrated. | `rg -n "legacy|shim|compat" tests harness scanners` inventory attached to the removal PR; focused tests for migrated fixture consumers. | Restore the local test shim only; production code remains untouched. |

## Intake Checklist For Future Temporary Paths

Before merging a temporary path, answer:

1. What exact behavior disappears when this path retires?
2. Which open BEAAA ticket owns the retirement?
3. What date or event makes removal mandatory for review?
4. What objective proof shows no active reader or rollback dependency still needs it?
5. What focused verification proves removal did not alter scoring, evidence completeness, or contract output?
6. Does removal need CTO, Underwriter, QA, or Founder involvement under the supervision contract, QA promotion gate, or Founder directive?

## Verification

- Artifact type: internal process document.
- Contract anchor: `are-scanner-api-v1@v1.3.0`; no output shape changed.
- Classification: rule execution, matching `scanner-implementation-supervision-v1@v1.1` row `Refactor that doesn't touch behavior` by analogy; this artifact only creates process and retirement candidates.
- Scanner state fields: `scanner_version` unchanged (`0.5.0`), `corpus_version` unchanged, `probe-set-version` unchanged, `rubric_point_id` unchanged, `N=5 calibration delta` n/a by construction.
- QA promotion gate: not triggered.
