---
artifact_type: adr
owner: cto
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "Surface the pinned rubric lock horizon as an additive `rubric_locked_through` field on the BackBond Scanner scan-output envelope so lock currency is self-describing per score — a downstream reader (Underwriter / auditor) can verify a scan ran under a non-expired lock without re-reading rubric/rubric.yaml."
contracts_referenced: ["are-scanner-api-v1@v1.9.0 -> v1.10.0 (proposed additive minor)", "scanner-implementation-supervision-v1@v1.0", "uw-rubric-supervision-v1@v1.0"]
ticket_refs: ["BEAAA-11930", "BEAAA-11915", "BEAAA-10199"]
proof_points_cited: ["rubric/scoring.py", "harness/contract_envelope.py", "api/scan_runtime_factory.py", "contracts/are-scanner-api-v1-v1.10.0-patch.md", "tools/calibration-deltas/2026-07-05-v1.2.0-relock-vs-beaaa11930-locked-through.md"]
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted: ["Underwriter co-sign REQUIRED before merge: this is a response/output-shape change to are-scanner-api-v1 (v1.9.0 -> v1.10.0). NO Founder gate: rubric_locked_through is a top-level envelope metadata field (sibling to rubric_version), not a response evidence_refs field; pillar weights 25/25/25/25 unchanged; are_total unchanged; golden N=5 Δ0. CONTRACT_VERSION literal unchanged (v1.3.0, pinned by tests/test_verdict_envelope_beaaa796.py)."]
---

# ADR-0049 — Surface `rubric_locked_through` on the scan-output envelope

**Status:** PROPOSED (CTO implementation done; Underwriter co-sign pending — contract change)
**Owner:** CTO
**Ticket:** BEAAA-11930 (hardening follow-up to WI-1 rubric re-lock BEAAA-11915)

## Context

WI-1 (BEAAA-11915) re-locked rubric 1.2.0 because the prior freeze window had
silently **lapsed** — the exact failure mode the lock exists to prevent. The
scan-output envelope already stamps `rubric_version: "1.2.0"` (via
`rubric/scoring.py` → `harness/contract_envelope.to_contract_output`), which
traces to the pinned lock in `rubric/rubric.yaml`. But the envelope does **not**
carry the lock *horizon* (`locked_through`), so a downstream reader — the
Underwriter pricing engine, or a future auditor — cannot verify a scan ran under
a **non-expired** lock without re-reading the rubric file. That file may not be
reachable from where the score is consumed, and its lapse is precisely what
silently breaks pricing currency. Every gap in self-description is a "trust-me"
gap the Underwriter has to either charge for or refuse.

## Decision

Add ONE additive, response-side field, `rubric_locked_through`, to the
scan-output envelope, stamped in `rubric/scoring.py` immediately after
`rubric_version`:

```python
"rubric_version": rubric.version,
"rubric_locked_through": rubric.locked_through,   # BEAAA-11930
```

- **Source of truth:** the pinned `locked_through` in `rubric/rubric.yaml`
  (loaded into `Rubric.locked_through`), stamped verbatim as an ISO 8601 date.
- **Flow-through, no envelope edit:** `to_contract_output` builds
  `contract_output = dict(score_result)` and `.update()`s only named keys, so
  the field rides into the CLI/customer envelope automatically; the API
  flag-on path (`api/scan_runtime_factory`) does `envelope = dict(scorer_envelope)`,
  so the API score envelope carries it too.
- **Currency is derived by the reader:** a scan is under a current lock iff
  `run_timestamp.date() <= rubric_locked_through`.

### Rejected alternative — a precomputed `rubric_lock_current` boolean

The ticket floated an optional computed boolean. **Rejected.** `run_timestamp`
is finalized at the API edge (`api/scan_runtime_factory` overwrites the
Scorer-side stamp with the kickoff-finalize timestamp so it matches the
`scan_log` row). A boolean computed at score time could therefore contradict the
emitted `run_timestamp` on the lock-expiry day. Surfacing the raw horizon and
letting the reader compare against the authoritative emitted timestamp is always
self-consistent — the correct primitive for a hardening change whose purpose is
to remove ambiguity, not add a second field that can drift from the first.

## Consequences

- **Reproducibility (Supreme Law):** the field is a deterministic constant off
  every scoring path. Golden N=5 (seeds 0-4, 8/9 baselined roster per
  BEAAA-10199) delta = **0** on `are_total` and all four pillars —
  `tools/calibration-deltas/2026-07-05-v1.2.0-relock-vs-beaaa11930-locked-through.md`
  (Result: PASS). The four reproducibility stamps are unchanged.
- **Contract:** additive minor, `are-scanner-api-v1` v1.9.0 → v1.10.0
  (`contracts/are-scanner-api-v1-v1.10.0-patch.md`). No field renamed/removed;
  pre-upgrade consumers read a byte-identical response for every prior key.
- **Approval gate:** Underwriter co-sign **required** before merge (contract
  change). **No Founder gate** — the field is a top-level envelope metadata
  field (sibling to `rubric_version`), not a response `evidence_refs` field; the
  Founder freeze on the evidence-ref set is untouched; pillar weights stay
  25/25/25/25.
- **Tamper resistance:** unaffected — the field is emitted output, not an
  input; a scanned agent cannot influence the pinned lock date.
- **Tests:** `tests/test_scoring.py::test_response_surfaces_rubric_locked_through`
  (ScoreResponse) and the envelope shape/value assertion in
  `tests/test_cli_runner_beaaa464.py::test_run_scan_matches_g1_expected`.

## Rollback

Tier-2 PR-revert. The one-line stamp and the doc set revert cleanly.
