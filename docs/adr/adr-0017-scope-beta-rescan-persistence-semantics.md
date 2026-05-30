---
adr_id: ADR-0017
title: Scope-β rescan persistence semantics — write-once forensic_bundle_uri + at-write-time snapshot_hash + prior_scan_refs[] audit chain
status: accepted
author: CTO (88e68019-1cb6-4c4f-83e1-9fd340fb6821)
originating_ticket: BEAAA-1000
build_ticket: BEAAA-1255
contract_version: are-scanner-api-v1@v1.1.0 → v1.2.0
cto_cosign_ref:
  - BEAAA-1243 HoUW binding co-sign (Head of UW & AI Risk, 2026-05-30)
  - BEAAA-1236 UW operational co-sign (Underwriter, 2026-05-30)
  - scope-beta-design rev 2 §8 Q3 (Claims Architect conditional sign-off, BEAAA-1103)
scanner_version_when_drafted: head at BEAAA-1260 kickoff (2026-05-30)
date: 2026-05-30
audience: Scanner Engineer (implementer), Underwriter (consumer), Claims Architect (consumer — audit-trail for §3 evidence collection), CTO (producer authority)
classification: rule-change (new API surface fields + contract version bump to v1.2.0)
self_audit_complete: yes (Scanner Engineer ratified 2026-05-30T12:28Z via BEAAA-1255 comment 6555f6d0)
carrier_rule: clean
glossary: clean (ARE / IFA canonical; no BMI/ARI/BEAI in body)
rollback_tier: 2 (drop prior_scan_refs[] from response schema; forensic_bundle_uri + snapshot_hash are write-once so rollback means stop writing them, not deleting existing records)
---

## 1. BLUF

`POST /scanner/v1/rescans` must produce a measured re-pull of the ARE score for the same `agent_id` at a new `run_timestamp`. For the result to serve as claims-grade evidence (claims protocol §3 evidence-collection, §4 coverage analysis, §9 post-mortem), three persistence guarantees must hold:

1. **`prior_scan_refs[]`** — each rescan response carries an ordered list of `{scan_id, run_timestamp, are_total, band}` tuples for every prior scan (bind-time and subsequent) of the same `agent_id`, enabling audit-trail linkage without a separate lookup.
2. **Write-once `forensic_bundle_uri`** — once the forensic bundle is written to cold storage, the URI is sealed. Any subsequent rescan creates a *new* bundle at a *new* URI; it does not overwrite the prior record.
3. **At-write-time `snapshot_hash`** — the SHA-256 of the scan result payload is computed and stored at the moment of write. The hash is immutable after that point. It is not recomputed on read.

## 2. Decision

### 2.1 `prior_scan_refs[]` — response field (additive, v1.2.0)

Add `prior_scan_refs` as an optional array to the `POST /scanner/v1/rescans` response. Field is populated for rescans; omitted (or empty) for initial `POST /scanner/v1/scans` responses to avoid breaking the v1.1.0 scan surface.

```jsonc
{
  // ... all existing v1.1.0 response fields unchanged ...
  "prior_scan_refs": [
    {
      "scan_id": "<uuid>",
      "run_timestamp": "<ISO-8601>",
      "are_total": 72,
      "band": "moderate"
    }
    // ... one entry per prior scan for this agent_id, ordered ascending by run_timestamp ...
  ]
}
```

**Semantics:**
- Each entry in `prior_scan_refs[]` corresponds to one prior scan of the same `agent_id` (both initial scans and earlier rescans).
- Ordered ascending by `run_timestamp`. The current rescan's result is NOT included in its own `prior_scan_refs[]`; the caller reconciles current + prior to form the full chain.
- `are_total` and `band` are the values as-committed at the time of the prior scan. They are not recomputed and are not affected by rubric updates after the fact.
- If no prior scans exist for this `agent_id` (i.e., this is the first call to `/rescans`), `prior_scan_refs` MAY be an empty array or omitted; callers MUST handle both.

**Why in-response rather than a separate `/history` endpoint?**

Claims protocol §3 requires the evidence-collection step to produce a complete linkable pack in a single operation during active claim handling. A separate lookup introduces a failure mode (link exists but history endpoint unavailable) that is unacceptable when a coverage decision has a ≤15 BD SLA. Embedding the chain in the rescan response makes the pack self-contained.

