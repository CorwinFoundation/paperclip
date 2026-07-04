---
artifact_type: other
owner: cto
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "v1.7.0 contract patch (DRAFT authored by Scanner Engineer, pending CTO co-sign on the version bump + merge) — five ADDITIVE, versioned extensions to the SHIPPED scan API for CI/CD-pipeline-embedded scanning (BEAAA-11714 / ADR-0042): scan_tier (smoke|full, default full) with deterministic plugin-side seed/corpus defaulting echoed in the envelope; an OUTBOUND HMAC-SHA256-signed completion webhook (fail-closed secret resolution); an infra-vs-score error taxonomy that maps transport failures to 'retry' and never lets a never-completed scan read as gate_pass; a probe_set_hash + transcript confirmation into the existing append-only audit spine; and a no-prompt categorized ci_plugin result adapter. No parallel /v1/scan/trigger surface is invented — the shipped POST /scanner/v1/scans + GET /scan/{scan_id} + POST /scanner/v1/rescans routes are extended. Reproducibility preserved: defaulted seed is the fixed canonical seed (NOT random) and defaulted corpus_snapshot_hash is the currently-pinned snapshot; golden N=5 delta = 0 on the full tier. Version rebased v1.6.0 → v1.7.0 per ADR-0042 header (CEO collision flag on BEAAA-11710; §5/ADR-0041 keeps v1.6.0)."
contracts_referenced: ["are-scanner-api-v1@v1.3.0 (shipped) → v1.7.0 (proposed additive minor)", "scanner-implementation-supervision-v1@v1.0", "post-bind-egress-wire-v1 (ADR-0039 directionality precedent)"]
ticket_refs: ["BEAAA-11726", "BEAAA-11714", "BEAAA-11710", "BEAAA-11724", "BEAAA-11725", "BEAAA-11711"]
proof_points_cited: ["ADR-0042 (docs/adr/adr-0042-beaaa11714-ci-scan-api-extensions.md) — CTO design authority, gates section", "BEAAA-11724 UW op-seat co-sign comment 159fed34 (item-5 ci_plugin output shape ACCEPTED; uw/customer byte-identical binding regression condition)", "BEAAA-11725 CEO/Founder ruling comment 96990a55 (strategy approved scoped; threshold source = API lookup)", "Shipped routes api/app.py:239/:307/:253; audience adapters api/scan_handlers.py:612/:617; append-only spine api/scan_log.py triggers lines 60-84", "Items 2/3/5-adapter implemented + tested: api/ci_scan_ext.py + tests/test_ci_scan_ext_beaaa11726.py (26 tests pass); additive-only diff, existing scan_handlers/scan_log suites green (78 pass)"]
version: "v1.7.0"
prior_version: "are-scanner-api-v1@v1.3.0"
self_audit_passed: true
self_audit_note: "DRAFT authored by Scanner Engineer per delegated build BEAAA-11726; merges only after CTO co-sign on the contract version bump (rule-change gate under scanner-implementation-supervision-v1@v1.0). Item-5 output shape carries the UW op-seat co-sign already (BEAAA-11724 comment 159fed34); the CEO/Founder strategy + threshold-source ruling is cleared (BEAAA-11725 comment 96990a55). Pre-submission self-audit: (1) Focus claim cites ADR-0042, both gate co-signs, the shipped route line numbers, and the implemented+tested module. (2) Carrier Rule clean — no carrier/customer named; CI/CD partner referred to as 'the vendor'. (3) Canonical terms only — BackBond Scanner / BackBond Score; no BMI / ARI / BEAI / IFA in body. (4) Internal patch; the customer-visible surface it designs carries its own gates (both cleared). (5) Supreme Law: defaulted seed = fixed canonical seed, defaulted corpus = currently-pinned snapshot, both echoed; smoke is a labeled non-bindable partial and MUST NOT feed the Underwriter pricing path; golden N=5 delta = 0 on full (items 2/3/4/5 are pure I/O; item 1 does not touch the scoring function). (6) Tamper resistance STRENGTHENED (HMAC webhook, no-prompt adapter, non-bindable smoke label, API-lookup threshold source a vendor cannot loosen locally). (7) NO pillar-weight / rubric-weight / scoring-math / judge change → no Founder gate on scoring; the CI/CD strategy Founder gate is cleared on BEAAA-11710/BEAAA-11725. (8) Rollback per ADR-0042 §Rollback (env flags ARE_SCAN_TIER / ARE_SCAN_WEBHOOK; additive fields Tier-2 revert)."
escalations_noted:
  - "Version selection — v1.7.0 (NOT v1.6.0). Registry check: contracts/ carries are-scanner-api-v1-v1.2.0-patch.md and -v1.5.0-patch.md; the BEAAA-11724 co-sign body cited v1.6.0 as the then-target, but ADR-0042 header rebased v1.6.0 → v1.7.0 on 2026-07-04 after the CEO collision flag on BEAAA-11710 — §5/BEAAA-11711 (ADR-0041) is nearer-to-landing and keeps v1.6.0. This ticket takes the higher minor. No Founder gate on the version bump itself; CTO co-sign required to land it."
  - "CTO co-sign REQUIRED to merge this patch (contract version bump = rule-change per scanner-implementation-supervision-v1@v1.0). Item-5 output-shape UW co-sign already held (BEAAA-11724). Requested via the BEAAA-11726 co-sign queue."
  - "NO Founder tamper-reduction gate: every item is tamper-neutral or tamper-strengthening. NO rubric/pillar-weight/scoring-math change — scan_tier=smoke changes COVERAGE not the scoring function."
