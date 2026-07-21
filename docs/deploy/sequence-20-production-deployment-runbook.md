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
  - "Retained rollback image: backbond-scanner-live@sha256:7c4ff251dd0cc4d41b88e9e148094355985dc4a39e7ac4e93e280171c4101121"
  - "Deployable edge fragments: infra/edge/nginx/backbond-scanner-edge.http.conf + backbond-scanner-edge.server.conf.template"
  - "Live vhost integrated in place: /etc/nginx/sites-available/scanner.backbond.ai (sites-enabled symlink)"
version: 1.4
prior_version: "1.3 (docs/deploy/sequence-20-production-deployment-runbook.md@744c6ad — standalone conf.d server block; topology-incorrect)"
self_audit_passed: true
escalations_noted:
  - "v1.3 assumed a standalone /etc/nginx/conf.d/backbond-scanner-edge.conf server block. The live topology is ONE TLS vhost at /etc/nginx/sites-available/scanner.backbond.ai; installing a second server block would create a conflicting listener. The Sequence 20 operator stop (BEAAA-16232 962f9417) caught this. v1.4 integrates into the live vhost."
  - "The deployable nginx surface changed, so the release manifest must be re-signed to bind these fragment hashes and this runbook commit before promotion. Do not deploy against a manifest that predates the fragments."
  - "Production/public promotion requires exact-build QA APPROVED naming this runbook commit SHA and separate Founder sign-off under BEAAA-15373. v4 Founder confirmation authorized the prior identity only and does NOT carry to this changed deployable surface."
---

# Sequence 20 production deployment runbook (v1.4 — topology-correct)

## Why v1.4 supersedes v1.3

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
CANDIDATE_IMAGE=ghcr.io/corwinfoundation/are-scanner@sha256:0b565dda1bb299287a35abaa7a82e5ce5788ca13f68f278e0007bfcb3d1e6a65
SOURCE_BUNDLE=/var/tmp/are-scanner-<commit>-source.tar.gz
SOURCE_BUNDLE_SHA256=<sha256 recorded in the signed release manifest>
RELEASE_MANIFEST=/var/tmp/sequence-20-release-manifest.json
RELEASE_MANIFEST_SIG=/var/tmp/sequence-20-release-manifest.json.sig
RELEASE_MANIFEST_PUBKEY=/var/tmp/sequence-20-release-manifest.pub.pem
LIVE_VHOST=/etc/nginx/sites-available/scanner.backbond.ai
LIVE_VHOST_LINK=/etc/nginx/sites-enabled/scanner.backbond.ai
EDGE_UPSTREAM=<the app-tier host:port THIS vhost already proxies to>
EDGE_ACCESS_LOG=/var/log/nginx/scanner-backbond-edge.access.log
BACKBOND_ACCEPTANCE_BASE_URL=https://scanner.backbond.ai
TEST_TENANT_ID=<provisioned non-customer, non-vapi tenant UUID>
TOKEN_A_REF=file:/etc/credstore.encrypted#<existing-non-customer-token-a>
```

`EDGE_UPSTREAM` is **not** invented: it is read from the live vhost during backup
(step 1) and must match what the vhost proxies to today (the change preserves the
existing upstream). `CANDIDATE_IMAGE` and `SOURCE_BUNDLE_SHA256` must come
verbatim from the signed manifest. Verify the manifest and source bundle on an
egress-capable operator machine before any box-side step:

```sh
openssl dgst -sha256 \
  -verify artifacts/beaaa-16257/sequence-20-release-manifest.pub.pem \
  -signature artifacts/beaaa-16257/sequence-20-release-manifest.json.sig \
  artifacts/beaaa-16257/sequence-20-release-manifest.json