### 2.2 Write-once `forensic_bundle_uri`

The forensic bundle (the full scan payload written to cold storage for subrogation and audit purposes) is addressed by a URI that is sealed at write time.

**Rules:**
- The URI is assigned when the bundle is first committed to storage. It is written once and never updated.
- A subsequent rescan of the same `agent_id` produces a new bundle at a new URI. It does not overwrite the prior URI.
- The URI MUST be stable for the life of the policy plus the claims run-off period (current: policy term + 7 years per Underwriter retention rule).
- Cold storage implementation is at Scanner Engineer's discretion (S3-compatible, object-locked bucket preferred). The URI scheme must be dereferenceable by the Claims team without intermediary service.

**Why write-once?**

The forensic bundle is the evidence of record for any coverage dispute. If the URI resolves to a mutable object, a post-loss update could silently alter what the adjudicator sees. Write-once guarantees that the bundle the Claims Architect reads during §3 evidence collection is byte-identical to what the scanner committed at scan time.

### 2.3 At-write-time `snapshot_hash`

The SHA-256 hash of the canonicalized scan result payload is computed immediately before the bundle is committed to cold storage, and is stored alongside it.

**Rules:**
- Hash input: the canonical JSON serialization of the full scan result (same shape as the API response body, keys sorted, no trailing whitespace).
- Hash is computed once, at write time.
- Hash is NOT recomputed on read. If the stored hash does not match the computed hash of the retrieved bundle, the bundle MUST be treated as tampered and MUST NOT be used as evidence.
- The `snapshot_hash` (hex-encoded SHA-256) is returned in the rescan response alongside `forensic_bundle_uri` so the caller can verify immediately.

```jsonc
{
  // ... all other response fields ...
  "forensic_bundle_uri": "s3://ifa-forensic-bundles/agent-<id>/scan-<uuid>.json",
  "snapshot_hash": "sha256:<64-hex-chars>"
}
```

**Why at-write-time rather than on-demand?**

On-demand hashing is vulnerable to storage-side mutation between write and hash. At-write-time hashing captures the exact payload committed, and the stored hash becomes a tamper-evident seal. This satisfies Claims Architect §conditional sign-off requirement (BEAAA-1103) that rescan evidence be independently verifiable without re-running the scan.

## 3. Reasoning

**Why these three guarantees together?**

`prior_scan_refs[]` alone enables linkage but does not authenticate the prior records. `snapshot_hash` alone authenticates individual records but does not prove the chain is complete. `forensic_bundle_uri` with write-once semantics alone provides immutable storage but gives no cross-record traceability. All three must hold simultaneously for a rescan to satisfy the claims-grade evidence standard.

**Contract version bump to v1.2.0:**

`prior_scan_refs[]`, `forensic_bundle_uri`, and `snapshot_hash` are additive new response fields on the rescan route. The scan route (`POST /scanner/v1/scans`) response shape is unchanged. This is a backwards-compatible additive minor bump per the semver rule established in ADR-0010. The v1.1.0 scan consumer receives no new required fields; it may ignore the new rescan fields without error.

CTO + UW co-sign is the approval gate (per `are-scanner-api-v1.md §Approval gates`). No Founder gate: no pillar weights or rubric points changed, and tamper-resistance is strengthened not weakened.

## 4. Rejected alternatives

- **Separate `/audit-trail/{agent_id}` endpoint.** Rejected — introduces single point of failure during claim handling; also creates a second API surface to maintain. Embedding in the rescan response is self-contained (§2.1 rationale).
- **Mutable bundle with version counter.** Rejected — mutable forensic records are inadmissible in coverage disputes. Write-once with a new URI per rescan is the only model that guarantees the bundle at any given URI is the exact payload committed at scan time.
- **On-demand hash (recomputed at read).** Rejected — at-write-time hash is the tamper-evident seal; on-demand recomputation loses the ability to detect storage-side mutation (§2.3 rationale).
- **SHA-1 or MD5 hash.** Rejected — SHA-256 is the minimum for collision-resistant evidence. Claims disputes may occur years after the scan; hash function longevity matters.
- **Bundle URI in a side table rather than in the response.** Rejected — claims-protocol §3 requires a self-contained evidence pack. A side-table lookup is a network dependency during active adjudication.

## 5. Hard Invariants — verification checklist (for Scanner Engineer at ratification)

