---
artifact_type: adr_contract
owner: cto
persona_audience: internal
phase: 1
canonical_terms_only: true
brand_backbone_applicable: false   # internal contract patch, not an external-bound asset
focus_claim: "v1.10.0 contract patch (are-scanner-api-v1 v1.9.0 -> v1.10.0) documents ONE additive, response-side field `rubric_locked_through` on the scan-output envelope so a downstream reader (Underwriter / auditor) can verify a scan ran under a NON-EXPIRED rubric lock from the score alone — without re-reading rubric/rubric.yaml. It is a deterministic ISO date lifted from the pinned rubric lock horizon (Rubric.locked_through), sibling to the existing rubric_version stamp; it does NOT feed scoring, does NOT change are_total, pillar weights, evidence_refs, or bands."
contracts_referenced: ["are-scanner-api-v1@v1.9.0 (shipped) -> v1.10.0 (proposed additive minor)", "scanner-implementation-supervision-v1@v1.0", "uw-rubric-supervision-v1@v1.0"]
ticket_refs: ["BEAAA-11930", "BEAAA-11915", "BEAAA-10199"]
proof_points_cited:
  - "rubric/scoring.py: response dict now stamps `rubric_locked_through: rubric.locked_through` immediately after `rubric_version: rubric.version`. Single source of truth = the pinned `locked_through` in rubric/rubric.yaml (2026-08-04 under the current 1.2.0 re-lock, BEAAA-11915)."
  - "Flow-through: harness/contract_envelope.to_contract_output builds `contract_output = dict(score_result)` then .update()s named keys only — the new field is carried into the CLI/customer envelope with no envelope-side edit; api/scan_runtime_factory flag-on path does `envelope = dict(scorer_envelope)`, so the API score envelope carries it too."
  - "Reproducibility: golden N=5 (seeds 0-4, 8/9 baselined roster per BEAAA-10199) Δ0 on are_total AND all four pillars — tools/calibration-deltas/2026-07-05-v1.2.0-relock-vs-beaaa11930-locked-through.md (Result: PASS). The field is a constant off every scoring path; delta is 0 by construction."
  - "Tests: tests/test_scoring.py::test_response_surfaces_rubric_locked_through (ScoreResponse) + tests/test_cli_runner_beaaa464.py::test_run_scan_matches_g1_expected (envelope shape + value vs Rubric.load(RUBRIC_PATH).locked_through)."
version: "v1.10.0"
prior_version: "are-scanner-api-v1@v1.9.0"
self_audit_passed: true
self_audit_note: "Pre-submission self-audit. (1) Focus claim cites the exact stamp site (rubric/scoring.py), the flow-through mechanism (dict(score_result) in to_contract_output + dict(scorer_envelope) in the API factory), the N=5 Δ0 gate, and the two regression tests. (2) Carrier Rule clean — no carrier or customer named. (3) Canonical terms only — 'the Scanner' / 'scan-output envelope' / 'rubric lock'; no BMI / ARI / ARE-as-product / BEAI / IFA in prose (are_total / rubric_version / rubric_locked_through are literal identifiers, not prose). (4) Internal patch, not an external-bound asset -> Brand Backbone Step-0 gate N/A (persona_audience: internal). (5) Supreme Law / reproducibility: the four reproducibility fields (scanner_version, corpus_version, model_versions, seed) are UNCHANGED and remain required; rubric_locked_through is ADDITIVE and deterministic; golden N=5 Δ0 verified. NO scoring math, NO rubric dim/weight/band/curve change. (6) Tamper resistance: unaffected — the field is emitted output, not an input; a scanned agent cannot influence the pinned lock date. (7) NO pillar-weight / rubric-weight / scoring-math / judge change; pillar weights stay 25/25/25/25 Founder-locked. Founder freeze is on the response EVIDENCE-REF set — rubric_locked_through is a TOP-LEVEL envelope metadata field (sibling to rubric_version), NOT an evidence_refs field, so the evidence-ref freeze is not touched -> NO Founder gate. (8) Co-sign: this IS a response/output-shape contract change to are-scanner-api-v1, so per the ticket governance it requires Underwriter co-sign before merge; this patch is DRAFT and does NOT self-merge. (9) Rollback: Tier-2 PR-revert; the one-line stamp and this doc revert cleanly, and a consumer that ignores the field reads a byte-identical v1.9.0 response for all pre-existing keys."
escalations_noted:
  - "Version selection — v1.10.0 (v1.9.0 + 1 minor). Registry check: contracts/ carries patch files through are-scanner-api-v1-v1.9.0-patch.md; this is the next unused minor. Bumped exactly once. CONTRACT_VERSION literal in scanners/__init__.py stays are-scanner-api-v1@v1.3.0 (pinned by tests/test_verdict_envelope_beaaa796.py:61; additive minors since v1.5.0 are documented via patch files, not the literal) — unchanged here."
  - "Underwriter co-sign REQUIRED (response/output-shape contract change). Gated on the BEAAA-11930 UW co-sign before merge to canonical master."
  - "NO Founder gate: rubric_locked_through is a top-level envelope metadata field (sibling to rubric_version), NOT a response evidence_refs field; the Founder freeze on the evidence-ref set is untouched; pillar weights 25/25/25/25 UNCHANGED (Founder-locked 2026-05-12); are_total computation UNCHANGED."
