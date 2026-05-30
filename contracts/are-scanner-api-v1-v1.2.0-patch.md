---
artifact_type: other
owner: underwriter
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "v1.2.0 contract bump adds prior_scan_refs[] + forensic_bundle_uri + snapshot_hash to POST /scanner/v1/rescans, enabling claims-grade audit-trail linkage across bind-time scan and all subsequent rescans."
contracts_referenced: ["are-scanner-api-v1@v1.1.0 → v1.2.0", "scanner-implementation-supervision-v1@v1.0", "uw-rubric-supervision-v1@v1.0"]
ticket_refs: ["BEAAA-1243", "BEAAA-1236", "BEAAA-1103", "BEAAA-1000", "BEAAA-1255"]
proof_points_cited: ["ADR-0017 (accepted 2026-05-30, docs/adr/adr-0017-scope-beta-rescan-persistence-semantics.md)", "HoUW binding co-sign BEAAA-1243 2026-05-30", "UW op-seat co-sign BEAAA-1236 2026-05-30", "Claims Architect sign-off BEAAA-1103", "Scanner Engineer ratification BEAAA-1255 comment 6555f6d0 2026-05-30", "CTO acceptance BEAAA-1255 2026-05-30"]
version: "v1.2.0-patch"
prior_version: "are-scanner-api-v1@v1.1.0"
self_audit_passed: true
self_audit_note: "ADR-0017 accepted 2026-05-30. Scanner Engineer ratified §7 Q7a/Q7b/Q7c (BEAAA-1255 comment 6555f6d0). CTO co-sign complete. Approved for merge; WS5 N=5 golden-set gate is the sole remaining merge blocker."
escalations_noted: ["No Founder gate: pillar weights / rubric points / bound-book pricing unchanged. CTO + UW co-sign authority sufficient (additive minor; no tamper-resistance regression — persistence guarantees strengthen evidence chain)."]
---

# Contract patch — `are-scanner-api-v1.md` v1.1.0 → v1.2.0

**Status:** approved for merge — ADR-0017 accepted 2026-05-30 (Scanner Engineer ratification BEAAA-1255 comment 6555f6d0; CTO co-sign 2026-05-30). Sole remaining merge gate: WS5 N=5 golden-set calibration delta (BEAAA-1277).
**Op-seat co-sign:** [BEAAA-1236](/BEAAA/issues/BEAAA-1236) `done` (Underwriter operational, 2026-05-30).
**Strategic-seat countersign:** [BEAAA-1243](/BEAAA/issues/BEAAA-1243) `done` (Head of UW & AI Risk, 2026-05-30).
**Claims Architect sign-off:** [BEAAA-1103](/BEAAA/issues/BEAAA-1103) `done` (conditional on ADR-0017 §Persistence-semantics).
**CTO authority:** ADR-0017 `proposed` at `projects/are-scanner/docs/adr/adr-0017-scope-beta-rescan-persistence-semantics.md`. Ratification at build close (BEAAA-1255).
**Build implementer:** Scanner Engineer on [BEAAA-1255](/BEAAA/issues/BEAAA-1255).
**Change classification:** additive minor; Tier 2 rollback (stop writing new fields; existing committed bundles remain valid). Existing v1.1.0 consumers unaffected — `/scanner/v1/scans` response is byte-identical.

---

## §Version header (replaces current line 1)

```
# Contract: ARE Scanner API — v1.2.0
```

## §New route — `POST /scanner/v1/rescans` (Scope-β)

This contract bump activates the Scope-β route. Prior to v1.2.0, `POST /scanner/v1/rescans` existed as a doc-only stub. From v1.2.0 onward, it is a live measurement endpoint.

### Input (same shape as `POST /scanner/v1/scans`)

`POST /scanner/v1/rescans` accepts the identical request schema as `POST /scanner/v1/scans`, plus one required field:

```jsonc
{
  // ... all existing /scans input fields, with same semantics ...
  "agent_id": "<uuid>",   // REQUIRED for rescans — identifies the agent being rescanned; must match an existing scan record
  "scan_mode": "offline_replay | live_probe",   // optional; default "offline_replay"
  "k_replicates": 5 | 10                         // optional; default 5; meaningful only for live_probe
}
```