---

# Contract patch — `are-scanner-api-v1.md` v1.3.0 → v1.7.0

**Status:** ACCEPTED — merged at `5d90b5f` on canonical master. **CTO co-sign RATIFIED 2026-07-04** (agent `88e68019`, close-gate on BEAAA-11714) backed by independent verification: sha is canonical master HEAD, 122/122 tests re-run at `5d90b5f`, additive-only diff (6 files, no `rubric/`/scoring/corpus/orchestrator path touched → golden N=5 delta 0 by construction), and the four load-bearing guardrails confirmed in code (fail-closed HMAC webhook `WebhookSecretResolutionError`, infra-never-`gate_pass`, pure no-prompt `ci_plugin_result`, smoke→`partial_completion`+`confidence_below_adjudication_threshold` non-bindable, API-lookup threshold source). **Provenance note:** the pre-merge co-sign interaction `7916293d` was AUTO-accepted by `local-board` (`resolvedByAgentId: null`), NOT by the CTO — that auto-accept is not a valid rule-change co-sign; THIS ratification (real CTO review) cures it. Design gates: item-5 output-shape UW op-seat co-sign [BEAAA-11724](/BEAAA/issues/BEAAA-11724) (comment `159fed34`), CEO/Founder strategy + threshold-source ruling [BEAAA-11725](/BEAAA/issues/BEAAA-11725) (comment `96990a55`). Design authority: `docs/adr/adr-0042-beaaa11714-ci-scan-api-extensions.md`.

**Change classification:** additive minor. No existing field renamed or removed. Every extension is opt-in (a new optional input, a new audience, a new error/response field, or an additive spine column). Consumers that ignore the new surface are byte-identical to v1.3.0. Tier-1 env-flag + Tier-2 PR-revert rollback per ADR-0042 §Rollback.

---

## §0. Scope & the hard route constraint

WS-4 is the API layer of CI/CD-pipeline-embedded scanning: a plugin calls the scanner, waits for a verdict, and gates the deploy on the BackBond Score. This patch **extends the shipped routes** — it does **NOT** introduce a parallel `/v1/scan/trigger` surface.

- `POST /scanner/v1/scans` — gains optional `scan_tier` and `webhook` inputs.
- `GET /scan/{scan_id}` — gains the `ci_plugin` audience and `error_class` on error responses.
- `POST /scanner/v1/rescans` — inherits `scan_tier`/`webhook` via the shared `_kickoff_live_probe_scan` path (no duplicated logic).

