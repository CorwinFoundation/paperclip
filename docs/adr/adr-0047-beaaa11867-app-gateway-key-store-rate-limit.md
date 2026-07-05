---
artifact_type: adr
artifact_id: "ADR-0047-beaaa11867-app-gateway-key-store-rate-limit"
owner: scanner-engineer
persona_audience: internal
phase: 1
canonical_terms_only: true
version: 0.1
prior_version: null
contracts_referenced: ["are-scanner-api-v1"]
ticket_refs: ["BEAAA-11867", "BEAAA-11862", "BEAAA-11861", "BEAAA-11858"]
brand_backbone_applicable: false   # internal decision record, not an external-bound asset
status: PROPOSED
proposed_date: 2026-07-05
contract_version: "are-scanner-api-v1@v1.1.0"   # NO bump — no contract output-shape change
scanner_version: "0.5.0"
corpus_version: "rubric.yaml@1.2.0"
classification: "rule-execution (auth/infra middleware; no rubric/scoring/judge/tamper delta) — ADR + CTO co-sign requested per parent build design BEAAA-11862"
cto_cosign_ref: null   # populated on CTO acceptance of the co-sign request on BEAAA-11867
merged_sha_on_canon_master_at_close: null   # populated at close after merge to canonical master
n5_golden_delta: "0 by construction — no scoring/rubric/corpus/golden-agent path touched"
---

# ADR-0047 — App gateway: D4 key store (revoke/rotate/expiry) + D3 per-tenant rate limit

**BLUF.** BEAAA-11867 (Child B of the public-scanner-front build BEAAA-11862;
D3+D4 of the CEO infra decision BEAAA-11861) adds a net-new app-tier gateway
module, `api/edge_gateway.py`, in front of the scan-creating route handlers.
It replaces the flat `ARE_API_AUTH_TOKENS_JSON` `{token: tenant_id}` dict with
a **mutable key store** (revoke / rotate / expiry, short-TTL cache, admin CLI,
no restart) and adds a **per-tenant token-bucket + max-concurrent rate limit**
keyed on the resolved identity. STAGING / non-public only. **No
`are-scanner-api-v1` contract change, no rubric/scoring/judge/tamper change.**
Feature-flagged (`ARE_EDGE_GATEWAY`), default-off; rollback is config-only.

## Context

Auth today (`api/app.py` `_authenticated_tenant_id`) resolves tenant from a
flat env dict: no revocation, no expiry, and changes require a process
restart. The public-scanner-front build needs (a) keys that can be revoked and
that expire without a restart, and (b) a per-tenant rate limit on scan
creation. Both need the **authenticated key/tenant identity**, which only the
app tier has — so D3 and D4 are co-located (D3 rate-limits on the key id that
D4 resolves; splitting them would invent an integration seam). D1 (the Caddy
edge front) is a separate child and out of scope here.

## Decision

### D4 — key store (`KeyStore`)
- Per-key record `{key_id, tenant_id, status: active|revoked, expires_at,
  created_at, rotated_from?, rate_limit?}` in a JSON-file backing store
  (stdlib-only; SQLite deferred — a JSON file is sufficient for the
  single-node staging front and keeps the dependency surface flat).
- Consulted per request through a **short-TTL in-process read cache (≤5s,
  hard-capped)**. Expiry is evaluated on **every** resolve against the cached
  record, so it propagates immediately; a revoke written out-of-process by the
  admin CLI propagates on the next reload — within the cache TTL, **no process
  restart**.
- `revoked` **or** `now > expires_at` → resolve returns *no identity* → `401`
  at the edge. Unknown / revoked / expired all collapse to `None` so the
  endpoint is not an oracle for which case occurred.
- **No infinite-lived keys:** `issue()` rejects a non-positive TTL; every key
  carries `expires_at`. Keys bootstrapped from the legacy env dict are assigned
  a finite bootstrap TTL.