**`agent_id` is required** on the rescans route (not required on the scans route where it may be a new agent). The rescan MUST fail with 422 `agent_id_not_found` if no prior scan exists for the given `agent_id`.

### Output (additive beyond `POST /scanner/v1/scans`)

`POST /scanner/v1/rescans` returns the same response schema as `POST /scanner/v1/scans` (all fields identical in semantics) plus three additional fields:

```jsonc
{
  // ... all existing /scans output fields, with same semantics (are_total, subscores, band, confidence,
  //     evidence_refs[], uncovered_dimensions, scanner_version, corpus_version, model_versions,
  //     seed, run_timestamp, scan_mode, spread_per_dim, spread_per_pillar, spread_total,
  //     live_probe_variance_band, k_replicates_observed, incomplete) ...

  // Scope-β additions (v1.2.0, rescans route only):
  "prior_scan_refs": [                            // ordered list of all prior scans for this agent_id
    {
      "scan_id": "<uuid>",
      "run_timestamp": "<ISO-8601>",
      "are_total": 0..100,
      "band": "low | moderate | high | very_high"
    }
    // ... one entry per prior scan (initial scan + all prior rescans), ascending by run_timestamp ...
    // The current rescan is NOT included in prior_scan_refs[]; caller reconciles current + prior.
  ],
  "forensic_bundle_uri": "<URI>",                 // write-once URI to the cold-storage forensic bundle for this rescan
  "snapshot_hash": "sha256:<64-hex-chars>"        // SHA-256 of the canonical JSON payload, computed at write time
}
```

## §Persistence semantics — `forensic_bundle_uri` + `snapshot_hash` (NEW section)

> **Authoritative ADR:** ADR-0017 (`docs/adr/adr-0017-scope-beta-rescan-persistence-semantics.md`).

**Write-once `forensic_bundle_uri`.** Once the forensic bundle for a rescan is written to cold storage, its URI is sealed. A subsequent rescan of the same `agent_id` produces a new bundle at a new URI; it does not overwrite the prior record. The URI MUST be stable for the life of the policy plus the claims run-off period (current: policy term + 7 years).

**At-write-time `snapshot_hash`.** The SHA-256 hash of the canonical JSON serialization of the full rescan result is computed immediately before the bundle is committed to cold storage. The hash is NOT recomputed on read. If a retrieved bundle's hash does not match the stored `snapshot_hash`, the bundle MUST be treated as tampered and MUST NOT be used as claims evidence.

**Tamper-resistance relationship.** These guarantees strengthen CTO Hard Guarantee #5 (tamper resistance) for the rescan evidence path. The scanner's refusal to overwrite prior bundles and the at-write-time seal mean that no post-loss mutation of the forensic record is possible via the scanner's own API surface.

## §Audit-trail linkage — `prior_scan_refs[]` (NEW section)

> **Claims protocol dependency:** §3 evidence-collection, §4 coverage analysis, §9 post-mortem.

`prior_scan_refs[]` makes the rescan response self-contained for adjudication: the claims handler can construct the complete ARE evidence chain (bind-time scan + all rescans) from a single rescan response, without a second API call or a history lookup.

**Entries are committed values.** `are_total` and `band` in each `prior_scan_refs[]` entry are the values as committed at the time of the prior scan. They are not recomputed and are not affected by rubric updates after the fact. This preserves the integrity of the evidence chain for §4 bind-time vs. at-loss state comparison.

**Pagination.** If `prior_scan_refs[]` would exceed N entries (N to be pinned at build close in ADR-0017 §7), the response MUST include a `prior_scan_refs_truncated: true` flag and a `prior_scan_refs_history_uri` pointing to the full history endpoint. Callers MUST check `prior_scan_refs_truncated` before treating the array as complete. (For typical policy lifetimes before first real Tier-2+ claim, N will not be reached.)

## §Hard guarantees (CTO) — Scope-β addendum

