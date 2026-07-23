---
artifact_type: playbook
owner: scanner_engineer
persona_audience: founder
phase: 1
canonical_terms_only: true
focus_claim: "This playbook makes the Sequence 20 BackBond Scanner edge promotion reproducible, fail-closed, and immediately reversible by integrating request-ID/redaction/default-deny INTO the one live scanner.backbond.ai TLS vhost on srv1749248 — never a parallel listener."
contracts_referenced: ["are-scanner-api-v1@v1.3.0"]
ticket_refs: ["BEAAA-16331", "BEAAA-16330", "BEAAA-16232", "BEAAA-16257", "BEAAA-16308", "BEAAA-15400", "BEAAA-15373"]
proof_points_cited:
  - "Consolidated candidate identity: signed release manifest artifacts/beaaa-16257/sequence-20-release-manifest.json"
  - "Scanner image (unchanged, signed): ghcr.io/corwinfoundation/are-scanner@sha256:0b565dda1bb299287a35abaa7a82e5ce5788ca13f68f278e0007bfcb3d1e6a65"
  - "Retained rollback image (read back from the running container, BEAAA-16331): image ID sha256:7c4ff251dd0cc4d41b88e9e148094355985dc4a39e7ac4e93e280171c4101121, org.opencontainers.image.revision=37192e2b0e9f50416170da77dcbc189c00761ecc, env SCANNER_EDGE_IMAGE=sha256:7c4ff251…"
  - "Signed image provenance (offline archive, NOT re-pulled): tar sha256 d9f7203b79d42a77e17bfa292781250c7575d31f011ee03d79f9accbfbaf29ac, local RepoTag backbond-scanner-live:b1c577c74a127c1342617230689eb6f0ef9be6d2, org.opencontainers.image.revision=b1c577c…; GHCR provenance ghcr.io/corwinfoundation/are-scanner@sha256:0b565dda… (recorded, not inspected offline)"
  - "Deployable edge fragments: infra/edge/nginx/backbond-scanner-edge.http.conf + backbond-scanner-edge.server.conf.template"
  - "Live vhost integrated in place: /etc/nginx/sites-available/scanner.backbond.ai (sites-enabled symlink)"
  - "Live service/container/env contract: service backbond-scanner-live.service, container backbond-scanner-live-edge, env-file /etc/backbond/scanner-edge.env (NOT are-scanner-edge / /etc/are-scanner/secrets.env)"
version: 1.6
prior_version: "1.5 (docs/deploy/sequence-20-production-deployment-runbook.md@727447c — image/credential/rollback identity reconciled, but the live-vhost mutation was still an interactive `$EDITOR` step: non-deterministic and not automation-runnable)"
self_audit_passed: true
escalations_noted:
  - "v1.3 assumed a standalone /etc/nginx/conf.d/backbond-scanner-edge.conf server block. The live topology is ONE TLS vhost at /etc/nginx/sites-available/scanner.backbond.ai; installing a second server block would create a conflicting listener. The Sequence 20 operator stop (BEAAA-16232 962f9417) caught this. v1.4 integrates into the live vhost."
  - "v1.5 reconciles image/credential/rollback identity to the operator's fail-closed read-back (BEAAA-16331 local-board comments 2026-07-21): (a) /usr/local/sbin/box_deploy is ABSENT and its GHCR-RepoDigest re-verify cannot run after an offline `docker load` (Docker archives do not preserve the GHCR RepoDigest), so image identity is verified by tar sha256 + loaded local RepoTag + org.opencontainers.image.revision, with the GHCR digest retained as provenance only; (b) the live service/container/env names are backbond-scanner-live.service / backbond-scanner-live-edge / /etc/backbond/scanner-edge.env, not the older are-scanner defaults; (c) rollback identity is the read-back image ID sha256:7c4ff251… (revision 37192e2b…), not a remembered tag; (d) two DISTINCT active non-customer/non-vapi Tenant A/B scoped credentials are NOT staged in /var/lib/are-scanner/edge/keys.json and must be provisioned fail-closed at mode 0600 before the tenant preflight can pass."
  - "The deployable nginx surface changed (standalone template -> two fragments) AND this runbook is now v1.6, so the release manifest (artifacts/beaaa-16257/sequence-20-release-manifest.json) MUST bind the two fragment SHA-256 values + this runbook's v1.6 SHA-256, then be RE-SIGNED. The re-input manifest now records the two fragment hashes and the v1.6 runbook SHA-256 (artifacts/beaaa-16331/revised-release-manifest.input.json also binds the offline archive tar sha256 + local tag/revision + rollback identity), but it is UNSIGNED (manifest_signature.status=NOT_PRODUCED) — re-signing is a CTO/Founder action; this task does NOT claim a signature it cannot produce. promotion_authorized stays false."
  - "v1.6 removes the interactive `$EDITOR` from the live-vhost mutation (BEAAA-16331 local-board 911fcd68 item 5): the vhost include is now applied by a deterministic, idempotent, hash-guarded transform (Step 2) that fail-closes unless the staged copy matches the operator-recorded pre-image hash LIVE_VHOST_SHA256, removes the scanner proxy location blocks, inserts exactly one include, emits the exact unified diff to the receipt, and records vhost_pre/vhost_post SHA-256. Re-running the transform logic is a no-op. This makes the mutation fully automation-runnable and reviewable before execution."
  - "Production/public promotion requires exact-build QA APPROVED naming this runbook commit SHA and separate Founder sign-off under BEAAA-15373. v4 Founder confirmation authorized the prior identity only and does NOT carry to this changed deployable surface. No production mutation is performed by this task."
---

# Sequence 20 production deployment runbook (v1.6 — topology-, identity-, and automation-correct)

## Why v1.6 supersedes v1.5

v1.5 reconciled the image, credential, and rollback identity to the operator
read-back, but the live-vhost mutation (Step 2) was still an interactive
`$EDITOR "$STAGE"` step. The operator's independent review (BEAAA-16331
local-board `911fcd68`, item 5, **no mutation**) flagged that as
**non-deterministic and not automation-runnable**. v1.6 replaces it with a
deterministic, idempotent, **hash-guarded** transform that:

- refuses to run unless the staged copy's SHA-256 equals the operator-recorded
  pre-image `LIVE_VHOST_SHA256` (fail-closed on any vhost drift), cross-checked
  against the Step-1 backup copy;
