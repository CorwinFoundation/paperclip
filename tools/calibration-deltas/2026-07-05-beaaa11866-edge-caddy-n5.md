---
artifact_type: calibration_delta
owner: scanner_engineer
persona_audience: internal
phase: 1
canonical_terms_only: true
brand_backbone_applicable: false   # internal calibration-delta record; the edge tier is staging/internal infra, not an external-bound asset
focus_claim: "BEAAA-11866 D1 edge front (Caddy staging reverse proxy) is score-neutral: it adds only infra files (infra/edge/) and an isolated integration test (tests/integration/), touches NO file under scanners/, rubric/, corpus, probe_sets/, or api/, and changes no contract output shape. Golden N=5 delta = 0 by construction on are_total and every pillar; the Scanner scoring engine is not on this change's code path."
contracts_referenced: ["are-scanner-api-v1@v1.3.0"]
ticket_refs: ["BEAAA-11866", "BEAAA-11862", "BEAAA-11861", "BEAAA-11607"]
proof_points_cited:
  - "git diff --cached --name-only -> only infra/edge/{Caddyfile,docker-compose.staging.yml,README.md} + tests/integration/{test_edge_caddy_staging.py,_edge_fake_upstream.py} + this note; NO scanners/ rubric/ corpus probe_sets/ api/ path"
  - "docker caddy:2.8-alpine `caddy validate` -> Valid configuration; `caddy fmt` clean"
  - "tests/integration/test_edge_caddy_staging.py -> 16 passed (2 allowlist-reach, 13 default-deny opaque-404, 1 upstream-5xx normalization) against live Caddy 2.8.4"
version: "0.1"
prior_version: null
self_audit_passed: true
escalations_noted:
  - "Classification: RULE-EXECUTION (edge/infra; per BEAAA-11866 scope note, not an Underwriter gate). No rubric point, pillar weight, judge model, scoring math, corpus, or contract output-shape change. CTO co-sign requested on the diff before merge per BEAAA-11866."
  - "cto_cosign_ref: PENDING — requested on BEAAA-11866; edge tier stays on branch beaaa-11866-edge-caddy (NOT merged to origin/master) until co-sign."
  - "No Founder gate: no tamper-resistance reduction. The edge STRENGTHENS posture (default-deny route allowlist + topology hiding + opaque error normalization); it only removes attack surface, never scoring signal."
  - "No Brand Backbone gate: persona_audience=internal; staging/non-public, no DNS, no publish."
---

# BEAAA-11866 — Calibration Delta: v1.3.0 baseline vs. D1 edge front (Caddy)

**BLUF:** The D1 edge front is **score-neutral by construction**. It is a network reverse-proxy tier that sits *in front of* the scanner app; it adds only `infra/edge/` deploy artifacts and one isolated integration test. No file under `scanners/`, `rubric/`, the corpus, `probe_sets/`, or `api/` is touched, and no `/scanner/v1/scans` or `/scan/{scan_id}` response body shape is altered (the edge proxies 2xx bodies through byte-unchanged and only rewrites non-2xx into opaque errors). Golden L1 N=5 therefore reports **Δ total = 0** and **Δ per-pillar = 0** — the scoring engine is not on this change's code path.

## Contract / operational metadata

| Field | Value |
|---|---|
| `scanner_version` | `0.5.0` (unchanged; not touched) |
| `corpus_version` | `rubric.yaml@1.2.0` (golden fixture baselines per `artifacts/beaaa-387/manifest.json`; unchanged) |
| `probe-set-version` | n/a — the edge runs no probes; probe corpus unchanged |
| `rubric_point_id` | n/a — infra/edge change; no rubric point implemented or modified |
| `N=5 calibration delta` | **PASS by construction** — `max ARE drift = 0`, all pillar drifts `0.0`, `breaches = 0` (no scoring code path changed) |
| `cto_cosign_ref` | **PENDING** — requested on BEAAA-11866 before merge (rule-execution / edge-infra co-sign) |

## Why Δ = 0 by construction

The N=5 golden calibration is emitted by the scanner scoring engine (`scanners/*`, `rubric/*`) from unchanged constants (`SCANNER_VERSION=0.5.0`, `CONTRACT_VERSION=are-scanner-api-v1@v1.3.0`) over the unchanged golden corpus. This change introduces **zero** edits to any of those inputs — the staged diff is confined to `infra/edge/` and `tests/integration/`. A network tier in front of the app cannot alter a score the app already computed; for the two allowlisted routes the edge is a transparent pass-through of the app's own bytes. This mirrors the security-posture-delta-0 precedent on BEAAA-11858 and BEAAA-11607 (default-deny hardening, N=5 delta 0).

## Behavior proof (not just by-construction)

`tests/integration/test_edge_caddy_staging.py` stands the committed `infra/edge/Caddyfile` up in Docker (real Caddy 2.8.4) in front of a fake upstream and proves all three acceptance properties — **16 passed**:

- **(a) allowlist / default-deny:** `POST /scanner/v1/scans` and `GET /scan/{scan_id}` reach upstream; 13 other method/path combinations (`/score`, `/quote`, `/forensic_bundle/*`, `/scanner/v1/rescans`, `/scanner/v1/sla`, `/admin`, `/healthz`, `/`, two-segment `/scan/a/b`, wrong-method hits, unknown paths) each return opaque `404 {"error":"not_found","request_id":…}` **and never reach upstream** (verified against the upstream's own request recorder).
- **(b) topology hiding:** no `Server`/`Via`/`X-Powered-By`/`Location`/`X-Runtime` header and no internal host/port/path/stack-trace substring appears in any response header or body.
- **(c) error normalization:** an injected upstream `500` (leaking a stack trace + internal path) is normalized to opaque `502 {"error":"upstream_error","request_id":…}`, with the `request_id` echoed in an `X-Request-ID` response header for support correlation.
