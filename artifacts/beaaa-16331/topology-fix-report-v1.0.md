---
artifact_type: implementation_report
owner: scanner_engineer
persona_audience: internal
phase: 1
canonical_terms_only: true
contracts_referenced: ["are-scanner-api-v1@v1.3.0", "scanner-implementation-supervision-v1@v1.1"]
ticket_refs: ["BEAAA-16331", "BEAAA-16330", "BEAAA-16232", "BEAAA-15400", "BEAAA-15373"]
scanner_version: "0.5.0 (unchanged)"
corpus_version: "0.1.0 (unchanged)"
probe_set_version: "n/a — infra/edge only"
rubric_point_id: "n/a — no rubric/scoring/judge/API/corpus change"
n5_calibration_delta: "n/a — no scoring surface touched (edge nginx config + runbook + tests only)"
classification: "rule-execution (deployable edge integration under existing rubric/contract; no rule change)"
self_audit_passed: true
---

# BEAAA-16331 — Topology-correct Seq20 scanner.backbond.ai edge integration

BLUF: The Sequence 20 edge is re-shaped from a standalone nginx `server {}` block
(which would have created a parallel/conflicting TLS listener against the live
`scanner.backbond.ai` vhost — the defect that stopped the operator on
BEAAA-16232 `962f9417`) into two fragments that integrate **into** that one live
vhost with no new listener. Request-ID/redaction/allowlist/default-deny posture,
image identity, and rollback are preserved. No score/API/rubric/corpus change.
Not promoted; requires fresh QA `APPROVED` + fresh Founder sign-off.

## Deliverables

| Item | Value |
|---|---|
| Branch | `beaaa-16297-lanec-failopen-fix` (SHA-preserving; see issue comment for pushed commit SHA) |
| Deployable — http fragment | `infra/edge/nginx/backbond-scanner-edge.http.conf` · sha256 `840c63d9e84b9818ae7ecc63d86cb6d9951b890186ba186da95148f6b4806814` |
| Deployable — server include | `infra/edge/nginx/backbond-scanner-edge.server.conf.template` · sha256 `78c342c2ce35b2fc6fe95b5f2a9214f11f8751d0c266da151535e19150b865c1` |
| Retired (deleted) | `infra/edge/nginx/backbond-scanner-edge.conf.template` (standalone server block — the defect) |
| Runbook | `docs/deploy/sequence-20-production-deployment-runbook.md` v1.4 · sha256 `b3c491c84d73a59cf99f9b07ad69ef1dfee571608d2c76af943993ac8f090bdf` |
| Scanner image (retained/reused) | `ghcr.io/corwinfoundation/are-scanner@sha256:0b565dda1bb299287a35abaa7a82e5ce5788ca13f68f278e0007bfcb3d1e6a65` (unchanged; edge config is not in the image) |
| Rollback image (retained) | `backbond-scanner-live@sha256:7c4ff251dd0cc4d41b88e9e148094355985dc4a39e7ac4e93e280171c4101121` |

## The defect and the correction

- **Live topology (operator-verified):** one TLS vhost —
  `/etc/nginx/sites-enabled/scanner.backbond.ai -> /etc/nginx/sites-available/scanner.backbond.ai`.
- **Defect:** v1.3 installed a complete `server { listen ${EDGE_LISTEN}; server_name _; … }`
  into `conf.d/`, i.e. a second listener next to the real vhost.
- **Correction:** two fragments that declare **no** `listen`/`server_name`/`ssl_certificate`/`server {}`:
  - `…http.conf` — http-scope `log_format edge_json` (dropped in `conf.d/`); opens no socket.
  - `…server.conf.template` — server-scope include pulled **inside** the live vhost,
    replacing its scanner proxy location(s); only `${EDGE_UPSTREAM}` substituted.
  - `nginx` **rejects** the server fragment outside a `server {}` block, so it
    cannot be reinstalled standalone.

## Tests (reproduce defect, then validate correction)

Commands:
```
python3 -m pytest tests/integration/test_edge_nginx_topology_beaaa16331.py \
                  tests/integration/test_edge_nginx_request_id_beaaa16230.py -q
```
Result in this environment: **11 passed, 1 skipped** (the skipped test needs a
local `nginx` binary; Docker was available so the functional suite ran).

- `test_edge_nginx_topology_beaaa16331.py` (static, always runs): the deployable
  server fragment carries no `listen`/`server_name`/`server {}`/`ssl_certificate`/`${EDGE_LISTEN}`
  (defect reproduction — fails against the old standalone template); the legacy
  standalone template is removed; the two fragments together preserve the full
  reviewed posture; and (nginx-gated) they validate inside a synthesized vhost and
  are rejected at http scope.
- `test_edge_nginx_request_id_beaaa16230.py` (Docker): composes the two fragments
  as the runbook does — http fragment at http scope + `server { listen; include
  <server fragment>; }` — and proves ordinary 401/404 X-Request-ID, exact
  access-log correlation, authed create, upstream-5xx→opaque `upstream_error`, and
  topology-header redaction. All 6 passed.

Real-nginx cross-check (nginx:1.27-alpine, outside pytest): fragments **pass**
`nginx -t` inside a vhost; the server fragment used standalone at http scope is
**rejected** (`"location" directive is not allowed here`).

## Rollback (concise)

Step 1 backs up the **actual** `sites-available/scanner.backbond.ai` with `cp -aL`
(non-symlink, self-contained) and records the exact `sites-enabled` symlink
target and observed upstream. Step 7 restores that exact file (`cmp`-verified),
re-creates the symlink to the recorded target, removes the added http fragment
and server include, runs `nginx -t`, zero-downtime reloads, and re-pins the
retained rollback image `@sha256:7c4ff251…`.

## Manifest binding determination

**Yes — `artifacts/beaaa-16257/sequence-20-release-manifest.json` binds the
deployable nginx config** via `nginx_template_sha256`
(`49994c977a238…24cf`) and `reviewed_file_sha256['…backbond-scanner-edge.conf.template']`.
Because BEAAA-16331 replaces that standalone template with two fragments, the
signed manifest no longer covers the deployable surface. A revised **unsigned**
manifest input is provided at
`artifacts/beaaa-16331/revised-release-manifest.input.json`, recording the two
fragment SHA-256 values and the unchanged image digest. It explicitly carries
`"signed": false` / `manifest_signature.status: NOT_PRODUCED` — **no signature is
claimed.** A signer must finalize and sign a manifest that fills `commit_sha` +
`source_bundle_sha256` from the pushed commit and binds the fragment hashes before
any promotion. The scanner image `@sha256:0b565dda…` is unchanged and keeps its
BEAAA-16308 signature/provenance.

## Governance

Rule-execution (deployable edge integration under the existing rubric/contract).
No scoring/rubric/judge/API/corpus change → N=5 not applicable. Not promoted.
Production/public promotion requires exact-build QA `APPROVED` naming the pushed
commit + separate Founder sign-off under BEAAA-15373; the prior v4 Founder
confirmation authorized the prior identity only and does not carry to this changed
deployable surface.