- removes the vhost's scanner proxy `location` blocks and inserts **exactly one**
  `include /etc/nginx/backbond-scanner-edge.server.conf;` after the
  `listen`/`server_name`/`ssl_certificate` set — re-applying the transform logic
  to already-transformed content is a no-op;
- emits the exact unified diff (config only, no secrets) and records
  `vhost_pre`/`vhost_post` SHA-256 into the receipt.

Nothing else changes from v1.5. The nginx topology fix (v1.4) and the
image/credential/rollback reconciliation (v1.5) are carried forward verbatim.

## Why v1.5 superseded v1.4

v1.4 fixed the nginx topology but described the image, credential, and rollback
identity from remembered values. The operator's fail-closed read-back on
`srv1749248` (BEAAA-16331 local-board, 2026-07-21, **no mutation**) proved four
things v1.4 could not execute against:

1. **No `box_deploy`, no GHCR-digest re-verify offline.** `/usr/local/sbin/box_deploy`
   is **absent**; the only installed helpers are `backbond-ensure-docker-networking`,
   `backbond-scanner-live-wait`, and firewall/bind helpers. The signed image
   arrives as an **offline Docker archive** — and `docker load` does **not** restore
   the GHCR `RepoDigest`, so `docker image inspect ghcr.io/…@sha256:0b565dda…`
   fails after load. Image identity must be verified by **tar sha256 + loaded
   local RepoTag + `org.opencontainers.image.revision`**, with the GHCR digest kept
   as provenance only (Step 4).
2. **Live service/container/env names.** service `backbond-scanner-live.service`,
   container `backbond-scanner-live-edge`, env-file `/etc/backbond/scanner-edge.env`
   — the older `are-scanner-edge` / `/etc/are-scanner/secrets.env` defaults are wrong.
3. **Rollback identity is a read-back, not a tag.** The running container is image
   ID `sha256:7c4ff251…` (revision `37192e2b…`); `/etc/backbond/scanner-edge.env`
   binds `SCANNER_EDGE_IMAGE=sha256:7c4ff251…`; the container `Config.Image` is that
   image ID. Rollback validates against the image ID + revision label (Step 7).
4. **Tenant A/B credentials are not staged.** `/var/lib/are-scanner/edge/keys.json`
   holds 5 records / 4 tenants but only **1** active and **2** bound to `vapi`; the
   two DISTINCT active non-customer/non-vapi Tenant A/B scoped tokens the preflight
   needs are **not present**, and the store is world-readable (`0644`). Step 0
   provisions them fail-closed at `0600` before the preflight can pass.

The nginx topology fix from v1.4 is unchanged and carried forward verbatim.

## Why v1.4 superseded v1.3

v1.3 installed the edge as a standalone `server {}` block into
`/etc/nginx/conf.d/backbond-scanner-edge.conf`. The operator's fail-closed
pre-mutation inventory (BEAAA-16232, `962f9417`) proved the live topology is a
**single** TLS vhost:

```text
/etc/nginx/sites-enabled/scanner.backbond.ai  ->  /etc/nginx/sites-available/scanner.backbond.ai
```

A second `server { listen … ssl; server_name _; }` block would have stood up a
**parallel, conflicting listener** on the same address. v1.4 instead integrates
the request-ID / redaction / allowlist / default-deny posture **into** that one
vhost. The deployable surface is now two fragments that declare **no** `listen`,
`server_name`, `ssl_certificate`, or `server {}` wrapper:

- `infra/edge/nginx/backbond-scanner-edge.http.conf` — http-scope `log_format`.
- `infra/edge/nginx/backbond-scanner-edge.server.conf.template` — server-scope
  `include`, pulled **inside** the live vhost, replacing its scanner proxy
  location(s). Only `${EDGE_UPSTREAM}` is substituted.

`nginx` rejects the server fragment if it is used outside a `server {}` block, so
it structurally cannot be reinstalled as a standalone listener
(`tests/integration/test_edge_nginx_topology_beaaa16331.py`).

## Decision and authority

This is an execution procedure, **not** promotion authority. Before step 1 the
originating issue must contain:

1. QA Engineer comment with the exact word `APPROVED`, naming this runbook's
   commit SHA;
2. Founder production sign-off under BEAAA-15373 for **this** changed deployable
   surface (the prior v4 confirmation covered the prior identity only); and
3. a **signed** release manifest that binds this runbook commit and the SHA-256 of
   both edge fragments, plus the repository-qualified scanner image digest
   `ghcr.io/corwinfoundation/are-scanner@sha256:0b565dda…a65`.

If any item is absent, names a different SHA, or the manifest predates the
fragments, **stop**. Secrets stay in the root-only `srv1749248` channel; never
paste secret values into shell history, this runbook, logs, or Paperclip.

## Topology (target state)

```text
Internet client
    |  HTTPS :443  scanner.backbond.ai
    v
srv1749248 host nginx
    - ONE TLS vhost: /etc/nginx/sites-available/scanner.backbond.ai
      (sites-enabled symlink) — listen/ssl_certificate/server_name UNCHANGED
    - http-scope log_format edge_json: /etc/nginx/conf.d/backbond-scanner-edge.http.conf
    - server-scope include (inside the vhost, replaces scanner proxy locations):
      include /etc/nginx/backbond-scanner-edge.server.conf;
    - X-Request-ID generated, returned on every response, and logged here
    |  HTTP loopback to the vhost's existing proxy target (EDGE_UPSTREAM)
    v
systemd unit backbond-scanner-live.service
    -> digest-pinned scanner image @sha256:0b565dda…a65
```

The vhost's TLS listener, certificate, and `server_name` are **left exactly as
they are**. This change only (a) adds one http-scope `log_format` file and (b)
replaces the vhost's scanner proxy location set with the server include. No new
listener, certificate, or `server_name` is introduced.

## Fixed paths and operator inputs

Run on `srv1749248` as the authorized operator. Root-owned input file
`/etc/backbond/sequence-20-production.env`, mode `0600`:

```text
CANDIDATE_COMMIT=<this runbook's pushed commit SHA, from the signed manifest>
# GHCR provenance of the signed image — RECORDED, never `docker image inspect`ed
# after an offline load (the RepoDigest is not restored by `docker load`).
PROVENANCE_GHCR_IMAGE=ghcr.io/corwinfoundation/are-scanner@sha256:0b565dda1bb299287a35abaa7a82e5ce5788ca13f68f278e0007bfcb3d1e6a65
SOURCE_BUNDLE=/var/tmp/are-scanner-<commit>-source.tar.gz
SOURCE_BUNDLE_SHA256=<sha256 recorded in the signed release manifest>
RELEASE_MANIFEST=/var/tmp/sequence-20-release-manifest.json
RELEASE_MANIFEST_SIG=/var/tmp/sequence-20-release-manifest.json.sig
RELEASE_MANIFEST_PUBKEY=/var/tmp/sequence-20-release-manifest.pub.pem
LIVE_VHOST=/etc/nginx/sites-available/scanner.backbond.ai
LIVE_VHOST_LINK=/etc/nginx/sites-enabled/scanner.backbond.ai
# Pre-image hash of the live vhost, recorded from the operator read-back inventory.
# The Step-2 transform fail-closes unless the staged copy matches this exactly.
LIVE_VHOST_SHA256=<sha256 of /etc/nginx/sites-available/scanner.backbond.ai at read-back>
EDGE_UPSTREAM=<the app-tier host:port THIS vhost already proxies to>
EDGE_ACCESS_LOG=/var/log/nginx/scanner-backbond-edge.access.log
BACKBOND_ACCEPTANCE_BASE_URL=https://scanner.backbond.ai
# Live service/container/env contract (read back from the box) — NOT are-scanner-*.
SCANNER_SERVICE=backbond-scanner-live.service
SCANNER_CONTAINER=backbond-scanner-live-edge
SCANNER_EDGE_ENV=/etc/backbond/scanner-edge.env
# Signed image as an OFFLINE archive (no registry pull; box has default-deny egress).
SCANNER_TARBALL=/var/tmp/backbond-scanner-live-b1c577c.tar
SCANNER_TARBALL_SHA256=d9f7203b79d42a77e17bfa292781250c7575d31f011ee03d79f9accbfbaf29ac
SCANNER_LOCAL_TAG=backbond-scanner-live:b1c577c74a127c1342617230689eb6f0ef9be6d2
SCANNER_OCI_REVISION=b1c577c74a127c1342617230689eb6f0ef9be6d2
# Rollback identity — read back from the RUNNING container, not remembered.
ROLLBACK_IMAGE_ID=sha256:7c4ff251dd0cc4d41b88e9e148094355985dc4a39e7ac4e93e280171c4101121
ROLLBACK_OCI_REVISION=37192e2b0e9f50416170da77dcbc189c00761ecc
# Tenant preflight credentials — file-backed key-store + root-only cleartext custody.
KEY_STORE=/var/lib/are-scanner/edge/keys.json
TEST_TENANT_ID=<Tenant A: provisioned non-customer, non-vapi tenant UUID>
TEST_TENANT_ID_B=<Tenant B: a SECOND distinct non-customer, non-vapi tenant UUID>
TOKEN_A_REF=file:/etc/credstore.encrypted#<tenant-a-scoped-token>
TOKEN_B_REF=file:/etc/credstore.encrypted#<tenant-b-scoped-token>
SYNTH_ENDPOINT_SECRET_REF=file:/etc/credstore.encrypted#<synthetic-endpoint-secret>
```

`EDGE_UPSTREAM` is **not** invented: it is read from the live vhost during backup
(step 1) and must match what the vhost proxies to today (the change preserves the
existing upstream). `PROVENANCE_GHCR_IMAGE`, `SCANNER_TARBALL_SHA256`, and
`SOURCE_BUNDLE_SHA256` must come verbatim from the signed manifest. The manifest
must be **re-signed** to bind this v1.6 runbook SHA-256 and both edge-fragment
SHA-256 values before promotion; the manifest under `artifacts/beaaa-16257/` is a
re-input (unsigned) until the CTO/Founder signing step runs — do **not** deploy
against it while `promotion_authorized` is `false` or the signature is absent.
Verify the manifest and source bundle on an egress-capable operator machine
before any box-side step:

```sh
openssl dgst -sha256 \
  -verify artifacts/beaaa-16257/sequence-20-release-manifest.pub.pem \
  -signature artifacts/beaaa-16257/sequence-20-release-manifest.json.sig \
  artifacts/beaaa-16257/sequence-20-release-manifest.json
# manifest must bind THIS v1.6 runbook SHA-256 and both edge-fragment SHA-256
# values; if it does not (or is unsigned / promotion_authorized=false), stop —
# the manifest predates the topology+identity fix.
```

Load and validate inputs without printing the token:

```sh
sudo sh -eu <<'SH'
. /etc/backbond/sequence-20-production.env
# GHCR provenance is a well-formed reference — recorded, never inspected offline.
printf '%s\n' "$PROVENANCE_GHCR_IMAGE" | grep -Eq '^ghcr\.io/corwinfoundation/are-scanner@sha256:[0-9a-f]{64}$'
[ "${PROVENANCE_GHCR_IMAGE##*@}" = 'sha256:0b565dda1bb299287a35abaa7a82e5ce5788ca13f68f278e0007bfcb3d1e6a65' ]
printf '%s\n' "$BACKBOND_ACCEPTANCE_BASE_URL" | grep -Eq '^https://scanner\.backbond\.ai/?$'
# Both tenant tokens are credential references; two DISTINCT non-vapi tenants.
printf '%s\n' "$TOKEN_A_REF" | grep -Eq '^(credstore|file|env|vault):'
printf '%s\n' "$TOKEN_B_REF" | grep -Eq '^(credstore|file|env|vault):'
[ "$TOKEN_A_REF" != "$TOKEN_B_REF" ]
# Offline archive + rollback identity are well-formed.
printf '%s\n' "$SCANNER_TARBALL_SHA256"  | grep -Eq '^[0-9a-f]{64}$'
printf '%s\n' "$SCANNER_OCI_REVISION"    | grep -Eq '^[0-9a-f]{40}$'
printf '%s\n' "$ROLLBACK_IMAGE_ID"       | grep -Eq '^sha256:[0-9a-f]{64}$'
printf '%s\n' "$ROLLBACK_OCI_REVISION"   | grep -Eq '^[0-9a-f]{40}$'
[ "$SCANNER_OCI_REVISION" != "$ROLLBACK_OCI_REVISION" ]   # deploy != rollback build
test -f "$SOURCE_BUNDLE" -a -f "$RELEASE_MANIFEST" -a -f "$RELEASE_MANIFEST_SIG" -a -f "$RELEASE_MANIFEST_PUBKEY"
openssl dgst -sha256 -verify "$RELEASE_MANIFEST_PUBKEY" -signature "$RELEASE_MANIFEST_SIG" "$RELEASE_MANIFEST"
printf '%s  %s\n' "$SOURCE_BUNDLE_SHA256" "$SOURCE_BUNDLE" | sha256sum -c -
[ "$(jq -r .commit_sha "$RELEASE_MANIFEST")" = "$CANDIDATE_COMMIT" ]
[ "$(jq -r .image_digest "$RELEASE_MANIFEST")" = "${PROVENANCE_GHCR_IMAGE##*@}" ]
[ "$(jq -r .source_bundle_sha256 "$RELEASE_MANIFEST")" = "$SOURCE_BUNDLE_SHA256" ]
# The manifest must be re-signed and promotion-authorized before any deploy.
[ "$(jq -r .promotion_authorized "$RELEASE_MANIFEST")" = 'true' ]
[ -n "$TEST_TENANT_ID" ]   && [ "$TEST_TENANT_ID"   != vapi ]
[ -n "$TEST_TENANT_ID_B" ] && [ "$TEST_TENANT_ID_B" != vapi ] && [ "$TEST_TENANT_ID_B" != "$TEST_TENANT_ID" ]
test -L "$LIVE_VHOST_LINK"
test -f "$LIVE_VHOST"
# The live-vhost pre-image hash must be a well-formed sha256 (Step-2 hash guard).
printf '%s\n' "$LIVE_VHOST_SHA256" | grep -Eq '^[0-9a-f]{64}$'
SH
```