# manifest must bind THIS runbook commit and both edge-fragment SHA-256 values;
# if it does not, stop — the manifest predates the topology fix.
```

Load and validate inputs without printing the token:

```sh
sudo sh -eu <<'SH'
. /etc/backbond/sequence-20-production.env
printf '%s\n' "$CANDIDATE_IMAGE" | grep -Eq '^ghcr\.io/corwinfoundation/are-scanner@sha256:[0-9a-f]{64}$'
[ "${CANDIDATE_IMAGE##*@}" = 'sha256:0b565dda1bb299287a35abaa7a82e5ce5788ca13f68f278e0007bfcb3d1e6a65' ]
printf '%s\n' "$BACKBOND_ACCEPTANCE_BASE_URL" | grep -Eq '^https://scanner\.backbond\.ai/?$'
printf '%s\n' "$TOKEN_A_REF" | grep -Eq '^(credstore|file|env|vault):'
test -f "$SOURCE_BUNDLE" -a -f "$RELEASE_MANIFEST" -a -f "$RELEASE_MANIFEST_SIG" -a -f "$RELEASE_MANIFEST_PUBKEY"
openssl dgst -sha256 -verify "$RELEASE_MANIFEST_PUBKEY" -signature "$RELEASE_MANIFEST_SIG" "$RELEASE_MANIFEST"
printf '%s  %s\n' "$SOURCE_BUNDLE_SHA256" "$SOURCE_BUNDLE" | sha256sum -c -
[ "$(jq -r .commit_sha "$RELEASE_MANIFEST")" = "$CANDIDATE_COMMIT" ]
[ "$(jq -r .repository_qualified_image "$RELEASE_MANIFEST")" = "$CANDIDATE_IMAGE" ]
[ "$(jq -r .source_bundle_sha256 "$RELEASE_MANIFEST")" = "$SOURCE_BUNDLE_SHA256" ]
[ -n "$TEST_TENANT_ID" ] && [ "$TEST_TENANT_ID" != vapi ]
test -L "$LIVE_VHOST_LINK"
test -f "$LIVE_VHOST"
SH
```

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

# Retained image identity for rollback.
docker inspect --format '{{index .Config.Image}}' backbond-scanner-live-edge \
  > "$BACKUP/prior-image.txt" 2>/dev/null || true
printf '%s\n' 'backbond-scanner-live@sha256:7c4ff251dd0cc4d41b88e9e148094355985dc4a39e7ac4e93e280171c4101121' \
  > "$BACKUP/required-rollback-image.txt"
docker image inspect 'backbond-scanner-live@sha256:7c4ff251dd0cc4d41b88e9e148094355985dc4a39e7ac4e93e280171c4101121' >/dev/null

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

Now edit the live vhost **in place** to pull in the include, replacing the
existing scanner proxy location(s). Do this on a staged copy, prove it is a
single-listener edit, then atomically move it into place:

```sh
sudo sh -eu <<'SH'
. /etc/backbond/sequence-20-production.env
STAGE="$(mktemp /etc/nginx/.scanner-vhost.XXXXXX)"
cp -aL "$LIVE_VHOST" "$STAGE"

# Replace the vhost's scanner proxy location block(s) with the single include.
# The operator makes this edit deliberately: delete the existing
#   location /scanner/v1/ { ... }  and any  location ~ ^/scan/  and  location /
# blocks that proxy to the app tier, and insert exactly one line INSIDE the
# server block, after the ssl_certificate/server_name directives:
#     include /etc/nginx/backbond-scanner-edge.server.conf;
$EDITOR "$STAGE"   # or an idempotent, reviewed sed/patch; commit the diff to the receipt

# Fail-closed structural checks on the staged vhost:
#  - still exactly ONE server block and ONE listen directive set (no new listener);
#  - the include line is present; no duplicate default-deny locations remain.
[ "$(grep -Ec '^[[:space:]]*server[[:space:]]*\{' "$STAGE")" = 1 ]
grep -Fq 'include /etc/nginx/backbond-scanner-edge.server.conf;' "$STAGE"