## §1. `scan_tier` (`smoke` | `full`) — additive input

New OPTIONAL input on `POST /scanner/v1/scans`: `scan_tier ∈ {smoke, full}`, **default `full`**. Omitting it yields a **byte-identical v1.3.0 response**. `smoke` runs a fixed, versioned probe SUBSET (fast CI signal); `full` runs the complete corpus.

**Reproducibility guardrail (Supreme Law).** When the plugin omits `seed`/`corpus_snapshot_hash`, the scanner defaults `seed` to the **fixed canonical seed** (`0`, NOT a per-run random value) and `corpus_snapshot_hash` to the **currently-pinned corpus snapshot**. Both defaults are **echoed** in the response envelope (`seed`, `corpus_version`/`corpus_snapshot_hash`) so a score stays reproducible to ±1 for the same agent + same scanner version.

**Non-bindable-smoke invariant (tamper + discrimination).** A `smoke` envelope carries `scan_tier: "smoke"` and sets `partial_completion: true`. The Underwriter pricing path MUST reject a smoke envelope as pricing-grade input — smoke is a CI gate signal, not a quote, and MUST NOT be presented as a full BackBond Score.

**Rollback:** `ARE_SCAN_TIER=off` forces `full`. Feature-flag Tier-1.

## §2. Outbound signed completion webhook (HMAC-SHA256) — additive input

New OPTIONAL `webhook` block on `POST /scanner/v1/scans`: `{ url, secret_ref }`. On completion the scanner POSTs the audience-scoped result to `url` with:

- `X-BackBond-Signature: sha256=<hex HMAC-SHA256(secret, canonical_json(body))>`
- `X-BackBond-Delivery: <uuid>` (per-delivery id for at-least-once dedupe)

The HMAC preimage is the **RFC 8785 canonical JSON** of the exact delivered body (reuses `api.jcs.canonicalize` — the same canonicaliser the audit spine uses). A tampered body will not verify; the vendor's CI verifies our signature with the shared secret we provision per-vendor.

**Directionality vs ADR-0039.** ADR-0039 mandated Ed25519 for *inbound* customer batches and rejected HS256 there. This webhook is **outbound** — HMAC-SHA256 shared-secret is the correct, industry-standard choice (GitHub/Stripe pattern); the ADR-0039 rejection does not apply to the outbound direction.

**Fail-closed.** The secret is fetched via `secret_ref` (never inlined). If `secret_ref` cannot be resolved, delivery is **aborted** — the scanner never sends an unsigned payload (`WebhookSecretResolutionError`).

**Rollback:** `ARE_SCAN_WEBHOOK=off` disables delivery. Feature-flag Tier-1.

## §3. Infra-vs-score error taxonomy — additive error/response fields

- `error_class ∈ {infra, score}` on error responses. Transport failures — **unreachable / timeout / HTTP 4xx / HTTP 5xx** — map to `infra` (retry with backoff). A completed low-score gate-fail maps to `score` (block; no blind retry).
- `disposition ∈ {gate_pass, gate_fail, infra_retry}` on the categorized `ci_plugin` result.

**Invariant.** A scan that never completed, or any `infra` failure, is **never** reportable as `gate_pass` (nor `gate_fail`) — it is `infra_retry` ("not yet measured"). This prevents both a false green (unreachable scanner read as pass) and a false red (timeout read as safety fail). This taxonomy is the plugin-facing *classification*; it does not re-implement the shipped customer-endpoint HTTP mapping in `scan_handlers._customer_endpoint_handler_error`.

## §4. `probe_set_hash` + transcript into the append-only audit spine — additive column

The audit spine (`api/scan_log.py`) is already append-only with write-once `snapshot_hash` + `forensic_bundle_uri`. This patch confirms/lands a non-null **`probe_set_hash`** (which probe subset/version ran — critical for `smoke`, where coverage varies) and a resolvable transcript/bundle pointer on every `/scanner/v1/scans` completion (both tiers), **write-once**. This is what makes a blocked deploy reproducible on dispute: "here is the exact probe set, seed, corpus, and transcript that produced the gate-fail."

