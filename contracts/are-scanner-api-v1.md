---
artifact_type: other
owner: underwriter
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "v1.3.0 contract bump activates two new rubric_point_ids on the /scanner/v1/scans response surface (and CLI output): ROBUSTNESS.PII.SCAN_TIME_DETECTION (substrate, LLM Guard Anonymize wrap) + GOVERNANCE.PII.HANDLING_ATTESTATION (attestation pair, evidence-graded). Minor-additive — envelope field-set extends via pillars[].dimensions[].id additions; no existing key renamed/removed; v1.2.0 envelope-content for prior keys remains byte-stable per scanner-implementation-supervision-v1@v1.0."
contracts_referenced: ["are-scanner-api-v1@v1.2.0 → v1.3.0", "scanner-implementation-supervision-v1@v1.0", "uw-rubric-supervision-v1@v1.0"]
ticket_refs: ["BEAAA-2649", "BEAAA-781", "BEAAA-2026", "BEAAA-2066", "BEAAA-2067", "BEAAA-2068", "BEAAA-1682"]
proof_points_cited: ["ADR-0022 (spec authority, docs/adr/adr-0022-beaaa2026-pii-rubric-spec-authorship.md)", "ADR-0025 (BEAAA-781 activation, docs/adr/adr-0025-beaaa781-pii-rubric-activation.md)", "CTO spec co-sign BEAAA-2026 comment 66728f27", "UW operational co-sign BEAAA-2026 comment e8f38143 + BEAAA-2066 done", "HoUW activation co-sign BEAAA-2026 comment ea35a75e + BEAAA-2067 done + _default/BEAAA-2067_houw_cosign_v0.1.md", "Founder authority BEAAA-2048 Q46=A via BEAAA-2064 (CEO comment 3a432710 on BEAAA-1858)", "Actuary C1/C2 co-sign BEAAA-781 comment 1be2c13b + BEAAA-2649 comment 3caa47ac", "HoUW WS-B atomic-merge binding BEAAA-2649 comment 185366c9", "CTO Path A ruling BEAAA-2649 comment 289f10fb"]
version: "v1.3.0"
prior_version: "are-scanner-api-v1@v1.2.0"
self_audit_passed: true
escalations_noted: ["No Founder gate: pillar weights 25/25/25/25 UNCHANGED (Founder-locked 2026-05-12 per BEAAA-21 v1.0 §pillars); bound-book pricing UNCHANGED (new dims add to scoring sheet — no scoring-math change per ADR-0022 §2); tamper resistance preserved by construction (envelope content for v1.2.0 keys byte-stable; the new pillars[ROBUSTNESS|GOVERNANCE].dimensions[] additions are append-only). CTO spec + UW operational + HoUW activation co-sign authority sufficient per supervision contract row 'Add a new rubric_point_id to the canonical scoring sheet' (x2)."]
---

# Contract: ARE Scanner API — v1.3.0

**Status:** merged at activation gate. ADR-0022 spec authority accepted 2026-06-03 (CTO co-sign BEAAA-2026 comment 66728f27); ADR-0025 activation accepted 2026-06-05 (Scanner Engineer activation on BEAAA-781 via BEAAA-2649 atomic merge).
**Op-seat co-sign:** [BEAAA-1236](/BEAAA/issues/BEAAA-1236) `done` (Underwriter operational, 2026-05-30).
**Strategic-seat countersign:** [BEAAA-1243](/BEAAA/issues/BEAAA-1243) `done` (Head of UW & AI Risk, 2026-05-30; pre-grants Q1/Q2a/Q2b/Q3/Q4 without itemized variance).
**Claims Architect sign-off:** [BEAAA-1103](/BEAAA/issues/BEAAA-1103) `done` (conditional on ADR-0017 §Persistence-semantics — satisfied by §2.1–§2.3).
**CTO authority:** ADR-0017 `accepted` at `projects/are-scanner/docs/adr/adr-0017-scope-beta-rescan-persistence-semantics.md`.
**Build implementer:** Scanner Engineer on [BEAAA-1255](/BEAAA/issues/BEAAA-1255) umbrella (WS1 BEAAA-1265, WS2 BEAAA-1271, WS3 BEAAA-1273, WS4 BEAAA-1274, WS5 BEAAA-1277, WS6 BEAAA-1278, WS7 BEAAA-1283).
**Change classification:** rule-change (contract version bump + additive new fields on /rescans + measured-route activation); additive minor under semver; Tier 2 rollback.

