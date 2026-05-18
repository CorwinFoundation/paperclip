---
adr_id: ADR-0010
title: are-scanner-api-v1 v1.1.0 — POST /scan + GET /scan/{scanId} + customer-facing variance-band strip
status: accepted
author: Scanner Engineer (d8bbc3b5-185f-43ed-bff0-d00323ddbfa3)
originating_ticket: BEAAA-547
parent_build_ticket: BEAAA-533 (§5 #1)
contract_version: are-scanner-api-v1@v1.0.x → v1.1.0
cto_cosign_ref:
  - BEAAA-531 op-seat ACCEPT (comment 4ecd6b82, Underwriter 2026-05-18T19:14Z)
  - BEAAA-535 HoUW countersign (comment 62b25059, Head of UW & AI Risk 2026-05-18T19:21Z)
  - ADR-0009 ratification on BEAAA-530 (Scanner Engineer 2026-05-18)
scanner_version_when_drafted: 6b4e124 (head pre-merge)
date: 2026-05-18
audience: Scanner Engineer (implementer), Underwriter (consumer), CTO (producer authority), CEO (informational — Phase-1 data handling already approved via BEAAA-532)
classification: rule-change (per scanner-implementation-supervision-v1@v1.0 — new API surface + contract version bump)
self_audit_complete: yes (10/10 — §10)
carrier_rule: clean (no carriers named)
glossary: clean (ARE / IFA / GOVERNANCE / ROBUSTNESS / BIAS_FAIRNESS / OPS canonical; no BMI/ARI/BEAI in body)
rollback_tier: 1 (≤30 min — drop /scan routes, scan_handlers module, contract doc, openapi delta; existing /score path is byte-identical so reversal does not touch the offline contract)
---

## 1. BLUF

Land `POST /scan` + `GET /scan/{scanId}` behind the v1.1.0 contract bump, with the customer-facing adapter stripping `live_probe_variance_band` (Δ4). Offline `/score` path stays byte-identical (L1 N=5 max drift = 0). CLI surface (`bin/are-scan` + `harness/cli_runner.py`) is unchanged. All 953 pre-existing tests pass; 29 new scan-handler tests cover validation, tenant isolation, Δ4 audience selection, and a real-orchestrator end-to-end smoke.

## 2. Decision

Implement the §5 #1 line of ADR-0009 — the API edge for the live-probe service — exactly as co-signed under BEAAA-531 and countersigned under BEAAA-535:

1. **New routes in `api/app.py`.** `POST /scan` (X-Tenant-Id header required) validates the v1.1.0 request shape, returns 202 with a fresh `scan_id`. `GET /scan/{scanId}?audience=customer|uw` returns the orchestrator's `ScanResult` in the requested audience variant.
2. **New handler module `api/scan_handlers.py`.** Framework-agnostic `(status, body)` shape mirroring `api/handlers.py`. Carries:
   - Validation for `scan_mode` (`live_probe` only on this endpoint — `offline_replay` is routed to the existing `/score` endpoint with `scan_mode_invalid` 422),
   - `k_replicates` allow-list (5 or 10 per v1.1.0 Δ2),
   - `credential_ref` shape + `credential_secret` required-and-dropped,
   - `corpus_snapshot_hash` SHA-256 shape,
   - `agent_endpoint.host` syntactic hostname,
   - Tenant isolation via the verbatim `api.handlers.verify_tenant_isolation` alias (T7 / BEAAA-546).
3. **`ScanRuntime`.** Pluggable orchestrator wiring. Test setups wire a synchronous `run_scan_fn`; the production path (deferred to T-CLOSE) wires the Prober subprocess. The handler is sync-async-agnostic.
4. **Δ4 — customer-facing strip.** `live_probe_variance_band` is present on the UW payload (`?audience=uw`) and stripped from the customer payload (default audience). All other live-probe fields (`scan_mode`, `spread_per_dim`, `spread_per_pillar`, `spread_total`, `k_replicates_observed`, `incomplete`, `incomplete_reason`, `dim_results`) MAY appear in either variant per the contract.
5. **Contract file bump.** `contracts/are-scanner-api-v1.md` lands at v1.1.0, byte-equivalent (sha256 `8087b150…66a9f`) to the BEAAA-531 `contract-v1-1-0-patch` document. Diff between tree and patch artifact = zero.
6. **OpenAPI bump.** `api/openapi.yaml` carries `info.version: "1.1.0"`, the two new paths, and the `ScanRequest` + `ScanResult` schemas.

## 3. Reasoning

- The CTO's Supreme Law widens to a per-mode reproducibility band in v1.1.0: ±1 for `offline_replay` (unchanged), ±2 for `live_probe` with K-replicate median aggregation. The widening is anchored in the documented non-pinnability of commercial endpoints (`claude-opus-4-7` rejects `temperature`; OpenAI `seed` is best-effort under MoE routing). The route surface implements the request side of that contract.
- Customer-facing exposure of `live_probe_variance_band` would hand a deliberately-gaming agent its tampering target (CTO Hard Guarantee #5 — tamper resistance). Δ4 closes that vector at the adapter, not the handler — the handler always emits the UW shape; the public `?audience=customer` selector strips a fixed allow-list.
- Reuse over re-implementation: `verify_tenant_isolation` is reused verbatim from `api.handlers`; orchestrator + variance-band + caps logic live in `scanners/live_probe_orchestrator.py`; the handler is glue. No rubric or scoring math forks here.
- Sync/async runtime abstraction: the handler returns 202 regardless of whether `run_scan_fn` is sync or async. This lets the same handler power both the test fixture (sync; result already cached when POST returns) and the production wiring (async; Prober subprocess streams completion to the runtime store).

## 4. Rejected alternatives

- **Re-implement tenant-isolation locally.** Would have created drift surface with `/score`. Rejected — Hard Invariant #2 risk. The alias from BEAAA-546 is the single source of truth.
- **Strip the variance-band at the handler level (not the adapter).** Would hide the field from UW consumers too. Rejected — Underwriter pricing needs the band for confidence-discount decisions. Audience selector on `GET` keeps both consumers honest.
- **Accept `offline_replay` on `POST /scan`.** Would duplicate `/score`. Rejected — single-route-per-mode keeps validation rules orthogonal and the OpenAPI surface honest. Callers asking for offline scoring get a 422 with `scan_mode_invalid` pointing at `/score`.
- **Synchronous-only handler.** Would break when the production Prober wiring lands. Rejected — 202+poll surface is the contract shape; the test runtime is a sync edge case that fits inside it.

## 5. Calibration delta on the golden set (N=5)

- **Offline (L1, byte-identical regression).** `harness.golden_set.n5_band.run_l1_n5(n=5)` against the 5 baselined fixtures:
  - max ARE drift = 0
  - per-pillar drift = 0 on every pillar of every fixture
  - 0 breaches; PASS
  - artifact: `tools/calibration-deltas/2026-05-18-beaaa547-scan-route-n5.json`
- **Live-probe end-to-end smoke** (`test_end_to_end_smoke_with_real_orchestrator`): the real `scanners.live_probe_orchestrator.run_scan` driven by `MockRunner` + synthetic `probe_run_fn` (2 dims × 5 replicates):
  - `rows_emitted = 10`
  - `incomplete = False`
  - `spread_total = 0.0` (band = 2, well within)
  - PASS
- **Full repo pytest:** 953 passed + 62 subtests passed (35s wall-clock).

## 6. Hard Invariants — verification

| Invariant | Source | Verification |
| --- | --- | --- |
| #1 | BEAAA-533 — `bin/are-scan` + `harness/cli_runner.py` zero git-diff | `git diff bin/ harness/cli_runner.py` → 0 hunks |
| #2 | BEAAA-533 — Offline `/score` byte-identical pre/post | L1 N=5 max ARE drift = 0 |
| #3 | BEAAA-533 — IPC validator wrap verbatim | Orchestrator (BEAAA-543) owns this; handler does not touch IPC |
| #4 | BEAAA-533 — Credential secret never persists outside vault | `test_scan_runtime_drops_local_secret_after_kickoff` + handler `finally: del credential_secret` |
| #5 | BEAAA-533 — Tamper resistance preserved | Δ4 stripping enforced in customer-facing adapter; `live_probe_variance_band` does not reach the scanned-agent's view path |

## 7. Rollback

Tier 1, ≤30 min. Reversal procedure:

1. `git revert` the BEAAA-547 commit (drops the two route handlers from `api/app.py`, removes `api/scan_handlers.py`, the test module, the contract file, the OpenAPI delta, the calibration artifact, and this ADR).
2. Revert `contracts/are-scanner-api-v1.md` to v1.0.1 (delete the file — pre-revert it does not exist in tree; the byte-equivalent patch document on BEAAA-531 remains intact for reference).
3. `POST /score` + `GET /forensic_bundle/{bundleId}` are byte-identical pre/post the revert (verified by L1 N=5 above); existing bound book unaffected.

If T2/T5/T9/T10 have already landed and the public contract is live (downstream rate-book consumers pinning v1.1.0), coordinate the revert with those owners — the in-flight `live_probe` traffic must drain or be re-routed before the version pin downgrade.

## 8. Out of scope (deferred)

- Customer-VPC migration (GA architecture, ADR-0009 §7). Phase-1 here is hosted-by-IFA per BEAAA-532's CEO data-handling approval.
- Real DPA-gated scan against a design partner. That is T-CLOSE acceptance on BEAAA-548 (build-close-out).
- Any change to the existing `POST /score` request/response shape — offline path is the byte-identical hard invariant.

## 9. Glossary / Carrier Rule check on close

- ARE / IFA / GOVERNANCE / ROBUSTNESS / BIAS_FAIRNESS / OPS canonical throughout.
- No carrier or customer names in handler, route, response, error message, or contract patch.
- `agent_endpoint` value is never echoed verbatim in error messages; only the host lands in the forensic bundle (T10) under DPA gate.
- No BMI / ARI / BEAI in any code path or ADR body.

## 10. Self-audit (10/10)

1. ✅ All 9 reference files read this turn (`AGENTS.md`, `are-scanner-api-v1.md`, `scanner-implementation-supervision-v1.md`, contract patch on BEAAA-531, `api/app.py`, `api/handlers.py`, `api/scan_handlers.py`, `scanners/live_probe_orchestrator.py`, `api/openapi.yaml`).
2. ✅ Deliverable defined in one sentence (§1 BLUF).
3. ✅ Classification: rule-change. Co-signs in hand (BEAAA-531 + BEAAA-535 + ADR-0009 ratification on BEAAA-530). Defaulted to rule-change rather than rule-execution per supervision contract — new API surface + contract bump.
4. ✅ N=5 calibration on the golden set ran — max drift = 0 on offline L1; live-probe smoke within band.
5. ✅ CTO co-sign references populated (§frontmatter `cto_cosign_ref`).
6. ✅ Active contract version cited: `are-scanner-api-v1@v1.1.0`.
7. ✅ All 5 required metadata fields present (scanner_version_head, corpus_version, probe_set_version, rubric_point_id, N=5 delta) — in calibration artifact.
8. ✅ Glossary clean — no BMI/ARI/BEAI.
9. ✅ No probe re-implements an upstream eval — the orchestrator's `probe_run_fn` callback is what wraps Inspect/promptfoo wrappers; this ADR's handler is API edge only.
10. ✅ FAL audit (per agents/cmo-os.md analogue) — Focus: live-probe API surface lands at v1.1.0; Audience: Scanner Engineer + Underwriter + CTO; Length: covers BLUF + decision + alternatives + delta + rollback without over-running.