## 0. Provision two Tenant A/B scoped credentials (fail-closed, non-echoing)

The operator read-back proved the live file-backed key-store
`/var/lib/are-scanner/edge/keys.json` holds 5 records across 4 tenants but only
**1** active and **2** bound to `vapi`; the two DISTINCT active non-customer,
non-`vapi` Tenant A/B credentials the preflight (Step 5) requires are **not**
staged, and the store is world-readable (`0644`, `are-scanner:are-scanner`) even
though bearer tokens are JSON object keys. Provision them before anything else.

Generate two cryptographically random scoped tokens, insert them **active** for
`TEST_TENANT_ID` (A) and `TEST_TENANT_ID_B` (B), tighten the store to `0600`
with ownership the container can still read, and place the cleartext only in
root-only encrypted custody. **Never echo a token**; post only key IDs/aliases
and aggregate counts to Paperclip.

```sh
sudo sh -eu <<'SH'
. /etc/backbond/sequence-20-production.env
umask 077
[ -f "$KEY_STORE" ]
# Mint two DISTINCT random tokens without ever printing them.
TOK_A="$(openssl rand -hex 32)"; TOK_B="$(openssl rand -hex 32)"
[ "$TOK_A" != "$TOK_B" ]
# Stable, non-secret aliases for the receipt (hash prefix only — not the token).
ALIAS_A="tenantA-$(printf '%s' "$TOK_A" | sha256sum | cut -c1-12)"
ALIAS_B="tenantB-$(printf '%s' "$TOK_B" | sha256sum | cut -c1-12)"

# Fail-closed edit of the JSON store: add two ACTIVE, tenant-scoped records whose
# JSON KEY is the bearer token. Written to a 0600 temp then atomically installed;
# jq --arg keeps the token out of argv history and process listings is avoided by
# passing via env, not the command line.
TMP="$(mktemp)"; chmod 0600 "$TMP"
TOK_A="$TOK_A" TOK_B="$TOK_B" \
ALIAS_A="$ALIAS_A" ALIAS_B="$ALIAS_B" \
TID_A="$TEST_TENANT_ID" TID_B="$TEST_TENANT_ID_B" \
jq -n --slurpfile cur "$KEY_STORE" '
  ($cur[0] // {}) as $store
  | $store
  + { ($ENV.TOK_A): {tenant_id:$ENV.TID_A, alias:$ENV.ALIAS_A, active:true, scope:"scanner-edge", origin:"seq20-preflight"} }
  + { ($ENV.TOK_B): {tenant_id:$ENV.TID_B, alias:$ENV.ALIAS_B, active:true, scope:"scanner-edge", origin:"seq20-preflight"} }
' > "$TMP"
# Structural fail-closed checks BEFORE swap: exactly the two new active records
# for A and B, both non-vapi and distinct.
[ "$(jq --arg a "$TEST_TENANT_ID" '[to_entries[]|select(.value.tenant_id==$a and .value.active==true)]|length' "$TMP")" -ge 1 ]
[ "$(jq --arg b "$TEST_TENANT_ID_B" '[to_entries[]|select(.value.tenant_id==$b and .value.active==true)]|length' "$TMP")" -ge 1 ]
[ "$(jq '[to_entries[]|select(.value.tenant_id=="vapi" and .value.active==true)]|length' "$TMP")" -eq "$(jq '[to_entries[]|select(.value.tenant_id=="vapi" and .value.active==true)]|length' "$KEY_STORE")" ]
# Preserve container-readable ownership, tighten mode from 0644 -> 0600.
OWNER="$(stat -c '%U:%G' "$KEY_STORE")"
install -o "${OWNER%%:*}" -g "${OWNER##*:}" -m 0600 "$TMP" "$KEY_STORE"
rm -f "$TMP"
[ "$(stat -c '%a' "$KEY_STORE")" = 600 ]

# Root-only encrypted custody of the cleartext tokens (for later resolution as
# TOKEN_A/TOKEN_B); the synthetic endpoint secret is staged the same way.
printf 'tenant_a %s %s\ntenant_b %s %s\n' "$ALIAS_A" "$TOK_A" "$ALIAS_B" "$TOK_B" \
  | systemd-creds encrypt --name=seq20-tenant-tokens - /etc/credstore.encrypted/seq20-tenant-tokens
unset TOK_A TOK_B
printf 'PROVISIONED key_store_mode=0600 tenant_a_alias=%s tenant_b_alias=%s\n' "$ALIAS_A" "$ALIAS_B"
SH
```

Confirm the app picks the new records up within the key-store cache TTL (read
back as **5 s** by the operator) — poll, do not sleep-and-hope:

```sh
sudo sh -eu <<'SH'
. /etc/backbond/sequence-20-production.env
i=0; until [ "$i" -ge 8 ]; do
  # health/SLA stays 200 (cache reload is non-disruptive); token validity is
  # asserted in Step 5 over the real vhost so no token is placed on this line.
  curl -fsS --max-time 5 --resolve scanner.backbond.ai:443:127.0.0.1 \
    -o /dev/null -w '' https://scanner.backbond.ai/scanner/v1/sla && break
  i=$((i+1)); sleep 1
done
[ "$i" -lt 8 ]
SH
```

Pass: the key-store is mode `0600`, holds one active record for each of Tenant A
and Tenant B (neither `vapi`), the `vapi`-active count is unchanged, the cleartext
lives only in `/etc/credstore.encrypted`, and health stays `200` across the TTL
window. The `SYNTH_ENDPOINT_SECRET_REF` synthetic endpoint secret must likewise be
present in `/etc/credstore.encrypted`; stage it here if absent. No customer token
is created, rotated, or logged.

## 1. Retain the exact rollback state (real site file + symlink, self-contained)

```sh
sudo sh -eu <<'SH'
. /etc/backbond/sequence-20-production.env
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP="/etc/nginx/backbond-backups/sequence-20-${STAMP}"
install -d -o root -g root -m 0700 "$BACKUP"

# Backup the ACTUAL sites-available file, dereferenced and self-contained.
cp -aL "$LIVE_VHOST" "$BACKUP/scanner.backbond.ai"
test ! -L "$BACKUP/scanner.backbond.ai"
cmp -s "$LIVE_VHOST" "$BACKUP/scanner.backbond.ai"

# Record the exact sites-enabled symlink target so rollback restores it verbatim.
readlink "$LIVE_VHOST_LINK" > "$BACKUP/sites-enabled-target.txt"
printf '%s\n' "$LIVE_VHOST"      > "$BACKUP/sites-available-path.txt"
printf '%s\n' "$LIVE_VHOST_LINK" > "$BACKUP/sites-enabled-path.txt"

# Capture the upstream the live vhost proxies to (do not invent one downstream).
grep -Eo 'proxy_pass[[:space:]]+https?://[^;]+' "$BACKUP/scanner.backbond.ai" \
  > "$BACKUP/observed-proxy-pass.txt" || true

# Retained image identity for rollback — read back from the running container,
# validated by IMAGE ID + org.opencontainers.image.revision (NOT a GHCR digest;
# the offline-loaded image carries no RepoDigest).
docker inspect --format '{{index .Config.Image}}' "$SCANNER_CONTAINER" \
  > "$BACKUP/prior-image.txt" 2>/dev/null || true
[ "$(cat "$BACKUP/prior-image.txt")" = "$ROLLBACK_IMAGE_ID" ]
printf '%s\n' "$ROLLBACK_IMAGE_ID" > "$BACKUP/required-rollback-image.txt"
docker image inspect "$ROLLBACK_IMAGE_ID" >/dev/null
[ "$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$ROLLBACK_IMAGE_ID")" = "$ROLLBACK_OCI_REVISION" ]

printf '%s\n' "$BACKUP" > /etc/backbond/sequence-20-active-backup
SH
```

Pass: the backup holds a non-symlink copy of the live vhost that `cmp`s equal to
it, the recorded symlink target, and the exact rollback image is locally
inspectable. Otherwise stop before any mutation.

## 2. Install the fragments from the inbound source bundle

No server-side fetch or registry pull. Extract the checksum-bound source bundle,
verify each fragment against the signed manifest, then install: the http fragment
at http scope, and the rendered server fragment as the vhost include.

```sh
sudo sh -eu <<'SH'
. /etc/backbond/sequence-20-production.env
SRC=/opt/backbond/sequence-20-${CANDIDATE_COMMIT}
install -d -o root -g root -m 0755 "$SRC"
tar --extract --gzip --file "$SOURCE_BUNDLE" --directory "$SRC" --strip-components=1

HTTP_SRC="$SRC/infra/edge/nginx/backbond-scanner-edge.http.conf"
SERVER_SRC="$SRC/infra/edge/nginx/backbond-scanner-edge.server.conf.template"
printf '%s  %s\n' "$(jq -r '.edge_http_fragment_sha256' "$RELEASE_MANIFEST")" "$HTTP_SRC"   | sha256sum -c -
printf '%s  %s\n' "$(jq -r '.edge_server_fragment_sha256' "$RELEASE_MANIFEST")" "$SERVER_SRC" | sha256sum -c -

# (a) http-scope log_format fragment -> conf.d (adds no server/listener).
install -o root -g root -m 0644 "$HTTP_SRC" /etc/nginx/conf.d/backbond-scanner-edge.http.conf

# (b) render the server include; substitute ONLY EDGE_UPSTREAM, keep $request_id
#     literal, and point access_log at the edge log path.
export EDGE_UPSTREAM
envsubst '${EDGE_UPSTREAM}' < "$SERVER_SRC" > /etc/nginx/backbond-scanner-edge.server.conf
sed -i "s#access_log[[:space:]]\+\S\+[[:space:]]\+edge_json;#access_log ${EDGE_ACCESS_LOG} edge_json;#" \
  /etc/nginx/backbond-scanner-edge.server.conf
grep -F 'add_header X-Request-ID $request_id always;' /etc/nginx/backbond-scanner-edge.server.conf >/dev/null
! grep -Eq '(^|[^#].*)\b(listen|server_name|ssl_certificate)\b' /etc/nginx/backbond-scanner-edge.server.conf
chown root:root /etc/nginx/backbond-scanner-edge.server.conf
chmod 0644 /etc/nginx/backbond-scanner-edge.server.conf
SH
```

Now integrate the include **into** the one live vhost with a deterministic,
idempotent, **hash-guarded** transform — never an interactive editor. The
transform refuses to run unless the staged copy matches the operator-recorded
pre-image `LIVE_VHOST_SHA256` (fail-closed on any drift), removes the vhost's
scanner proxy `location` blocks, inserts the include exactly once, writes the
exact unified diff to the receipt, and records the pre/post SHA-256. Re-applying
the transform logic to already-transformed content is a no-op.