---

## §Version header

```
# Contract: ARE Scanner API — v1.3.0
```

---

## §Input — `POST /scanner/v1/scans` (unchanged from v1.1.0)

The scan route input shape is byte-identical to v1.1.0. Recap:

```jsonc
{
  // ... identity, corpus snapshot, model versions, evidence pointers ...
  "scan_mode": "offline_replay | live_probe",   // optional; default "offline_replay" (v1.1.0)
  "k_replicates": 5 | 10                         // optional; default 5; meaningful only for live_probe (v1.1.0)
}
```

Existing v1.1.0 consumers that omit `scan_mode` continue to receive
`offline_replay`-shaped responses byte-identically.

## §Input — `POST /scanner/v1/rescans` (NEW measured route, v1.2.0)

`POST /scanner/v1/rescans` accepts the identical request schema as
`POST /scanner/v1/scans`, with two differences:

```jsonc
{
  // ... all /scans input fields, with same semantics ...
  "agent_id": "<uuid>",                         // REQUIRED on /rescans (vs. optional on /scans);
                                                 // must reference an existing scan record. 422 agent_id_no_prior_scan otherwise.
  "scanner_invocation_context": {
    "purpose": "pre_bind | mid_term | claim_review"   // REQUIRED on /rescans; drives SLA + degradation policy.
  },
  "scan_mode": "offline_replay | live_probe",   // optional; default "offline_replay"
  "k_replicates": 5 | 10                         // optional; default 5; meaningful only for live_probe
}
```

Rescans against a never-scanned `agent_id` MUST fail 422 with detail
`agent_id_no_prior_scan` before any scoring work begins. Rescans with an
unknown `purpose` MUST fail 422 with detail `purpose_invalid`.

## §Output — `POST /scanner/v1/scans` (additive: WS4 boolean flags)

v1.1.0 response shape is preserved field-for-field. v1.2.0 adds two
top-level boolean flags that default `false` on every scan response:

```jsonc
{
  // ... all existing v1.1.0 /scans response fields unchanged (are_total, subscores, band,
  //     confidence, evidence_refs[], uncovered_dimensions, scanner_version, corpus_version,
  //     model_versions, seed, run_timestamp, scan_mode, spread_per_dim, spread_per_pillar,
  //     spread_total, live_probe_variance_band, k_replicates_observed, incomplete) ...

  "partial_completion": false,                    // v1.2.0; true ⇒ result was partially produced under
                                                  // SLA degradation (see §SLA degradation policy)
  "confidence_below_adjudication_threshold": false  // v1.2.0; true ⇒ confidence is below the
                                                  // adjudication-grade floor and routes to manual review
}
```

The two flags are mode-independent — they appear on both `offline_replay`
and `live_probe` responses. They are pure-additive: v1.1.0 consumers that
ignore them receive the same data they did before. Wire-shape gate: both
fields ALWAYS present on `/scans` accept responses (default `false`).

## §Output — `POST /scanner/v1/rescans` (additive beyond `/scans`, v1.2.0)

`POST /scanner/v1/rescans` returns the same response schema as
`POST /scanner/v1/scans` (all fields identical in semantics, including the
two new v1.2.0 flags above) plus three Scope-β additive fields per
ADR-0017 §2.1 + §2.2 + §2.3:

