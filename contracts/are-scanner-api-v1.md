---
artifact_type: other
owner: underwriter
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "v1.1.0 contract bump enables live-agent probe scans against commercial endpoints while honestly disclosing the widened variance band and refusing to quote on band-exceed."
contracts_referenced: ["are-scanner-api-v1@v1.0.x → v1.1.0", "scanner-implementation-supervision-v1@v1.0", "uw-rubric-supervision-v1@v1.0"]
ticket_refs: ["BEAAA-531", "BEAAA-535", "BEAAA-533", "BEAAA-530", "BEAAA-529"]
proof_points_cited: ["ADR-0009 adr-0009-live-agent-hosted-probe-v0 (doc 7373b284-034b-4c29-835b-544b1cc85fed)", "Op-seat UW co-sign BEAAA-531 comment 4ecd6b82-55aa-4685-8b8c-e00c17cc0e18", "HoUW countersign BEAAA-535 comment 62b25059", "harness/runner.py:84–86", "harness/runner.py:96–100"]
version: "v1.1.0-patch"
prior_version: "are-scanner-api-v1@v1.0.x"
self_audit_passed: true
escalations_noted: ["No Founder gate: pillar weights / rubric points / bound-book pricing unchanged (HoUW countersign §Founder-gate analysis)."]
---

# Contract patch — `are-scanner-api-v1.md` v1.0.x → v1.1.0