```sh
sudo sh -eu <<'SH'
. /etc/backbond/sequence-20-production.env
BACKUP="$(cat /etc/backbond/sequence-20-active-backup)"
STAGE="$(mktemp /etc/nginx/.scanner-vhost.XXXXXX)"
cp -aL "$LIVE_VHOST" "$STAGE"

# (bound by hash) refuse unless the live vhost is the exact reviewed pre-image,
# cross-checked against the Step-1 backup copy. Any drift stops the run.
PRE="$(sha256sum < "$STAGE" | cut -d' ' -f1)"
[ "$PRE" = "$LIVE_VHOST_SHA256" ]
[ "$PRE" = "$(sha256sum < "$BACKUP/scanner.backbond.ai" | cut -d' ' -f1)" ]

# Deterministic, idempotent transform: delete the scanner proxy location blocks,
# insert exactly one include after the listen/server_name/ssl_certificate set.
# No interactive editor; the same input always yields the same output.
python3 - "$STAGE" "$EDGE_UPSTREAM" <<'PY'
import re, sys
path, upstream = sys.argv[1], sys.argv[2]
src = open(path).read()
INCLUDE = 'include /etc/nginx/backbond-scanner-edge.server.conf;'
if len(re.findall(r'(?m)^\s*server\s*\{', src)) != 1:
    sys.exit('FAIL: expected exactly one server block')

def blocks(text, header):
    out = []
    for m in re.finditer(header, text):
        i = text.index('{', m.start()); depth = 0; j = i
        while j < len(text):
            if text[j] == '{': depth += 1
            elif text[j] == '}':
                depth -= 1
                if depth == 0: break
            j += 1
        out.append((m.start(), j + 1))
    return out

# Remove location blocks that proxy the scanner API to the app tier.
spans = []
for s, e in blocks(src, r'(?m)^[ \t]*location\b[^\{]*\{'):
    body = src[s:e]
    if 'proxy_pass' in body and (upstream in body or '/scanner/' in body
                                 or '/scan' in body or re.search(r'location\s+/\s*\{', body)):
        spans.append((s, e))
for s, e in sorted(spans, reverse=True):
    ls = src.rfind('\n', 0, s) + 1          # swallow leading indent
    en = e
    while en < len(src) and src[en] in ' \t': en += 1
    if en < len(src) and src[en] == '\n': en += 1   # and the trailing newline
    src = src[:ls] + src[en:]

# Insert the include exactly once (idempotent: skip if already present).
if INCLUDE not in src:
    anchor = None
    for m in re.finditer(r'(?m)^[ \t]*(ssl_certificate_key|ssl_certificate|server_name|listen)\b[^\n]*\n', src):
        anchor = m.end()
    if anchor is None:
        sys.exit('FAIL: no listen/server_name/ssl_certificate anchor directive')
    src = src[:anchor] + '    ' + INCLUDE + '\n' + src[anchor:]

open(path, 'w').write(src)
PY

# Exact unified diff to the receipt (config only, no secrets).
diff -u "$BACKUP/scanner.backbond.ai" "$STAGE" > "$BACKUP/vhost-include.diff" || true

# Fail-closed structural checks on the staged vhost:
#  - still exactly ONE server block and ONE listen directive set (no new listener);
#  - the include appears exactly once; no scanner proxy location remains.
[ "$(grep -Ec '^[[:space:]]*server[[:space:]]*\{' "$STAGE")" = 1 ]
[ "$(grep -Fc 'include /etc/nginx/backbond-scanner-edge.server.conf;' "$STAGE")" = 1 ]
! grep -Eq '^[[:space:]]*location[[:space:]]+/scanner/' "$STAGE"

install -o root -g root -m 0644 "$STAGE" "$LIVE_VHOST"
POST="$(sha256sum < "$LIVE_VHOST" | cut -d' ' -f1)"
printf 'vhost_pre=%s vhost_post=%s\n' "$PRE" "$POST" > "$BACKUP/vhost-transform-hashes.txt"
rm -f "$STAGE"
# sites-enabled symlink is unchanged and still points at the same target.
test -L "$LIVE_VHOST_LINK"
[ "$(readlink "$LIVE_VHOST_LINK")" = "$(cat "$BACKUP/sites-enabled-target.txt")" ]
SH
```

Pass: the staged vhost matched the recorded pre-image hash, the transform removed
the scanner proxy locations and inserted exactly one include, the staged vhost
has exactly one server block and one listener set, the unified diff and pre/post
hashes are captured in the backup, it installs cleanly, and the sites-enabled
symlink target is unchanged. Any deviation — hash mismatch, missing anchor, more
than one server block — stops the run before `nginx -t`.

## 3. Validate and zero-downtime reload nginx

```sh
sudo nginx -t
sudo systemctl reload nginx
sudo systemctl is-active --quiet nginx
sudo journalctl -u nginx -n 20 --no-pager
```

Pass: `nginx -t` prints both `syntax is ok` and `test is successful` (a duplicate
listener or duplicate `location` would fail here); reload and `is-active` exit
`0`; the journal shows no reload error. Any failure triggers the rollback below
**before** proceeding.

## 4. Confirm the scanner image identity (offline, no registry, no `box_deploy`)

The app container is **not swapped** by this edge fix; this step only *proves the
running image identity* by read-back so rollback (Step 7) is anchored to a real
value. The box runs default-deny egress, `/usr/local/sbin/box_deploy` is absent,
and an offline `docker load` does **not** restore the GHCR `RepoDigest` — so
identity is verified by **image ID + `org.opencontainers.image.revision`**, never
`docker image inspect ghcr.io/…@sha256:0b565dda…` (that reference will not exist
after an offline load). Do **not** re-pull.

```sh
sudo sh -eu <<'SH'
. /etc/backbond/sequence-20-production.env
# Running container identity by ID + revision label (NOT the GHCR ref).
RUN_IMG="$(docker inspect --format '{{index .Config.Image}}' "$SCANNER_CONTAINER")"
[ "$RUN_IMG" = "$ROLLBACK_IMAGE_ID" ]
docker image inspect "$RUN_IMG" >/dev/null
[ "$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$RUN_IMG")" = "$ROLLBACK_OCI_REVISION" ]
# Env-file binds the same image ID (not a remembered tag).
grep -Fxq "SCANNER_EDGE_IMAGE=$ROLLBACK_IMAGE_ID" "$SCANNER_EDGE_ENV"
systemctl is-active --quiet "$SCANNER_SERVICE"
SH
```