Append to clause #1 (reproducibility):

> **1c. Reproducibility (rescans).** `POST /scanner/v1/rescans` with `scan_mode: "offline_replay"` satisfies the same ±1 reproducibility guarantee as `POST /scanner/v1/scans` (clause 1a). `scan_mode: "live_probe"` satisfies the same ±2 band guarantee (clause 1b). The rescan result is NOT required to equal the bind-time scan result — bind-to-loss drift is expected and is the measurement we are trying to capture.

Append to clause #5 (tamper resistance):

> **5a. Forensic bundle tamper evidence.** The `snapshot_hash` returned in every rescan response is the tamper-evident seal on the committed forensic bundle. Any party in possession of the bundle and the hash can independently verify integrity. The scanner MUST NOT provide any API surface to recompute or overwrite the hash for an existing bundle.

## §Approval gates — unchanged

This v1.2.0 bump is classified under "Scoring logic change (CTO + Underwriter)" (additive surface; no pillar weights or rubric points changed). CTO + Underwriter co-sign authority is sufficient; no Founder sign-off triggered.

## §Versioning — bump

Update the standing version pointer. Existing v1.1.0 consumers that call `POST /scanner/v1/scans` receive byte-identical responses. `POST /scanner/v1/rescans` callers upgrade to v1.2.0 when they are ready to consume the three new fields. `prior_scan_refs_truncated` defaults to `false` (absent) when the array is not truncated; callers MUST treat absent and `false` identically.

---

## Build-spec implementation flags (NON-contract — for BEAAA-1255)

1. **Cold-storage choice** (ADR-0017 §7 open question 1): S3-compatible object-locked bucket preferred. URI scheme must be dereferenceable by Claims team without scanner intermediary. Pin implementation choice and confirm in ADR-0017 ratification comment on BEAAA-1255.
2. **`prior_scan_refs[]` pagination cap N** (ADR-0017 §7 open question 2): propose a cap (e.g., 50 entries) or confirm no cap needed for v1.2.0 with a v1.3.0 follow-on. Pin in ADR-0017 ratification.
3. **Canonical-JSON serialization spec** (ADR-0017 §7 open question 3): key sort order, null handling, float precision must be pinned. Provide a canonical-serialization fixture in the test suite. Pin in ADR-0017 ratification.
4. **`pre_bind`/`mid_term` SLA**: ≤4 hr p95 for rescan completion. Document measurement methodology in BEAAA-1255. `claim_review` degradation: prefer SLA extension up to 12 hr; partial fallback gated behind explicit flags + manual routing (per scope-beta-design rev 2 §2.2).
5. **Smoke target**: Syllable apichat (BEAAA-585 target). Two timestamped rescans returning linkable, measured pack rows within SLA. Both `prior_scan_refs[]` from rescan #2 must include rescan #1 as an entry.

## Co-sign reference manifest (for BEAAA-1255 build spec)

- `cto_cosign_ref`: ADR-0017 accepted 2026-05-30 ([BEAAA-1255](/BEAAA/issues/BEAAA-1255)), Scanner Engineer ratification comment 6555f6d0, CTO co-sign complete.
- `uw_operational_cosign_ref`: [BEAAA-1236](/BEAAA/issues/BEAAA-1236) (Underwriter operational, 2026-05-30).
- `head_of_uw_cosign_ref`: [BEAAA-1243](/BEAAA/issues/BEAAA-1243) (Head of UW & AI Risk, 2026-05-30).
- `claims_architect_cosign_ref`: [BEAAA-1103](/BEAAA/issues/BEAAA-1103) (Claims Architect, conditional on ADR-0017 §Persistence-semantics).

## Rollback

Tier 2. Stop writing `prior_scan_refs[]`, `forensic_bundle_uri`, `snapshot_hash`, `prior_scan_refs_truncated`, `prior_scan_refs_history_uri` to new rescan responses (contract reverts to v1.1.0 rescan behavior — which was doc-only). Existing committed bundles remain in cold storage; their URIs and hashes remain valid. No bundle deletion required or permitted (write-once guarantee).
