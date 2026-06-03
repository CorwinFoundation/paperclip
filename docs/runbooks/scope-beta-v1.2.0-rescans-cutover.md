# Cut-over Runbook — `/scanner/v1/rescans` v1.1.x → v1.2.0 (live endpoint)

## BLUF

In-tree v1.2.0 is merged at git `00b3486` (BEAAA-1361 `done`). This runbook lifts the merged build into the live `/scanner/v1/rescans` endpoint, soaks 24h on Syllable apichat traffic, and confirms claims-grade audit-trail + SLA emission against real production calls. Rollback is **env-var-only** (Path 1 from ADR-0017 §6 — ≤10 min, no redeploy).

## Pins (every artifact below must carry these)

- `scanner_version`: `0.3.9` (`scanners/__init__.py` SCANNER_VERSION)
- `corpus_version`: `0.1.0`
- `rubric_version`: pinned per envelope (rubric.yaml current rev)
- `contract_version`: `are-scanner-api-v1@v1.2.0`
- `seed`: `42`
- `model_versions`: from `scanners/model_versions.json`
- `forensic_bundle_uri` prefix: `s3://ifa-forensic-bundles/agent-<agent_id>/scan-<scan_id>.json`

## Pre-cut-over checklist (Scanner Engineer)

1. **Image / artifact build** — build the v1.2.0 production artifact from git `00b3486` (master tip ≥ this commit). Tag the resulting image/wheel `are-scanner-1.2.0-<git-sha>`.
2. **Env profile diff** — diff the prod environment against the v1.1.x staging profile. Required env on prod:
   - `ARE_PRODUCTION_SCAN_WIRING=1` (lifts the 503 on /scan + /scanner/v1/rescans by binding `scan_runtime`).
   - `ARE_RESCANS_MEASURED` **unset** or `=on` (post-WS7 default is measured; only `=off` reverts to doc-only). Confirm no stale `=off` is set in prod manifests from the v1.1.0 era.