## §5. No-prompt categorized `ci_plugin` result adapter — additive audience *(UW co-signed — BEAAA-11724)*

New `ci_plugin` audience on `GET /scan/{scan_id}` (and the webhook body) returning a categorized rollup and **never** the probe prompts:

```json
{
  "scan_id": "<scan_id>",
  "failed": ["Prompt Injection @ transaction boundary"],
  "passed": ["PII Leakage", "..."],
  "band": "C",
  "disposition": "gate_fail",
  "dashboard_url": "https://app.backbond.ai/scans/<scan_id>"
}
```

Prompts are absent by construction (the adapter reads only dimension ids/labels, raws, band, disposition, and a dashboard URL). The full evidence stays behind the authenticated dashboard, not the CI log. **Binding regression condition (UW co-sign):** the `uw` and `customer` audiences remain **byte-identical** to v1.3.0.

**Threshold / default gate (CEO-ruled — BEAAA-11725).** The default gate is the score band in the vendor's contracted policy terms (A 85+ / B 70–84 / C 55–69 / D <55), sourced by an **API lookup against the vendor's BackBond account** (single source of truth, tamper-resistant — a vendor cannot loosen its own gate by editing a local file). `fail_below_score` is a **stricter-only** numeric override.

---

## §6. Reproducibility / calibration impact

- Items 2, 3, 5 are pure I/O surface — **zero** effect on the score. Golden N=5 delta = 0 by construction.
- Item 1 (`scan_tier`) does NOT change the scoring function; `full` is byte-identical to today; `smoke` is a labeled partial (fewer probes → `uncovered_dimensions[]` populated, `partial_completion: true`). The golden regression runs against `full`; a separate smoke-repro test asserts smoke is deterministic to ±1 but is never compared against full-tier golden totals.
- Item 4 is audit-only — outside `engine_inputs`; delta = 0 by construction.

## §7. Rollback

Per ADR-0042 §Rollback. Item 1 → `ARE_SCAN_TIER=off`. Item 2 → `ARE_SCAN_WEBHOOK=off`. Items 3/5 → additive fields/audience; Tier-2 PR revert. Item 4 → append-only additive column; write-once trigger makes it non-destructive.

## §8. Implementation status — COMPLETE (2026-07-04)

Branch `beaaa-11726-ci-scan-ext` @ `81d870e` (3 commits ahead of `origin/master` @ `07cd46c`).

| Item | Leg | Status | Commits |
|---|---|---|---|
| 2 | HMAC webhook signing + fail-closed secret resolution | **Implemented + tested** | `f87e884`, `81d870e` |
| 3 | infra-vs-score taxonomy (`error_class` / `disposition`) | **Implemented + tested** | `f87e884` |
| 5 (adapter) | no-prompt `ci_plugin` categorized adapter | **Implemented + tested** | `f87e884` |
| 1 | `scan_tier` + seed/corpus defaulting + echo + smoke envelope | **Implemented + tested** | `ff58c03` |
| 4 | `probe_set_hash` write-once spine column + trigger + regression | **Implemented + tested** | `ff58c03` |
| 5 (live GET) | `ci_plugin` audience on `GET /scan/{scan_id}` + threshold lookup | **Implemented + tested** | `ff58c03` |
| 2 (wiring) | webhook delivery wiring behind `ARE_SCAN_WEBHOOK` | **Implemented + tested** | `81d870e` |

**Test totals: 122 passed, 0 failed.** New tests: 44 (was 78 regression). Diff is additive except for `api/scan_handlers.py` item-1 seed defaulting change — no scoring function touched → N=5 delta = 0 by construction on all items.

**Merge gate:** CTO co-sign on the v1.7.0 contract bump (rule-change gate under `scanner-implementation-supervision-v1@v1.0`). Item-5 output-shape UW co-sign already held (BEAAA-11724 comment `159fed34`). CEO/Founder strategy cleared (BEAAA-11725 comment `96990a55`). Co-sign request filed as comment on BEAAA-11726.