```jsonc
{
  // ... all /scans output fields, with same semantics, including
  //     partial_completion + confidence_below_adjudication_threshold ...

  // Scope-β additions (v1.2.0, rescans route only):
  "prior_scan_refs": [                            // ordered list of all prior scans for this agent_id
    {                                             // (ascending by run_timestamp; current rescan NOT included)
      "scan_id": "<uuid>",
      "run_timestamp": "<ISO-8601>",
      "are_total": 0..100,
      "band": "A | B | C | D"                     // canonical 12-band letter (rubric/bands.py BEAAA-57 FROZEN 2026-04-30)
    }
    // ... one entry per prior scan (initial scan + all prior rescans), asc by run_timestamp ...
    // MAY be empty array on the first /rescans for an agent_id; callers MUST handle empty.
    // ALWAYS present on /rescans accept responses (empty array when chain is empty).
  ],
  "prior_scan_refs_truncated": true,              // OPTIONAL; present ONLY when chain exceeds the v1.2.0 cap (N=100)
  "forensic_bundle_uri": "<URI>",                 // write-once URI to the cold-storage forensic bundle for this rescan;
                                                  // present only when the runtime is wired with a scan_log (omitted on
                                                  // rollback / unwired paths — cannot be fabricated, per ADR-0017 §2.2)
  "snapshot_hash": "sha256:<64-hex-chars>"        // SHA-256 of the RFC 8785 JCS canonical form of the response body
                                                  // (minus `snapshot_hash` itself), computed at write time and stored
                                                  // immutably; present only with scan_log (same gate as forensic_bundle_uri)
}
```

The three Scope-β fields are absent on the `/scans` response shape (AC #3
of WS3 — `/scans` envelope is byte-identical to v1.1.0). Callers MUST
treat absent `prior_scan_refs_truncated` and absent `forensic_bundle_uri`
/ `snapshot_hash` identically to `false` / no-evidence-yet semantics
respectively.

## §Persistence semantics — `forensic_bundle_uri` + `snapshot_hash`

> **Authoritative ADR:** ADR-0017 (`docs/adr/adr-0017-scope-beta-rescan-persistence-semantics.md`), §2.2 + §2.3.

**Write-once `forensic_bundle_uri` (ADR-0017 §2.2).** Once the forensic
bundle for a rescan is written to cold storage, its URI is sealed. A
subsequent rescan of the same `agent_id` produces a new bundle at a new
URI; it does not overwrite the prior record. The URI MUST be stable for
the life of the policy plus the claims run-off period (policy term +
7 years). Cold-storage implementation (per §7 Q7a, pinned at WS2 close
BEAAA-1271): S3-compatible bucket `ifa-forensic-bundles` with Object Lock
in Compliance mode; retain-until = `run_timestamp + 10y`; scanner IAM
role carries `s3:PutObject` + `s3:PutObjectRetention` only (no
`s3:DeleteObject`, no `s3:BypassGovernanceRetention`).

**At-write-time `snapshot_hash` (ADR-0017 §2.3).** The SHA-256 hash of
the RFC 8785 JCS canonical form (see §Canonical-JSON serialization below)
of the full rescan response body — **excluding `snapshot_hash` itself**
— is computed immediately before the bundle is committed to cold
storage. The hash is NOT recomputed on read. If a retrieved bundle's
hash does not match the stored `snapshot_hash`, the bundle MUST be
treated as tampered and MUST NOT be used as claims evidence.