Pass: the running container `backbond-scanner-live-edge` is image ID
`sha256:7c4ff251…` with revision `37192e2b…`, `/etc/backbond/scanner-edge.env`
binds that same image ID, and `backbond-scanner-live.service` is active. A
mismatch is a stop condition (the app image is out of scope for this edge change).

> **If a signed-image activation is ever authorized (separate task, not this
> one):** load the offline archive with a checksum-bound, reviewed loader — or the
> inline equivalent below — and verify **tar sha256 → loaded local RepoTag →
> revision label**, retaining the GHCR digest as provenance only. Never require a
> GHCR `docker image inspect` after load.
>
> ```sh
> printf '%s  %s\n' "$SCANNER_TARBALL_SHA256" "$SCANNER_TARBALL" | sha256sum -c -
> docker load -i "$SCANNER_TARBALL"                 # -> "Loaded image: backbond-scanner-live:b1c577c…"
> docker image inspect "$SCANNER_LOCAL_TAG" >/dev/null
> [ "$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$SCANNER_LOCAL_TAG")" = "$SCANNER_OCI_REVISION" ]
> # GHCR digest $PROVENANCE_GHCR_IMAGE is recorded as provenance; NOT inspected here.
> # Activation would then set SCANNER_EDGE_IMAGE in $SCANNER_EDGE_ENV and restart
> # $SCANNER_SERVICE — requiring its own QA + Founder sign-off.
> ```

## 5. Preflight: real scanner.backbond.ai request selects the integration

Prove the request-ID/redaction/default-deny configuration is actually on the path
for real `scanner.backbond.ai` traffic — not a sibling server block. Both tenant
tokens are resolved **inside** the privileged subshell from the Step 0 root-only
encrypted custody (`/etc/credstore.encrypted/seq20-tenant-tokens`); the quoted
heredoc never inherits an ambient `TOKEN_*`, and no token is ever echoed.

```sh
sudo sh -eu <<'SH'
. /etc/backbond/sequence-20-production.env
umask 077
# Resolve both tokens from the Step 0 root-only encrypted custody INSIDE this
# privileged subshell (the quoted heredoc never inherits an ambient TOKEN_*).
# Fail closed if the credential is missing or unparseable — never echo a token.
CREDS="$(systemd-creds decrypt /etc/credstore.encrypted/seq20-tenant-tokens - 2>/dev/null)" \
  || { echo 'seq20-tenant-tokens not staged — run Step 0 first' >&2; exit 3; }
TOKEN_A="$(printf '%s\n' "$CREDS" | awk '$1=="tenant_a"{print $3; exit}')"
TOKEN_B="$(printf '%s\n' "$CREDS" | awk '$1=="tenant_b"{print $3; exit}')"
unset CREDS
: "${TOKEN_A:?tenant_a token absent in credstore — re-run Step 0}"
: "${TOKEN_B:?tenant_b token absent in credstore — re-run Step 0}"
[ "$TOKEN_A" != "$TOKEN_B" ]   # two DISTINCT tenants; fail closed if the store lied
WORK="$(mktemp -d)"
trap 'rm -f "$WORK/headers" "$WORK/body"; rmdir "$WORK"' EXIT

# (a) default-deny path over the REAL vhost: opaque 404 + X-Request-ID + no leak.
CODE="$(curl --silent --show-error --max-time 30 \
  --resolve scanner.backbond.ai:443:127.0.0.1 \
  --header "Authorization: Bearer $TOKEN_A" \
  --header "X-Tenant-Id: $TEST_TENANT_ID" \
  --dump-header "$WORK/headers" --output "$WORK/body" --write-out '%{http_code}' \
  "https://scanner.backbond.ai/scanner/v1/sla")"
[ "$CODE" = 404 ]
grep -Eiq '^x-request-id:[[:space:]]*[0-9A-Za-z._-]{8,128}' "$WORK/headers"
REQUEST_ID="$(sed -n 's/^[Xx]-[Rr]equest-[Ii][Dd]:[[:space:]]*\([^[:space:]\r]*\).*/\1/p' "$WORK/headers" | head -1)"
# default-deny body is opaque and echoes the same id (proves OUR location served it).
grep -Fq "\"request_id\":\"$REQUEST_ID\"" "$WORK/body"
grep -Fq '"error":"not_found"' "$WORK/body"
# redaction preserved.
SERVER_HEADER="$(sed -n 's/^[Ss]erver:[[:space:]]*\([^[:space:]\r]*\).*/\1/p' "$WORK/headers" | head -1)"
[ -z "$SERVER_HEADER" ] || [ "$SERVER_HEADER" = nginx ]
! grep -Eiq '^(Via|X-Powered-By|X-Runtime|X-AspNet-Version|Location):' "$WORK/headers"
# exact correlation: the id appears verbatim in exactly one edge access-log line.
MATCHES="$(grep -Fc -- "$REQUEST_ID" "$EDGE_ACCESS_LOG" || true)"
[ "$MATCHES" -eq 1 ]

# (b) Tenant A scoped route reaches upstream (no body assertion — v3 fix retained).
CODE_A="$(curl --silent --show-error --max-time 30 \
  --resolve scanner.backbond.ai:443:127.0.0.1 \
  --header "Authorization: Bearer $TOKEN_A" \
  --header "X-Tenant-Id: $TEST_TENANT_ID" \
  --output /dev/null --write-out '%{http_code}' \
  "https://scanner.backbond.ai/scan/seq20-preflight-unknown")"
printf '%s\n' "$CODE_A" | grep -Eq '^(200|404)$'

# (c) Tenant B parity — proves the SECOND distinct credential is active (Step 0).
CODE_B="$(curl --silent --show-error --max-time 30 \
  --resolve scanner.backbond.ai:443:127.0.0.1 \
  --header "Authorization: Bearer $TOKEN_B" \
  --header "X-Tenant-Id: $TEST_TENANT_ID_B" \
  --output /dev/null --write-out '%{http_code}' \
  "https://scanner.backbond.ai/scan/seq20-preflight-unknown")"
printf '%s\n' "$CODE_B" | grep -Eq '^(200|404)$'

printf 'PASS deny_status=404 request_id=%s edge_log_matches=1 tenant_a_status=%s tenant_b_status=%s\n' \
  "$REQUEST_ID" "$CODE_A" "$CODE_B"
SH
```