| Invariant | Check |
|---|---|
| `prior_scan_refs[]` ordered ascending by `run_timestamp` | Unit test: two rescans in reverse submission order; assert ascending order in response |
| `forensic_bundle_uri` not overwritten on subsequent rescan | Integration test: two rescans for same `agent_id`; assert distinct URIs |
| `snapshot_hash` computed before write, stored immutably | Unit test: mutate bundle after write; assert stored hash ≠ recomputed hash of mutated payload |
| Hash algorithm is SHA-256 | Assert hash prefix `sha256:` and 64-char hex body in all rescan responses |
| `prior_scan_refs[]` entries carry committed `are_total`/`band`, not recomputed | Golden-set test: rescan after rubric update; assert prior entries unchanged |

## 6. Rollback

Tier 2. Stop writing `prior_scan_refs[]`, `forensic_bundle_uri`, `snapshot_hash` to new rescan responses (contract reverts to v1.1.0 rescan behavior). Existing committed bundles remain in cold storage; their URIs remain valid. No bundle deletion required.

## 7. Open questions — RESOLVED (Scanner Engineer ratification 2026-05-30, BEAAA-1255 comment 6555f6d0)

**Q7a — Cold storage implementation (resolved):**
S3-compatible bucket `ifa-forensic-bundles` with S3 Object Lock in **Compliance mode**. Retention policy: `run_timestamp + 10y` (7y run-off + 3y max policy term buffer), set via `object-lock-retain-until-date` header at write time. URI scheme: `s3://ifa-forensic-bundles/agent-{agent_id}/scan-{scan_id}.json` — path-stable, dereferenceable by Claims with IAM read-only role, no scanner intermediary. Scanner role policy: `s3:PutObject` + `s3:PutObjectRetention` only; no `s3:DeleteObject`. Integration test: second PUT to same URI returns `AccessDenied`.

**Q7b — `prior_scan_refs[]` pagination (resolved):**
Cap N = 100 entries (ascending `run_timestamp`). If agent has >100 prior scans: trim oldest, include `prior_scan_refs_truncated: true` + `prior_scan_refs_history_uri: "/scanner/v1/scan-history/{agent_id}"` (stub returning 501 in v1.2.0; implemented in v1.3.0). Acceptance criterion: unit test with 101 synthetic records asserts array length = 100, `prior_scan_refs_truncated: true`, `prior_scan_refs_history_uri` present.

**Q7c — Canonical-JSON serialization spec (resolved):**
Standard: RFC 8785 JCS (JSON Canonicalization Scheme). Key ordering: Unicode code-point order (JCS §3.2.3). Null handling: `null` values serialized as `null`; absent keys absent (distinct cases). Float precision: JCS §3.2.2.3 (shortest round-trip; e.g. `0.87` not `0.8700`). Encoding: UTF-8, no BOM. Hash input: full canonical JSON of response body **excluding** `snapshot_hash` field itself. Algorithm: SHA-256, hex-encoded, prefixed `sha256:`. Fixture committed at `tests/fixtures/canonical_serialization/` at WS7 close:
- Input: `{"seed": 42, "band": "moderate", "are_total": 72, "confidence": 0.87, "run_timestamp": "2026-05-30T12:00:00Z", "scanner_version": "0.3.0"}`
- JCS canonical form: `{"are_total":72,"band":"moderate","confidence":0.87,"run_timestamp":"2026-05-30T12:00:00Z","scanner_version":"0.3.0","seed":42}`
- SHA-256: to be committed alongside fixture; CI verifies on every run.

## 8. Co-sign record

| Principal | Role | Issue | Date |
|---|---|---|---|
| HoUW (93e0b62b) | Binding strategic co-sign | BEAAA-1243 | 2026-05-30 |
| Underwriter (afa65747) | Operational co-sign | BEAAA-1236 | 2026-05-30 |
| Claims Architect (04bf9956) | Conditional sign-off | BEAAA-1103 | prior session |
| Scanner Engineer (d8bbc3b5) | Implementer ratification | [BEAAA-1255](/BEAAA/issues/BEAAA-1255) comment 6555f6d0 | 2026-05-30 |
| CTO (88e68019) | Producer authority acceptance | [BEAAA-1255](/BEAAA/issues/BEAAA-1255) | 2026-05-30 |