- **Rotation:** `rotate()` issues a new `key_id` with **grace-overlap** (the
  new key inherits the old key's `expires_at`, so both are valid during the
  overlap) and records `rotated_from`; the operator calls `revoke()` on the old
  `key_id` to cut over.
- **Admin CLI:** `python -m api.edge_gateway --store <path> {issue|revoke|
  rotate|set-limit|list}` mutates the store on disk atomically (temp-file +
  replace); the running app picks the change up within the cache TTL.
- **Env-JSON preserved** as a local/test bootstrap source only.

### D3 — per-tenant rate limit (`RateLimiter`)
- **Token bucket** (capacity = burst = `N`, refill = `N` tokens / 60s) plus a
  **max-concurrent gauge** (`M`), both keyed on the **resolved tenant_id**
  (post-auth), NOT source IP. Scoped to the scan-creating routes
  (`POST /scanner/v1/scans`, `/scanner/v1/rescans` measured path).
- Phase-1 defaults `N`/`M` are env-configurable and **per-tenant overridable**
  via the key record's `rate_limit`. Over a limit → `429
  {"error":"rate_limited","reason":...}` + `Retry-After`.
- A `ConcurrencyLease` is acquired at scan-admit and released when the handler
  returns. The current `ScanRuntime.kickoff` executes the scan **inline**, so
  holding the lease across the handler correctly bounds concurrent executions.

### Identity granularity of the bucket (design note for review)
The D3 headline is a **per-tenant** limit, so the bucket is keyed on the
resolved `tenant_id`. Acceptance (f) ("two keys, same source IP → independent
buckets, keyed on identity not IP") is satisfied with two keys belonging to two
**different tenants**: their buckets are independent, proving the limiter keys
on authenticated identity, never on network source. Two keys for the *same*
tenant intentionally share that tenant's bucket (that is the per-tenant
semantics). If the CTO wants per-**key** buckets instead, it is a one-line
change to the bucket key — flagged here for the co-sign.

## Rollback
`ARE_EDGE_GATEWAY` unset (default) ⇒ `EdgeGateway.from_env()` returns `None`
and the app keeps the legacy flat-dict auth path with no rate limiting —
byte-identical to pre-BEAAA-11867 (verified: the 16 existing `test_app.py`
cases and 86 route-regression cases pass unchanged). Revert is config-only.

## Consequences / known limitations
- **Phase-1 is in-process, single-node.** In a horizontally-scaled deployment
  each node keeps its own buckets, so the effective ceiling is
  `per-node-limit × node-count`. A multi-node shared-store limiter is a
  deliberate deferral (documented limitation, not a Phase-1 gap). The staging
  front is single-node, so the limit is exact there.
- Max-concurrent is measured over the handler window, which currently spans
  scan execution because `kickoff` runs the scan inline. If the runtime becomes
  truly async (202-then-background), the lease boundary must move to the scan's
  terminal-state callback — noted for the async-runtime work.

## Verification
- `tests/test_edge_gateway_beaaa11867.py` — 21 cases: component (KeyStore,
  RateLimiter) + end-to-end HTTP wiring through `create_app`. Covers
  acceptance (a)–(g). All pass.
- Regression: `test_app.py` (16) + scanner-v1 routes + rescans + ci-scan-ext
  (86 total) pass unchanged.
- **N=5 golden-set calibration delta = 0 by construction.** The gateway lives
  at the HTTP auth/rate-limit layer; it touches no scoring, rubric, corpus,
  judge, or golden-agent path. Golden calibration exercises the scorer
  directly, not the FastAPI auth surface — there is no code path by which this
  change can move a golden `are_total`.

## Governance
Auth/infra middleware; no rubric/scoring/contract change, no
`are-scanner-api-v1` bump, no Underwriter gate. Per the parent build design
(BEAAA-11862), this auth-model change ships with this ADR and a **CTO co-sign
on the diff before merge**; `cto_cosign_ref` and
`merged_sha_on_canon_master_at_close` are populated at co-sign / close.