Pass: the default-deny path over real `scanner.backbond.ai` returns an opaque
`404` whose body `request_id` equals the response `X-Request-ID` (only OUR
`location /` emits that body, so the integration is provably on-path); `Server`
is absent or bare `nginx`; no upstream topology header is present; exactly one
edge access-log line carries the id; and **both** Tenant A and Tenant B scoped
routes are proxied upstream (status asserted, body not — proving two distinct
active credentials from Step 0). No scan is created. `--resolve` pins the
request to the loopback so the preflight never depends on external DNS.

## 6. Promotion gates

| Gate | Pass criterion | Failure action |
|---|---|---|
| Single listener | `nginx -t` succeeds; the live vhost has exactly one server block / listener set; no second scanner server block exists | rollback |
| Image identity | running container `backbond-scanner-live-edge` image ID = `sha256:7c4ff251…`, revision label `37192e2b…`, env binds that same ID (read-back, not a GHCR digest) | rollback |
| Tenant credentials | key-store `0600`; one active non-`vapi` record each for Tenant A and Tenant B; both scoped routes reachable; no token logged | rollback / re-provision (Step 0) |
| Integration on-path | real `scanner.backbond.ai` default-deny 404 body `request_id` equals response `X-Request-ID` | rollback |
| Redaction | `Server` absent or bare `nginx`; no version/upstream topology header | rollback |
| Request ID | header present; exactly one edge access-log line carries the identical id | rollback |
| Manifest binding | manifest re-signed, binds this v1.6 runbook SHA-256 + both fragment SHA-256, `promotion_authorized=true` | do not promote |
| Rollback proof | backup holds a non-symlink copy of the prior vhost + recorded symlink target + retained image ID + revision label | do not start |

No scan, customer mutation, credential rotation, or tenant creation is authorized
beyond the fail-closed Tenant A/B provisioning in Step 0.

## 7. Immediate rollback on any failed check

Restore the **exact** prior sites-available file and sites-enabled symlink, and
the retained image. Run all commands; do not attempt in-place repair.

```sh
sudo sh -eu <<'SH'
. /etc/backbond/sequence-20-production.env
BACKUP="$(cat /etc/backbond/sequence-20-active-backup)"
LIVE_VHOST="$(cat "$BACKUP/sites-available-path.txt")"
LIVE_VHOST_LINK="$(cat "$BACKUP/sites-enabled-path.txt")"
TARGET="$(cat "$BACKUP/sites-enabled-target.txt")"

test -f "$BACKUP/scanner.backbond.ai"
test ! -L "$BACKUP/scanner.backbond.ai"

# restore exact vhost file.
install -o root -g root -m 0644 "$BACKUP/scanner.backbond.ai" "$LIVE_VHOST"
cmp -s "$BACKUP/scanner.backbond.ai" "$LIVE_VHOST"

# restore exact sites-enabled symlink.
ln -sfn "$TARGET" "$LIVE_VHOST_LINK"
test -L "$LIVE_VHOST_LINK"
[ "$(readlink "$LIVE_VHOST_LINK")" = "$TARGET" ]

# the added http-scope fragment is removed so no dangling edge_json reference.
rm -f /etc/nginx/conf.d/backbond-scanner-edge.http.conf /etc/nginx/backbond-scanner-edge.server.conf

nginx -t
systemctl reload nginx
systemctl is-active --quiet nginx

# Restore the retained app image by IMAGE ID + revision label (never a GHCR
# digest or remembered tag — the offline-loaded image has no RepoDigest).
docker image inspect "$ROLLBACK_IMAGE_ID" >/dev/null
[ "$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$ROLLBACK_IMAGE_ID")" = "$ROLLBACK_OCI_REVISION" ]
sed -i "s#^SCANNER_EDGE_IMAGE=.*#SCANNER_EDGE_IMAGE=$ROLLBACK_IMAGE_ID#" "$SCANNER_EDGE_ENV"
systemctl restart "$SCANNER_SERVICE"
systemctl is-active --quiet "$SCANNER_SERVICE"
[ "$(docker inspect --format '{{index .Config.Image}}' "$SCANNER_CONTAINER")" = "$ROLLBACK_IMAGE_ID" ]
SH

curl --silent --show-error --max-time 30 \
  --resolve scanner.backbond.ai:443:127.0.0.1 \
  --output /dev/null --write-out '%{http_code}\n' \
  https://scanner.backbond.ai/scanner/v1/sla | grep -Eq '^(404|200)$'
sudo nginx -t
sudo systemctl is-active --quiet nginx backbond-scanner-live.service
```

Rollback passes only when the prior vhost file `cmp`s equal to the live file, the
sites-enabled symlink points at the recorded target, nginx validates and reloads,
and the container is back on image ID `sha256:7c4ff251…` (revision `37192e2b…`)
with `/etc/backbond/scanner-edge.env` binding that same ID. If rollback itself
fails, page the Founder/operator immediately and keep design-partner access closed.

## Redacted completion receipt

Attach only: operator role, UTC window, runbook commit, the vhost path, backup
path, the vhost include diff (config only, no secrets), `nginx -t` outcome,
image identity, integration-on-path proof (status + id match), preflight status,
opaque request ID, exact log-match count, and final `PASS` or `ROLLED BACK`.
Never attach bearer tokens, secret values, environment-file contents, proprietary
prompts, or request bodies.

## Self-audit

1. One thing: executable, reversible edge promotion integrated into the ONE live
   `scanner.backbond.ai` vhost — never a parallel listener.
2. Audience/action: Founder-designated operator runs it on `srv1749248` only
   after all three gates exist.
3. Supreme Law: evidence completeness, pinned identity, fail-closed behavior, and
   exact reversibility preserved.
4. No score is reported; score-output version fields are not applicable.
5. No scoring/corpus/rubric change; N=5 calibration is not applicable.
6. Rollback restores the exact prior sites-available file, its sites-enabled
   symlink, and the retained image, then rechecks health.
7. No LLM judge is introduced.
8. Canonical terminology check passed (BackBond Scanner; insure).
9. Carrier Rule check passed; carriers are not discussed.
10. Likely critique addressed: the deployable surface cannot create a second
    listener (structurally rejected outside a server block), and no signature or
    digest is guessed — the signed manifest binding these fragments is mandatory.