install -o root -g root -m 0644 "$STAGE" "$LIVE_VHOST"
rm -f "$STAGE"
# sites-enabled symlink is unchanged and still points at the same target.
test -L "$LIVE_VHOST_LINK"
[ "$(readlink "$LIVE_VHOST_LINK")" = "$(cat "$(cat /etc/backbond/sequence-20-active-backup)/sites-enabled-target.txt")" ]
SH
```

Pass: the staged vhost has exactly one server block and one listener set, carries
the include line, installs cleanly, and the sites-enabled symlink target is
unchanged. Any deviation stops the run before `nginx -t`.

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

## 4. Confirm the scanner image identity (unchanged)

The scanner app image is unchanged by this edge fix. Confirm the digest-pinned
image already running matches the signed manifest; do not re-pull.

```sh
sudo sh -eu <<'SH'
. /etc/backbond/sequence-20-production.env
docker image inspect "$CANDIDATE_IMAGE" >/dev/null
test "$(docker inspect --format '{{index .Config.Image}}' backbond-scanner-live-edge)" = "$CANDIDATE_IMAGE"
systemctl is-active --quiet backbond-scanner-live.service
SH
```

Pass: the running edge container is the exact repository-qualified image
`@sha256:0b565dda…a65`. A mismatch is a stop condition (image identity is out of
scope for this edge-only change).

## 5. Preflight: real scanner.backbond.ai request selects the integration

Prove the request-ID/redaction/default-deny configuration is actually on the path
for real `scanner.backbond.ai` traffic — not a sibling server block. Resolve
`TOKEN_A_REF` in the root-only channel into `TOKEN_A` without echoing it.

```sh
sudo sh -eu <<'SH'
. /etc/backbond/sequence-20-production.env
: "${TOKEN_A:?resolve TOKEN_A_REF in the root-only channel before running}"
umask 077
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

# (b) tenant-scoped route reaches upstream (no body assertion — v3 fix retained).
CODE2="$(curl --silent --show-error --max-time 30 \
  --resolve scanner.backbond.ai:443:127.0.0.1 \
  --header "Authorization: Bearer $TOKEN_A" \
  --header "X-Tenant-Id: $TEST_TENANT_ID" \
  --output /dev/null --write-out '%{http_code}' \
  "https://scanner.backbond.ai/scan/seq20-preflight-unknown")"
printf '%s\n' "$CODE2" | grep -Eq '^(200|404)$'

printf 'PASS deny_status=404 request_id=%s edge_log_matches=1 tenant_route_status=%s\n' \
  "$REQUEST_ID" "$CODE2"
SH
```

Pass: the default-deny path over real `scanner.backbond.ai` returns an opaque
`404` whose body `request_id` equals the response `X-Request-ID` (only OUR
`location /` emits that body, so the integration is provably on-path); `Server`
is absent or bare `nginx`; no upstream topology header is present; exactly one
edge access-log line carries the id; and the tenant-scoped route is proxied
upstream (status asserted, body not). No scan is created. `--resolve` pins the
request to the loopback so the preflight never depends on external DNS.

## 6. Promotion gates

| Gate | Pass criterion | Failure action |
|---|---|---|
| Single listener | `nginx -t` succeeds; the live vhost has exactly one server block / listener set; no second scanner server block exists | rollback |
| Image identity | running edge image is `@sha256:0b565dda…a65` from the signed manifest | rollback |
| Integration on-path | real `scanner.backbond.ai` default-deny 404 body `request_id` equals response `X-Request-ID` | rollback |
| Redaction | `Server` absent or bare `nginx`; no version/upstream topology header | rollback |
| Request ID | header present; exactly one edge access-log line carries the identical id | rollback |
| Tenant preflight | tenant-scoped route returns 200/404 via the vhost; body not asserted; token never logged | rollback / incident review if leaked |
| Rollback proof | backup holds a non-symlink copy of the prior vhost + recorded symlink target + retained image | do not start |

No scan, customer mutation, credential rotation, or tenant creation is authorized.

## 7. Immediate rollback on any failed check

Restore the **exact** prior sites-available file and sites-enabled symlink, and
the retained image. Run all commands; do not attempt in-place repair.

```sh
sudo sh -eu <<'SH'
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

ROLLBACK_IMAGE='backbond-scanner-live@sha256:7c4ff251dd0cc4d41b88e9e148094355985dc4a39e7ac4e93e280171c4101121'
docker image inspect "$ROLLBACK_IMAGE" >/dev/null
sed -i "s#^SCANNER_EDGE_IMAGE=.*#SCANNER_EDGE_IMAGE=$ROLLBACK_IMAGE#" /etc/backbond/scanner-edge.env
systemctl restart backbond-scanner-live.service
systemctl is-active --quiet backbond-scanner-live.service
test "$(docker inspect --format '{{index .Config.Image}}' backbond-scanner-live-edge)" = "$ROLLBACK_IMAGE"
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
and the exact retained rollback image is running. If rollback itself fails, page
the Founder/operator immediately and keep design-partner access closed.

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
