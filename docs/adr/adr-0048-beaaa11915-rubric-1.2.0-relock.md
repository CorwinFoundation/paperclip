---
artifact_type: adr
owner: cto
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "Re-lock the lapsed rubric 1.2.0 freeze window (2026-06-30 -> 2026-08-04) with byte-identical content so no partner-visible or externally reported BackBond Scanner scan runs against an unlocked rubric."
contracts_referenced: ["are-scanner-api-v1@v1.0", "scanner-implementation-supervision-v1@v1.0"]
ticket_refs: ["BEAAA-11915", "BEAAA-11914", "BEAAA-10199"]
proof_points_cited: ["rubric/rubric.yaml", "README.md", "scripts/release_gate_n5.py", "tools/calibration-deltas/2026-07-05-v1.2.0-locked-vs-v1.2.0-relock.md"]
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted: ["No Underwriter/CEO co-sign gate triggered: rubric 1.2.0 content byte-identical, golden N=5 Δ0 on total and all four pillars across the 8/9 baselined roster; only locked_through metadata moved. CTO-unilateral. UW notified on BEAAA-11915 for awareness, not approval."]
---

# ADR-0048 — Rubric 1.2.0 re-lock (external-readiness WI-1)

**Status:** Accepted (CTO-unilateral, 2026-07-05)
**Owner:** CTO
**Ticket:** BEAAA-11915 (WI-1, External Readiness work-items, BEAAA-11914)

## Context

The External Readiness plan (BEAAA-11914) makes rubric re-lock the hard gate for
the first authorized external scan (Section D, days 8–21). `rubric/rubric.yaml`
declared `version: "1.2.0"` with `locked_through: "2026-06-30"` (last set by
`ced667b`, CTO, 2026-06-05). As of 2026-07-05 that freeze window has **lapsed**.

Company Canon §10 names `are-scanner/` (this repo, `origin/master`) as the sole
authoritative scanner source; `-review` / `-snapshot` copies are not. Verified
against `origin/master` HEAD `4656a56`: the lock is expired.

Frozen-version scoring discipline (Supreme Law; canon §2/§7 recency window):
**no external scan result may be produced against an unlocked rubric.** The lock
is a currency window on an otherwise-immutable scoring surface, not a content
change.

## Decision

Re-lock the **unchanged** rubric 1.2.0 by extending `locked_through` to
`2026-08-04` — a 30-day monthly-cadence window (canon §2 monthly rescans) that
covers the Section D external-scan window (days 8–21, through ~2026-07-26) with
buffer.

- **No content change.** Dims (25: 6 OPS / 6 BIAS_FAIRNESS / 6 ROBUSTNESS / 7
  GOVERNANCE), pillar weights (25/25/25/25, Founder-locked 2026-05-12), 12-band
  cuts, calibration curves, and `rubric/scoring.py` math are byte-identical to
  the 2026-06-30 lock. `locked_through` is parsed into the `Rubric` dataclass
  (`rubric/scoring.py:49,122`) but never read by `score()` — it cannot move a
  score by construction.
- `README.md` "Current versions" updated to `locked through 2026-08-04`.

## Why CTO-unilateral (no UW/CEO co-sign gate)

Per `scanner-implementation-supervision-v1.md@v1.0` and the CTO standing
reproducibility procedure: a change with every golden delta within ±1 is clean —
note in the ADR and merge. This re-lock is Δ0 by construction and empirically:

- `scripts/release_gate_n5.py --baseline v1.2.0-locked --candidate v1.2.0-relock`
  (seeds 0–4, 8/9 baselined roster per BEAAA-10199 B2 exclusion) → **PASS**,
  Δ0 on `are_total` and GOVERNANCE / ROBUSTNESS / BIAS_FAIRNESS / OPS for every
  agent (G1 G2 G3 M1 M2 M3 B1 B3). Evidence:
  `tools/calibration-deltas/2026-07-05-v1.2.0-locked-vs-v1.2.0-relock.md`.

The co-sign gate in AGENTS.md fires on rubric **weight or scoring-logic** changes
and on any golden move >1 point — none of which this is. Underwriter is
**notified** on BEAAA-11915 (pricing stability preserved), not asked to approve.

## Scan-output reference (Done-criterion: "referenced by scan outputs")

Scan outputs already reference the pinned rubric: `rubric/scoring.py` stamps
`rubric_version: rubric.version` ("1.2.0") into the score response, and
`harness/contract_envelope.to_contract_output` spreads that response into the
contract envelope. The scanner loads `rubric/rubric.yaml` at scan time, so a
scan's `rubric_version: 1.2.0` traces to the current, pinned lock window in that
same file. The lock is therefore referenced by every scan output today.

**Hardening follow-up (out of scope here, requires UW gate):** surface
`locked_through` as an explicit envelope field so a downstream reader
(Underwriter/auditor) can verify a scan ran under a *non-expired* lock without
re-reading `rubric.yaml`. That is an additive `are-scanner-api-v1` contract
change → Underwriter approval gate. Tracked as a follow-up on BEAAA-11915; it is
**not** required to clear WI-1, since the lock is already current, pinned, and
referenced via `rubric_version`.

## Rollback

Revert this commit: restore `locked_through: "2026-06-30"` and the README line.
Fully reversible; no data migration, no score impact.

## Red-line compliance (BEAAA-11915)

This ADR authorizes no external artifact and implies no warranty/insurance. It
only re-establishes the pre-condition ("current lock") that a *future*
authorized external scan requires. All other red lines (no OSS CI/CD publish
before probe protection, no public Trials, no "measured" proxy results,
"warranty remedy eligibility" not "coverage") are untouched.