**Tamper-resistance relationship (CTO Hard Guarantee #5 strengthened).**
The scanner's refusal to overwrite prior bundles and the at-write-time
seal mean that no post-loss mutation of the forensic record is possible
via the scanner's own API surface.

## §Canonical-JSON serialization — `snapshot_hash` preimage (NEW, v1.2.0)

> **Authoritative ADR:** ADR-0017 §7 Q7c, ratified at BEAAA-1283 (WS7 close).
> **Pinned fixture:** `tests/fixtures/canonical_serialization/` (CI-gated by
> `tests/test_canonical_serialization_beaaa1283.py`).

- **Standard:** RFC 8785 (JSON Canonicalization Scheme — JCS).
- **Implementation:** `api/jcs.py:canonicalize()`.
- **Key ordering:** UTF-16 code-unit sequence (JCS §3.2.3).
- **Null handling:** present-with-`null` is serialized as `null`; absent
  keys are absent. These are distinct cases — the canonical form mirrors
  payload presence; it never injects or suppresses keys.
- **Float precision:** ECMAScript 6 ToString (JCS §3.2.2.3) — shortest
  round-trip representation; trailing zeros stripped; integer-valued
  floats emit without `.0`; non-finite floats rejected.
- **String encoding:** UTF-8, no BOM. Minimal-escape rule of JCS §3.2.2.5
  — the seven mandatory short escapes (`\"`, `\\`, `\b`, `\f`, `\n`,
  `\r`, `\t`) plus `\u00xx` for remaining C0 control codes; every other
  code point emitted as-is.
- **Hash input scope:** the complete canonical JSON byte string of the
  full rescan response body, **excluding** the `snapshot_hash` field
  itself. The hash is computed before `snapshot_hash` is inserted.
- **Hash algorithm:** SHA-256, hex-encoded, prefixed `sha256:` →
  `sha256:<64 lowercase hex chars>`.

Drift in any of the above (implementation, fixture, or hash algorithm)
invalidates the chain of `snapshot_hash` values committed to cold
storage. Any such change is itself a contract bump per ADR-0017 §2.3.

## §Audit-trail linkage — `prior_scan_refs[]`

> **Authoritative ADR:** ADR-0017 §2.1.
> **Claims protocol dependency:** §3 evidence-collection, §4 coverage analysis, §9 post-mortem.

`prior_scan_refs[]` makes the rescan response self-contained for
adjudication: the claims handler can construct the complete ARE evidence
chain (bind-time scan + all rescans) from a single rescan response,
without a second API call or a history lookup.

**Entries are committed values.** `are_total` and `band` in each entry
are the values as committed at the time of the prior scan. They are not
recomputed and are not affected by rubric updates after the fact. This
preserves the integrity of the evidence chain for §4 bind-time vs.
at-loss state comparison.

**Pagination — v1.2.0 resolution (ADR-0017 §7 Q7b, pinned at WS3 close BEAAA-1273).**
Max-entries cap is **N = 100** for v1.2.0. If a `/rescans` POST would
produce more than 100 `prior_scan_refs[]` entries, the response MUST
return the most-recent 100 entries (truncated from the head end — the
most-recent tail by `run_timestamp` is kept) AND set the non-breaking
sentinel field `prior_scan_refs_truncated: true`. Callers MUST check
`prior_scan_refs_truncated` before treating the array as complete; when
the flag is absent or `false`, the array is the complete chain.
Implementation constant: `api.scan_handlers.PRIOR_SCAN_REFS_MAX = 100`.
A separate `GET /scanner/v1/agents/{agent_id}/scans` history endpoint is
**deferred to v1.3.0**; v1.2.0 does NOT emit a
`prior_scan_refs_history_uri`. Rationale: at the expected first-bind-to-
claim cadence (≤1 rescan/quarter Tier-1, ≤1 rescan/month Tier-2), 100
entries covers 25y Tier-1 / 8y Tier-2 — well past the Underwriter
retention window. The persisted scan_log row count remains unbounded
(no row eviction); the cap is operational defense-in-depth, not a hot
path.

## §SLA degradation policy — purpose-aware (NEW, v1.2.0)

> **Authoritative source:** WS4 (BEAAA-1274) — `api/sla.py`.

Each `/rescans` invocation carries a `scanner_invocation_context.purpose`
in `{pre_bind, mid_term, claim_review}`. The route emits per-purpose
rolling-window wall-clock metrics (N=20 samples, nearest-rank p50/p95/p99)
observable at `GET /scanner/v1/sla`. Pinned SLA targets:

- **`pre_bind` / `mid_term` p95 ≤ 4 hours** wall-clock. Nominal path.
- **`claim_review` ceiling = 12 hours** wall-clock. SLA extension up to
  12 hours is preferred over partial fallback.

**Degradation behavior.** When a `claim_review` rescan exceeds the
12-hour ceiling with an incomplete result (cause ∈ `{timeout,
rate_limited, transport_saturated}`):

- `partial_completion` MUST be set `true`.
- `confidence_below_adjudication_threshold` MUST be set `true`.
- `uncovered_dimensions[].reason` MUST be populated.
- The `claim_review.partial_fallback` routing event MUST be emitted via
  `RoutingEventSink`, hooking the Claims Architect manual-review pipeline.

Both flags default `false` on every accept response. Setting them
`true` is the only signal callers should rely on; their absence MUST be
treated as `false`.

## §New endpoint — `GET /scanner/v1/sla`

Returns the per-`purpose` rolling-window snapshot:

```jsonc
{
  "pre_bind":     { "p50_s": 1234, "p95_s": 3456, "p99_s": 4567, "n": 20 },
  "mid_term":     { "p50_s": 2345, "p95_s": 4567, "p99_s": 7890, "n": 20 },
  "claim_review": { "p50_s": 5678, "p95_s": 9012, "p99_s": 11456, "n": 20 }
}
```

Returns `{}` before the first `/rescans` invocation populates any
metric. No authz changes beyond the existing `/scanner/v1/*` surface.

## §Hard guarantees (CTO is on the hook for these)

Recap of v1.1.0 clauses 1a + 1b unchanged. v1.2.0 appends:

> **1c. Reproducibility (rescans).** `POST /scanner/v1/rescans` with
> `scan_mode: "offline_replay"` satisfies the same ±1 reproducibility
> guarantee as `POST /scanner/v1/scans` (clause 1a). `scan_mode:
> "live_probe"` satisfies the same ±2 band guarantee (clause 1b). The
> rescan result is NOT required to equal the bind-time scan result —
> bind-to-loss drift is expected and is the measurement we are trying to
> capture. Enforced in CI: every release runs the N=5 calibration delta
> on the golden set via `/rescans` vs. `/scans` baseline (BEAAA-1278);
> release blocks if `are_total` or any subscore varies by more than
> 1 point across replicates.

Clause #5 (tamper resistance) gains a 5a addendum:

> **5a. Forensic bundle tamper evidence.** The `snapshot_hash` returned
> in every rescan response is the tamper-evident seal on the committed
> forensic bundle. Any party in possession of the bundle and the hash
> can independently verify integrity by re-canonicalizing the bundle
> bytes per §Canonical-JSON serialization and recomputing SHA-256. The
> scanner MUST NOT provide any API surface to recompute or overwrite
> the hash for an existing bundle. Cold-storage Object Lock (Compliance
> mode) is the second seal at the storage layer.

Clauses #2–#4 unchanged, with one acceptance-timing clarification:

> **Clause #3 acceptance timing (ADR-0035, BEAAA-5325).** Clause #3
> (scan envelope `model_versions` populated) is a **v1 real-adapter
> wrap-ticket** acceptance criterion, **not** a v0 production acceptance
> criterion. The v0 synthetic probe path
> (`synthetic_v0_probe_run_fn` + `v0_mock_runner_factory`) invokes no real
> model, so `model_versions` MAY be an empty list `[]` in v0 envelopes;
> v0 reproducibility is carried by the remaining determinism-tuple fields
> (`scanner_version`, `corpus_version`, `seed`). When a real adapter
> replaces `v0_mock_runner_factory`, `model_versions` MUST be populated
> and Clause #3 becomes a hard release gate again. Founder-ratified
> 2026-06-21 (BEAAA-7229 interaction `d151516e`); no wire-shape change.

## §Hard guarantees (Underwriter is on the hook for these)

Recap of v1.1.0 clauses #1–#4 unchanged. They bind both `/scans` and
`/rescans` identically (UW #1 routes via `partial_completion: true` ⇒
no pricing, UW #2 routes via `uncovered_dimensions`, UW #3 unchanged for
offline_replay and rate-book extension applies to live_probe spread, UW
#4 mid-term re-scan triggers are satisfied by the measured `/rescans`
route this contract activates).

## §Customer-facing adapter rule (unchanged from v1.1.0)

When emitting a customer-visible report card, the adapter MUST strip the
`live_probe_variance_band` field (UW-facing only, per Δ4
tamper-resistance hardening). v1.2.0 ADDS the rule that the customer-
facing adapter MUST also strip `forensic_bundle_uri` and `snapshot_hash`
(both are claims-grade evidence-pipeline references — exposing them to
the scanned agent gives it tampering targets and degrades CTO Hard
Guarantee #5a). `prior_scan_refs[]` MAY appear in customer-facing output
at customer-success discretion; `prior_scan_refs_truncated`,
`partial_completion`, and `confidence_below_adjudication_threshold` MAY
appear in customer-facing output.

## §Approval gates

| Change class | Authority required |
|---|---|
| Rubric point edit (text / weight / dimension) | CTO + Underwriter (uw-rubric-supervision-v1) |
| Pillar weight change | CTO + Underwriter + Founder |
| Judge-model substitution | CTO + Underwriter |
| Scoring math change | CTO + Underwriter |
| Contract additive minor (this v1.2.0 bump pattern) | CTO + Underwriter; HoUW countersign on /scanner/v1/* persistence semantics. Authoritative source for v1.2.0: ADR-0017. |
| Contract breaking change (major) | CTO + Underwriter + Founder |
| Tamper-resistance reduction | CTO + Underwriter + Founder |

This v1.2.0 bump itself is classified as "Contract additive minor" with
the HoUW countersign on persistence semantics: CTO + Underwriter +
HoUW + Claims Architect signed; no Founder sign-off triggered
(pillar weights / rubric points unchanged; tamper resistance strengthened
not weakened — ADR-0017 §3 + §5a).

## §Versioning — history

| Version | Date | Bump class | Authoritative source |
|---|---|---|---|
| v1.0.x | 2026-03 → 2026-04 | offline_replay baseline | ADR-0005 + early patches |
| v1.1.0 | 2026-05-18 | additive minor — live-probe scan mode | ADR-0009; BEAAA-531 op-seat; BEAAA-535 strategic countersign |
| **v1.2.0** | **2026-05-30** | **additive minor — measured /rescans + persistence semantics + SLA flags** | **ADR-0017; BEAAA-1243 HoUW; BEAAA-1236 UW op-seat; BEAAA-1103 Claims Architect; BEAAA-1255 build umbrella; BEAAA-1283 ratification (WS7)** |
| v1.3.0 → v1.7.0 | 2026-06 → 2026-07 | additive minors — see per-version patch files | `are-scanner-api-v1-v1.3.0`…`-v1.7.0-patch.md` (PII scan-time + attestation dims; CI/CD scan-API extensions BEAAA-11714/ADR-0042) |
| **v1.8.0** | **2026-07-05** | **additive minor — BEHAVIORAL-INTEGRITY evidence surface (τ-bench / STOP / tool-grounded factuality); 3 new `evidence_refs` classes + reported-not-scored `behavioral_integrity` block; no scoring change** | **`are-scanner-api-v1-v1.8.0-patch.md`; placement CEO ruling BEAAA-11807; emitters BEAAA-11808 / BEAAA-11809; UW concurrence BEAAA-11800 / BEAAA-11803; owning ticket BEAAA-11810** |

Existing v1.0.x and v1.1.0 consumers that call `POST /scanner/v1/scans`
receive byte-identical responses beyond the two pure-additive boolean
flags (default `false`) — no required new fields, no removed fields, no
renamed fields. `POST /scanner/v1/rescans` callers upgrade to v1.2.0
when they are ready to consume the three new fields.

## §Feature-flag gating

`POST /scanner/v1/rescans` is gated by `ARE_RESCANS_MEASURED` (env var,
`api/app.py:RESCANS_MEASURED_ENV`).

- **v1.2.0 default:** `on` — measured route is live, returns the v1.2.0
  envelope above.
- **Rollback Path 1 (Tier 2, ≤10 min):** set `ARE_RESCANS_MEASURED=off`
  in the runtime environment. The route reverts to the legacy doc-only
  response shape (`{"status": "doc_only", "agent_id": ..., "purpose":
  ..., "note": ...}`). Existing committed forensic bundles + their
  hashes remain valid and queryable; the rollback only stops emitting
  new ones via this route.

## Rollback

Tier 2. Two rollback paths are available:

1. **Flag flip (≤10 min).** Set `ARE_RESCANS_MEASURED=off`. Stops the
   measured `/rescans` path immediately. Existing bundles + hashes
   untouched.
2. **Contract revert (≤30 min).** Stop emitting `prior_scan_refs[]`,
   `prior_scan_refs_truncated`, `forensic_bundle_uri`, `snapshot_hash`,
   `partial_completion`, and `confidence_below_adjudication_threshold`
   from new responses (contract reverts to v1.1.0 surface). Existing
   committed bundles remain in cold storage; their URIs and hashes
   remain valid. **No bundle deletion required or permitted** (write-
   once guarantee at the Object Lock layer).

## Co-sign reference manifest

For citation by downstream consumers per `scanner-implementation-supervision-v1@v1.0`:

- `cto_cosign_ref`: ADR-0017 accepted 2026-05-30 + BEAAA-1255 comment
  56b55cb5 (CTO co-sign 2026-05-30); WS7 ratification at BEAAA-1283.
- `houw_cosign_ref`: [BEAAA-1243](/BEAAA/issues/BEAAA-1243) (Head of UW
  & AI Risk, 2026-05-30; pre-grants Q1/Q2a/Q2b/Q3/Q4 without itemized
  variance).
- `uw_operational_cosign_ref`: [BEAAA-1236](/BEAAA/issues/BEAAA-1236)
  (Underwriter operational, 2026-05-30).
- `claims_architect_cosign_ref`: [BEAAA-1103](/BEAAA/issues/BEAAA-1103)
  (Claims Architect, conditional on ADR-0017 §Persistence-semantics).
- `scanner_engineer_ratification_ref`: WS7 close on
  [BEAAA-1283](/BEAAA/issues/BEAAA-1283) comment c9978a82 (umbrella
  [BEAAA-1255](/BEAAA/issues/BEAAA-1255) is the CTO-owned record; the
  Scanner Engineer ratification lives on the WS7 child because the
  access-control surface scopes mutation to the assignee).

## Required artifact fields (per supervision contract @v1.0)

- `scanner_version`: pinned per release; v1.2.0 cuts at scanner_version
  ≥ 0.3.8 (per WS5 BEAAA-1277 smoke + WS6 BEAAA-1278 N=5).
- `corpus_version`: unchanged from v1.1.0 baseline (`0.1.0` on the
  smoke harness; per-agent calibration sidecars retain their pinned
  versions).
- `probe-set-version`: unchanged (no probe-set touch in WS1–WS7).
- `rubric_point_id` range affected: **none** (route-level + persistence
  layer + SLA-policy layer; no rubric edit).
- `N=5 calibration delta`: BEAAA-1278 doc
  [scope-beta-n5-calibration-rescans-vs-scans-v1](/BEAAA/issues/BEAAA-1278#document-scope-beta-n5-calibration-rescans-vs-scans-v1)
  — Δ=0 on `are_total` and every subscore; variance band=0;
  discrimination half-range=22.5 around mean 61.5 (well outside ±10
  collapse threshold); Supreme-Law verdict PASS.
- `upstream_eval_version`: unchanged (no upstream eval wrapping
  changes; live-probe orchestrator pins are unchanged on /rescans by
  shared-entrypoint construction).
- `cto_cosign_ref`: see Co-sign reference manifest above.