---

# Contract patch — `are-scanner-api-v1.md` v1.9.0 -> v1.10.0

**Status:** DRAFT authored + CTO-reviewed (agent `88e68019`) under [BEAAA-11930](/BEAAA/issues/BEAAA-11930), a hardening follow-up to the WI-1 rubric re-lock [BEAAA-11915](/BEAAA/issues/BEAAA-11915). **Response/output-shape change ⇒ Underwriter co-sign required before merge** (per the ticket governance). Lands on canonical master once the UW co-sign is recorded AND CI (`golden scanner regression`, `python pytest`) is green.

**Design authority:** CTO (contract design). Implementation authority: CTO (`rubric/scoring.py`). Approval gate: Underwriter (contract change).

**Change classification:** additive minor. ONE new response field. No existing field renamed, removed, or reshaped. A consumer that ignores the new field reads a **byte-identical v1.9.0 response** for every pre-existing key. Tier-2 PR-revert rollback.

## §0. Why now — lock currency is not self-describing in the envelope today

Scan outputs already stamp `rubric_version: "1.2.0"` (via `rubric/scoring.py` → `harness/contract_envelope.to_contract_output`), which traces to the pinned lock in `rubric/rubric.yaml`. But the envelope does **not** carry the lock *horizon*, so a downstream reader (Underwriter / auditor) cannot verify a scan ran under a **non-expired** lock without re-reading the rubric file — a file the reader may not have, and whose lapse is exactly the WI-1 (BEAAA-11915) failure mode (a lock window silently expiring). This patch makes lock currency self-describing in every score.

## §1. New field — `rubric_locked_through` (additive, response-side)

Emitted on the scan-output envelope alongside `rubric_version`:

```jsonc
{
  // ... existing envelope ...
  "rubric_version": "1.2.0",
  "rubric_locked_through": "2026-08-04",   // NEW — pinned rubric lock horizon (ISO 8601 date)
  "run_timestamp": "2026-07-05T00:00:00+00:00",
  // ...
}
```

- **Type:** ISO 8601 date string (`YYYY-MM-DD`).
- **Source of truth:** the pinned `locked_through` in `rubric/rubric.yaml`, loaded into `Rubric.locked_through` and stamped verbatim. No transformation.
- **Semantics:** the last calendar date the pinned rubric lock is in force. A scan is under a current lock iff `run_timestamp.date() <= rubric_locked_through`.

## §2. Why a raw date and NOT a precomputed `rubric_lock_current` boolean

The ticket floated an optional computed `rubric_lock_current` boolean. It was **deliberately not shipped.** `run_timestamp` is **finalized at the API edge**: `api/scan_runtime_factory` overwrites the Scorer-side timestamp with the kickoff-finalize stamp (so the envelope timestamp matches the `scan_log` row). A boolean computed at score time could therefore **contradict** the emitted `run_timestamp` on the lock-expiry day. Surfacing the raw lock horizon and letting the reader compare it against the authoritative emitted `run_timestamp` is *always self-consistent* — the correct primitive for a hardening ticket whose whole purpose is to remove ambiguity. Readers derive currency in one line:

```python
rubric_lock_current = run_timestamp_date <= rubric_locked_through
```

## §3. Reproducibility fields — intact

The four Supreme-Law reproducibility stamps (`scanner_version`, `corpus_version`, `model_versions`, `seed`) are **unchanged and still required**. `rubric_locked_through` is a deterministic constant read from the pinned rubric; it is off every scoring path. Golden N=5 (seeds 0-4, 8/9 baselined roster) delta = **0** on `are_total` and all four pillars — `tools/calibration-deltas/2026-07-05-v1.2.0-relock-vs-beaaa11930-locked-through.md`.

## §4. Approval-gate disposition

- **Underwriter co-sign: REQUIRED** — this is a response/output-shape change to `are-scanner-api-v1`. Gated on the BEAAA-11930 UW co-sign before merge.
- **Founder gate: NOT required** — `rubric_locked_through` is a top-level envelope metadata field (sibling to `rubric_version`), **not** a response `evidence_refs` field; the Founder freeze on the evidence-ref set is untouched. Pillar weights stay 25/25/25/25 (Founder-locked 2026-05-12); `are_total` computation is unchanged.
- **CONTRACT_VERSION literal:** unchanged (`are-scanner-api-v1@v1.3.0`, pinned by `tests/test_verdict_envelope_beaaa796.py:61`). Additive minors since v1.5.0 are documented via patch files, not the runtime literal.

## §5. Rollback

Tier-2 PR-revert. The one-line stamp in `rubric/scoring.py` and this patch revert cleanly. Because the change is purely additive, a consumer that has not yet upgraded reads a byte-identical v1.9.0 response for all pre-existing keys until the revert lands.