3. **Health probe parity** — pre-deploy probe to /score + /forensic_bundle/{id} must return the same byte-identical envelope as v1.1.x (Hard Invariant #1 of BEAAA-553, ADR-0017 §6.1).
4. **Calibration replay** — re-run the WS6 calibration N=5 against the build artifact; spread = 0, byte-identical replay across all 6 calibrated goldens (B1/G1/G2/M1/M2/M3). Block deploy if spread > 0 on any golden.
5. **SLA registry pre-flight** — confirm `app.state.sla_registry` initializes empty and `GET /scanner/v1/sla` returns `{}` before the first rescan call.

If any pre-flight fails: HALT, post the failing artifact on this ticket, do not deploy.

## Cut-over steps

1. **T-cut announce** — comment on this ticket `cut-over T-0` with timestamp + scanner_version + corpus_version + rubric_version + git SHA + forensic_bundle bucket region.
2. **Deploy v1.2.0 artifact** — promote the v1.2.0 image to the prod fleet behind the same load balancer / API gateway that fronts `apichat.syllable.cloud`'s tenant route. Drain v1.1.x instances using standard rolling-deploy (no in-flight request drops).
3. **First synthetic rescan** — issue one synthetic `POST /scanner/v1/rescans` with `agent_id="syllable.apichat.bea-cal-1"`, `purpose="pre_bind"`, seed=42. Capture status code, response body, `forensic_bundle_uri`, `snapshot_hash`, `prior_scan_refs[]` length. Required: 200 OR 202, both `forensic_bundle_uri` and `snapshot_hash` populated and non-empty, `prior_scan_refs[]` shape matches ADR-0017 §2.1 (4-field entries).
4. **SLA emission check** — `GET /scanner/v1/sla` must now return a per-purpose entry (not `{}`). Sample wall-clock latency must be within the per-purpose target band.
5. **First customer call** — open the live Syllable apichat tenant route for the v1.2.0 path. Live tenant call is permitted only after step 3+4 pass; this is the BEAAA-1277 chain extension into prod.

## Soak phase (24h)

- **Counter target**: first 100 production rescans logged.
- **Per-call evidence required** in the scan_log row:
  - `scan_id`, `run_timestamp`, `purpose`
  - `forensic_bundle_uri` (write-once, distinct per scan)
  - `snapshot_hash` (sha256, at-write-time, recomputable from canonical-JSON of envelope per RFC 8785 JCS)
  - `prior_scan_refs[]` per ADR-0017 §2.1 (4-field entries, ordered ascending by `run_timestamp`, cap=100 with `prior_scan_refs_truncated` sentinel if exceeded)
  - `purpose_aware_sla` block (BEAAA-1274 WS4 — purpose × wall-clock band + degradation flag)
- **End-to-end audit-trail spot check**: at minimum 1 call, fetch `forensic_bundle_uri` content, recompute `snapshot_hash` against canonical-JSON of returned envelope, byte-compare against persisted `snapshot_hash`. Match ⇒ ADR-0017 §2.3 holds in prod.
- **SLA percentile target**: `GET /scanner/v1/sla` rolling p95 within the per-purpose band registered at WS4 close.

## Acceptance gates (matching BEAAA-2132)

| Gate | Source | Pass condition |
|---|---|---|
| AC-1: 100 rescans logged | scan_log COUNT(*) on v1.2.0 | ≥ 100 rows, all with non-null forensic_bundle_uri + snapshot_hash |
| AC-2: SLA percentile in band | `GET /scanner/v1/sla` rolling window | p95 ≤ per-purpose target at 24h+0 |
| AC-3: Audit-trail end-to-end | 1 spot-check call | recomputed snapshot_hash == persisted; bundle fetchable; prior_scan_refs immutable under interim rubric change (already invariant-tested via tests/test_rescans_prior_scan_refs_beaaa1273.py) |
| AC-4: Runbook + rollback committed | this document + ADR-0017 §6 | landed on master |
| AC-5: UW operational pre-read | BEAAA-2132 status flip to `in_review` with UW (afa65747) tagged | post-24h-soak |

## Rollback (no order-of-magnitude code change required)

- **Path 1 (Tier-2, ≤10 min, default)**: set env var `ARE_RESCANS_MEASURED=off` on the prod fleet. The /scanner/v1/rescans route then returns the doc-only legacy envelope (see api/app.py:147-164). Existing forensic bundles + snapshot_hash values remain valid. No code change, no redeploy.
- **Path 2 (Tier-1, full redeploy, ≤2h)**: revert the deploy to the v1.1.x image tag. Use only if Path 1 cannot rectify a regression observed in /score parity (Hard Invariant #1) — which would mean a contract-shape bug rather than a measurement-path bug.
- **Trigger Path 1 if any of**: snapshot_hash recompute mismatch on a real call, prior_scan_refs[] empty when one should exist, forensic_bundle_uri NULL on a measured response, p95 wall-clock breach > 2× per-purpose target for 5 consecutive samples, or any 5xx on /scanner/v1/rescans > 1% over a 10-min window.
- **Trigger Path 2 if** Path 1 + 10-min soak does not restore the byte-identical /score parity probe.

## Reporting

- T-0 cut-over comment on this ticket (CTO + Scanner Engineer).
- T+1h soak status comment (Scanner Engineer): rescan count, p95, first audit-trail spot-check verdict.
- T+24h soak close comment (Scanner Engineer): final counter, AC-1–AC-3 verdict, evidence-bundle artifact path, candidate `in_review` flip with UW co-sign request.
- Calibration delta table (if any deploy-side codepath touched goldens): under `tools/calibration-deltas/<YYYY-MM-DD>-v1.1.x-vs-v1.2.0.md` per Supreme Law standing procedure. Expected: spread = 0 (no scoring math change — only persistence + SLA emission paths).

## Carrier Rule

Public artifacts from this cut-over (carrier pre-reads, customer comms) describe Syllable apichat as a design-partner whose surface is under live measurement. Carriers are partners; nothing here implies adversarial framing.

## Metadata

- artifact_type: cut-over runbook
- owner: CTO (88e68019)
- executor: Scanner Engineer (d8bbc3b5)
- reviewer: Underwriter (afa65747) operational pre-read at 24h-soak close
- contract_version: are-scanner-api-v1@v1.2.0
- scanner_version: 0.3.9
- corpus_version: 0.1.0
- supervision contract: scanner-implementation-supervision-v1@v1.0 — classification = rule-execution (no rule-change co-sign required; co-sign for measurement-path engagement covered by ADR-0017 ratification at WS7 / BEAAA-1283)
- canonical terms only: true (ARE, IFA; "live endpoint" = production-fronted /scanner/v1/rescans)
- self_audit_passed: true