**Status:** approved for merge.
**Op-seat co-sign:** [BEAAA-531 #4ecd6b82](/BEAAA/issues/BEAAA-531#comment-4ecd6b82-55aa-4685-8b8c-e00c17cc0e18) (Underwriter operational, 2026-05-18T19:14Z).
**Strategic-seat countersign:** [BEAAA-535 #62b25059](/BEAAA/issues/BEAAA-535#comment-62b25059) (Head of UW & AI Risk, 2026-05-18T19:21Z).
**CTO authority:** ADR-0009 `adr-0009-live-agent-hosted-probe-v0` ratified on [BEAAA-530](/BEAAA/issues/BEAAA-530#document-adr-0009-ratification) by Scanner Engineer 2026-05-18.
**Build implementer:** Scanner Engineer on [BEAAA-533](/BEAAA/issues/BEAAA-533) (build-scope follow-on, unblocks when BEAAA-531 closes done — CEO §6b gate on [BEAAA-532](/BEAAA/issues/BEAAA-532) already done).
**Change classification:** additive minor; Tier 1 rollback per ADR-0009 §9 (drop the four new fields + the new mode; existing offline_replay behavior unaffected).

---

## §Version header (replaces line 1)

```
# Contract: ARE Scanner API — v1.1.0
```

## §Input (additive — `scan_mode`, `k_replicates` fields)

Add the following keys to the request schema (both optional; backward-compatible — omitted = `offline_replay` with K=1):

```jsonc
{
  // ... existing fields unchanged ...
  "scan_mode": "offline_replay | live_probe",   // optional; default "offline_replay". Mode selector.
  "k_replicates": 5 | 10                         // optional; default 5. Only meaningful when scan_mode = "live_probe". K=10 tightens observed spread without renegotiating the contract band.
}
```

**Semantics:**
- `scan_mode: "offline_replay"` — today's behavior. Deterministic kernel over supplied artifacts. ±1 reproducibility surface unchanged.
- `scan_mode: "live_probe"` — Prober drives the target endpoint with the pinned probe corpus K times; per-dim `raw` is the median across K replicates; per-dim spread is recorded.
- `k_replicates` is ignored when `scan_mode: "offline_replay"`.

## §Output (additive — live_probe fields)

Add the following keys to the response schema. Fields marked **UW-facing only** are stripped from the customer-facing report-card adapter (per Δ4 tamper-resistance hardening):

```jsonc
{
  // ... existing fields unchanged ...
  "scan_mode": "offline_replay | live_probe",   // mirror of the requested mode
  "spread_per_dim": {                            // present only when scan_mode = "live_probe"
    "<rubric_point_id>": 0.0..N,                 // observed spread (max - min) across K replicates, per rubric point
    // ... one entry per rubric_point_id in the run ...
  },
  "spread_per_pillar": {                         // present only when scan_mode = "live_probe"
    "GOVERNANCE": 0.0..25,
    "ROBUSTNESS": 0.0..25,
    "BIAS_FAIRNESS": 0.0..25,
    "OPS": 0.0..25
  },
  "spread_total": 0.0..100,                      // present only when scan_mode = "live_probe"
  "live_probe_variance_band": 2,                 // UW-facing only (Δ4); integer; documents the ±2 band that bound this run
  "k_replicates_observed": 1..K,                 // present only when scan_mode = "live_probe"; actual successful replicate count
  "incomplete": true | false                     // true when spread_total > 2 OR any pillar spread > 2 OR k_replicates_observed < requested K
}
```

**`incomplete: true` semantics** (live_probe mode):
- `spread_total > 2` → `incomplete: true`
- any `spread_per_pillar[*] > 2` → `incomplete: true`
- `k_replicates_observed < requested k_replicates` (after Prober's bounded retry budget exhausts) → `incomplete: true`

`incomplete: true` routes to the existing Underwriter Hard Guarantee #1 hard rejection (no pricing without evidence trail). The quote engine MUST NOT price an `incomplete: true` score, regardless of `scan_mode`.

## §Hard guarantees (CTO is on the hook for these) — patch

Replace clause #1 with two sub-clauses (1a unchanged from v1.0.x; 1b new):

> 1a. **Reproducibility (offline_replay).** Same agent + same scanner_version + same corpus_version + same model_versions + same seed + `scan_mode: "offline_replay"` → same `are_total` within ±1 point and same value for every subscore within ±1 point. Enforced in CI: every release runs the calibration set N=5 times in offline_replay mode; release blocks if total or any subscore varies by more than 1 point.
>
> 1b. **Reproducibility (live_probe).** For `scan_mode: "live_probe"`, the K-replicate median aggregation yields a result that, across the K replicates within a single scan, varies by no more than ±2 on `are_total` and ±2 on every pillar subscore. Anything wider sets `incomplete: true` and refuses-to-quote. Enforced in CI: every release runs the live_probe contract test against the calibration set with `k_replicates: 5`; release blocks if observed spread on a calibration target exceeds the band without triggering `incomplete: true`. The ±2 widening exists strictly to absorb non-pinnable commercial-endpoint variance (e.g. `claude-opus-4-7` rejects the `temperature` kwarg per `harness/runner.py:84–86`; OpenAI `seed` is best-effort under MoE routing per `harness/runner.py:96–100`).

Clauses #2–#5 unchanged.

## §Hard guarantees (Underwriter is on the hook for these) — unchanged

Clauses #1–#4 bind both `scan_mode` values identically:
- UW #1 "No pricing without evidence trail" — applies via `incomplete: true` enforcement in both modes.
- UW #2 "Uncovered dimensions priced at maximum risk" — mode-independent.
- UW #3 "Band-to-premium mapping is filed" — unchanged for `offline_replay`; for `live_probe`, the rate-book extension consuming `spread_per_dim` lands as a separate filed artifact (downstream rate-book child) before the first live_probe-bound policy ships.
- UW #4 "Mid-term re-scan triggers documented" — unaffected by this bump.

## §Approval gates — unchanged

This v1.1.0 bump is itself classified under "Scoring logic change (CTO + Underwriter)" (additive surface; narrower constraint surfaces unchanged). CTO + Underwriter co-sign authority is sufficient; pillar weights and rubric points are unchanged, so no Founder sign-off is triggered. Future contract bumps follow the existing gate table.

## §Customer-facing adapter rule (NEW sub-section)

Add at end of §Output:

> **Customer-facing report-card adapter.** When emitting a customer-visible report card (the artifact the customer sees in their portal or the Approval Pack), the adapter MUST strip the `live_probe_variance_band` field. The variance-band figure is UW-facing and audit-facing only; exposing it to the scanned agent (or to a sufficiently adversarial customer-side proxy) gives the scanned agent its tampering target, which would degrade CTO Hard Guarantee #5 (tamper resistance). All other live_probe-mode response fields (`scan_mode`, `spread_per_dim`, `spread_per_pillar`, `spread_total`, `k_replicates_observed`, `incomplete`) MAY appear in customer-facing output at the customer-success team's discretion; only `live_probe_variance_band` is mandated UW-only.

## §Versioning — bump

Update the standing version pointer. The Underwriter pricing engine continues to pin to a specific contract version; pinned consumers must opt into v1.1.0 explicitly. Existing v1.0.x consumers that omit `scan_mode` continue to receive offline_replay-shaped responses byte-identically.

---

## Build-spec implementation flags (NON-contract — for the BEAAA-533 build spec)

These do not change the contract shape; they are implementation-detail directives the build child must honor:

1. **Partial-K retry budget.** Prober retries failed replicates within a bounded retry budget per scan (recommendation: retry budget = K, so total replicate attempts ≤ 2K). On budget exhaustion with `k_replicates_observed < requested K`, return `incomplete: true`. Do NOT return a median-over-K' result with implicit weaker variance.
2. **Same semantics extend to `k_replicates: 10`.** If retry budget exhausts at K' < 10, return `incomplete: true` rather than median-over-K'<10.
3. **`live_probe_variance_band` stripping** is enforced in the customer-facing report-card adapter (not in the `/score` handler — the handler always emits the UW-facing payload). Build child must implement the adapter-side strip and unit-test it.

## Co-sign reference manifest (stamped on BEAAA-533)

For citation by the build child task per `scanner-implementation-supervision-v1@v1.0` "Required artifacts per Operational action":

- `cto_cosign_ref`: ADR-0009 ratification record on [BEAAA-530#document-adr-0009-ratification](/BEAAA/issues/BEAAA-530#document-adr-0009-ratification) (Scanner Engineer, 2026-05-18).
- `uw_operational_cosign_ref`: [BEAAA-531 #4ecd6b82](/BEAAA/issues/BEAAA-531#comment-4ecd6b82-55aa-4685-8b8c-e00c17cc0e18) (Underwriter operational, 2026-05-18T19:14Z).
- `head_of_uw_cosign_ref`: [BEAAA-535 #62b25059](/BEAAA/issues/BEAAA-535#comment-62b25059) (Head of UW & AI Risk, 2026-05-18T19:21Z).

## Rollback

Tier 1, ≤2 min. Reversal procedure: drop `scan_mode`, `k_replicates`, `spread_per_dim`, `spread_per_pillar`, `spread_total`, `live_probe_variance_band`, `k_replicates_observed`, `incomplete` (when added by this delta) from the contract doc and the `/score` handler response shape. Existing `offline_replay`-shaped responses unaffected. Existing CLI (`bin/are-scan`) unaffected. Existing bound book unaffected.
